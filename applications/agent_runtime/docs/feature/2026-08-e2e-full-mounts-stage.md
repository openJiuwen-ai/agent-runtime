# e2e 全量真实规格阶段(--with-mounts):三挂载/PVC 预置/逐字段断言

- 日期:2026-08-28
- 里程碑 / commit:M0–M8 维护期(测试增强)
- 涉及模块:测试(e2e 冒烟)/ 文档

## 背景与动机

一条接近真实的 config_sync 请求(真镜像 agentserver/sandbox 0.0.6s、真 SSE/健康契约
8086 + `/api/v1/health` + `/api/v1/events/stream`、真 agent_env 注入、主容器
cm/hp/pvc 三挂载、sidecar jiuwenbox 完整规格含 PVC)与当时 e2e 现状对照,缺口:

| 真实请求有 | 当时 e2e 现状 |
|---|---|
| 主容器三种挂载 | 所有 e2e 脚本零下发 |
| sidecar pvc_mounts | 零覆盖(PVC 只有手工验证记录,无自动化预置) |
| container_port/template_name/pod_name 显式 | 从未显式下发 |
| 特权四件套/卷/挂载/探针逐字段断言 | 只验"Pod 能跑" |
| PVC 前置资源 | 无任何 setup(nfs-provisioner 不供给,只能手工) |

「所有真实场景必须有 e2e 对应用例」硬标准 + 2026-08-28 双真镜像手工全量验证已通过
(见 [2026-08-sidecar-containers.md](2026-08-sidecar-containers.md))——本次把它自动化沉淀进冒烟。

**与需求方确认过的决策**:新阶段 flag 门控、默认关(与 `--with-sidecar` 同构,写进
CLAUDE.md 发布门禁命令)——它需要 kubectl 有 PV 创建权限、节点接受 apparmor annotation,
默认开会改变所有人的日常替身冒烟行为。

## 方案

- 新增 `--with-mounts`(env `AGENT_RUNTIME_E2E_WITH_MOUNTS=1`):
  - **阶段 0m 资源预置**(5 项):2 ConfigMap(幂等 create,已存在则复用)+ 2 PVC
    「**已 Bound 即复用,缺失才静态供给**」+ Bound 等待(卡 Released 时 patch
    claimRef 兜底)。
  - **阶段 2c 全量规格断言**(19 项,`--agent-env` 时 +1):tpl-mnt 按真实请求逐字段
    复刻,min_idle=1(下发即预热,暖 Pod 复用零冷启动)→ route → Pod spec 逐字段断言
    (container_port/挂载 subPath/readOnly/探针/特权三件套/apparmor annotation/卷全景
    按内容断言)→ 容器内 exec 实证(CM 内容=CM 当前值、hostPath DirectoryOrCreate
    存在且只读拒写、双 PVC 写回读)。
- 跨 scope 计数修正:阶段 4/5 各 `+(1 if WITH_MOUNTS else 0)`;**阶段 12 清空断言语义
  修正**——牙齿从「ns 恒零 Pod」改为「cleanup 前存量 Pod 全部经 NotFound 路径收敛」,
  因为 min_idle scope 的 autoscale 重建热备与恒零断言自相矛盾(--with-mounts 双
  min_idle scope 同 watch tick 一起重建,旧断言必闪红)。
- 被否方案:tpl-mnt 的挂载进 `template()` base——会波及全部模板 deploy_ver → 全量
  A 类日落,stage3 直接 409;sidecar 复用 `_sidecar_standin()`——其 ConfigMap 指向
  `--with-sidecar` 门控的 `e2e-box-cm`,单独开 `--with-mounts` 时 CM 缺失 →
  CreateContainerConfigError 永不 Ready;scope 用真实请求的空串通配——会打掉阶段 13
  的 CONFIG_NOT_FOUND 验收。

## 实现

- `scripts/e2e_lib.py`:`kubectl()` 加 keyword-only `stdin`(PV/PVC 清单 `apply -f -`)。
- `scripts/e2e_hld_acceptance.py`:全局 `MNT/WITH_MOUNTS`;`_jiuwenbox_spec()`(特权
  四件套替身/真镜像两模式都带——渲染路径不依赖镜像);`_schedulable_node()`(Ready 且
  避 NoSchedule 污点 master,PV 亲和不可变);`_pv_pvc_yaml()`/`_ensure_pvc()`/
  `stage0_provision_mounts()`/`stage2c_mounts()`;计数与阶段 12 语义修正;`--with-mounts`
  参数与 main 接线(预置失败在 FLUSHDB/删 Pod 等破坏性动作之前中止,退出码 2)。
- 不改任何服务端 src(全部字段已核实真实消费、非占位)。

## 验证

真环境(8091 单实例 server 模式,PG 后端;Redis 30001/1;双节点 K8s):

- 默认冒烟(不带新 flag):74/75——唯一 FAIL 为 DB 落库校验环境错配(8091 实例是
  PostgreSQL 后端,脚本默认查 MySQL;与本改动无关,带 `--db-type postgresql
  --db-port 30025` 即过),门控零回归。
- `--with-mounts` 替身(influxdb 双容器+挂载+特权四件套):**99/99 PASS**;阶段 4/5
  计数(+1)与阶段 12 新语义实测通过。
- 双真镜像发布门禁(agentserver:0.0.6s + sandbox:0.0.6s + 契约三件套 +
  `--with-sidecar --with-mounts`,AGENT_HTTP_PORT=8086 与 sse_port 对齐):**104/105**,
  唯一 FAIL 即下述真实缺陷②;agent_env 逐项注入可见,`/api/v1/health`:8086 readiness
  真契约通过。

**抓到两个真实场景问题**(本次改造的核心收益):

1. **同名 PVC 撞环境预置**(已修):ns 里已有真实实验留下的 `agent-data-pvc`→
   `pv-agent-data` 手工对,静态供给清单盲目 apply 触碰不可变 `volumeName` → Invalid。
   修法:「已 Bound 即复用,缺失才供给」——也更贴近生产(PVC 由谁供给本就不归模板管);
   CM 内容断言同步改为与 CM 当前值比对(预置/复用通吃)。
2. **真镜像非 root 写不进 root 属主 PVC**(OPEN,待产品决策):agentserver 0.0.6s 以
   uid=1000(app) 运行,PVC 后端目录 root:root 0755 → `/var/lib/agent` 写入 Permission
   denied(替身 influxdb 以 root 跑、sidecar 特权,均掩盖此问题;`id`/`ls -ld` 取证在
   卷与节点两侧)。当前 Template schema 无 pod 级 fsGroup、主容器不渲染
   securityContext(sidecar 才有 run_as_user)。修法候选:模板加 fsGroup / 主容器
   securityContext / initContainer chown / PV 目录预属主——修前门禁该项保持红。

   **老体系做法考证(2026-08-28,外层 swarm 仓库 + 6s 部署脚本)**:老镜像同样非
   root(`useradd app`→uid 1000,`USER app`,构建期 `chown -R app:app
   /home/app/.jiuwenclaw` 并 `jiuwenclaw-init` 烘焙工作区);老 SDK
   (`openjiuwen_runtime.management.session.k8s_service_handler`)product 模式下
   Pod/容器 securityContext 全空(`run_as_user=None`、无 fsGroup),其真正解法在
   **部署脚本层**:NFS export `no_root_squash` + `nfs_handler.sh` 对后端宿主目录
   `chmod -R 777 /data/nfs` + `check_handler.sh` 在 gateway 启动前 `kubectl exec`
   进 NFS Pod 对 `${NFS_POD_PATH}/jiuwenclaw` 显式 `chown 1000:1000 && chmod 777`
   ——即**存储后端预属主化(运维手段),Pod spec 层不解决**;`fs_group=0` +
   `run_as_user=0` 仅 dev 模式兜底。老 PVC 为 nfs-provisioner 动态供给 RWX,
   本环境不供给(feature 记录),静态 hostPath PV root 属主 → 问题在新 e2e 才暴露。

   **后续决策(2026-08-28 同日)**:`fs_group` 模板字段方案曾实现并真环境验证——
   渲染链路正常(Pod `spec.securityContext.fsGroup=1000` 就位、单测/断言全过),
   但 **fsGroup 的属主管理由 volume plugin 决定,hostPath(含经 PVC 间接引用)与
   NFS in-tree plugin 均不做**(容器内卷仍 root:root 0755,`ls -ld` 实锤)——即
   Pod spec 层在本环境/老体系 NFS 形态下都解决不了写权限。按需求方决定**整体
   回退暂缓**;本环境现阶段解法回归「存储侧预属主」运维手段(同老体系 6s 的
   chown 1000:1000)。若未来生产存储为 CSI/块存储(FSGroupPolicy 支持),可重新
   引入 fs_group 方案。

## 影响面

- 文档同步:`docs/spec/e2e-test-cases.md`(§1 总览计数、§3.0 模板矩阵 +tpl-mnt、
  阶段 0m/2c 新小节、阶段 4/5/12 注记与语义、§8.3 缺口清单)、`CLAUDE.md` 发布门禁
  命令升级为全量规格版(AGENT_HTTP_PORT 8086 与真实请求对齐,替换旧 8080 示例)。
- 遗留开放问题:真实缺陷②(见上);manager 发送方 `routing_rules` 仍发结构化 list 而
  接收端只收表达式字符串(2026-08-routing-rules-expression-string.md 的 wire 变更未
  同步到 manager,疑似必 400)——独立后续任务;sidecar 资源限额/run_as_user、
  readiness http 探针、nfs_* 挂载、多 sidecar 仍未覆盖(§8.3)。
