# 2026-08 容器表拆分 + config_sync 三段式契约(K8s 原生形态)

> commit: `<本提交 hash>`
> 关联规格:`docs/spec/session-manager.md` §config_store/§container_spec;HLD §3.1(语义权威)。

## 动机

`service_config_template` 表中,主容器配置(约 22 个 `agent_*`/裸列)与 sidecar 配置(`sidecars` JSON 列内 24 键)语义重复、命名三套、默认值有出入、校验/渲染逻辑双份。且 env 只支持 `dict[str,str]` 字面量,密钥明文落 DB/快照/`pod_spec_json` 缓存。

## 改动定案(与需求方确认的决策)

1. **新增表 `service_config_container`**(15 列;框架 init_table 自动建):容器规格系统记录,主/sidecar 同表,角色由模板引用位置决定。模板表只持 `main_container_id` + `sidecar_container_ids` 引用与 Pod 级 `volumes`。
2. **config_sync 契约改三段式** `{containers, templates, scopes}`;**过渡期双收**(无 containers 键 → legacy 内联路径逐字节保留;mixed 形态 → 400)。
3. **wire 对齐 K8s 原生**:容器字段用 K8s API 同名 camelCase(`imagePullPolicy`/`containerPort`/`mountPath`/`periodSeconds`),嵌套结构(`resources`/`readinessProbe`/`securityContext`/`ports`/`env: [{name,value}]`);业务键(`container_id`/模板级策略字段)保持本仓 snake_case;DB 列一律 snake,段落以 JSON 列存内部规范形。卷采用 **模板级 `volumes` + 容器级 `volumeMounts` 分离**(K8s spec.volumes 同构)。
4. **新增 envFrom(secretRef/configMapRef)支持**:`env`/`envFrom` 全量能力(K8s EnvFromSource 完整形态:prefix/optional);密钥以引用名下发,值不再落模板/快照/pod_spec。
5. **存量迁移 = 读兼容回退 + config_sync 重放**:行有真值 `main_container_id` → 新形态水合(任一引用容器行缺失 → WARNING + 整模板跳过);否则 legacy 列路径。无迁移脚本。
6. **指纹零扰动(红线)**:水合后仍是扁平 `Template`(快照/RM 契约不变)——同值必同 deploy_ver;`agent_env_from` 缺省 None 被 fingerprint 滤除;sidecar 规范形 `env_from` 为**条件键**(None/[] 省略,区别于其他显式存 None 的键)。承重断言:`test_split_contract_deploy_ver_identical_to_inline`(新旧契约同值 deploy_ver + 快照 JSON 逐字节相等)、`test_env_from_absent_keeps_deploy_ver_byte_identical`(2026-08-31 改动前实测指纹常量锚定)、`test_volume_join_fused_mounts_byte_identical`。

## 新 400 收紧面(Manager 适配方须知)

- 主容器 `image` 必填非空、`name` 必须 DNS-1123(此前无校验)。
- 主容器 ports 必须含 `name="sse"`;`env[].value` 必须 str(此前 agent_env 接受标量);env name 重复 → 400。
- **未被任何模板引用的容器 / 同 id 双角色引用 → 400**(全量语义下 = 配置错误)。
- **未被任何容器挂载的卷 / 悬挂 volumeMount 引用 / 卷多源 / 卷名重复 → 400**;`subPath` 仅 configMap 卷挂载;NFS 卷仅主容器至多一个、不支持 `readOnly:true`。
- **不可表示即拒绝**:`command`/`args`/端口 `protocol`/主容器 `readinessProbe.tcpSocket`·`timeoutSeconds`/`seccompProfile`·`appArmorProfile` type=`Localhost`/envFrom 双 ref 等 → 400,绝不静默丢弃。
- **mixed 形态**:模板同时携带引用键与 legacy 内联容器键 → 400;payload 有 containers 段但模板无 `main_container_id`(或反之)→ 400。
- 响应体新增 `containers_synced`/`containers_deleted` 计数字段(可断言)。
- A 类热更新的字段归属变化:`readiness_period` 等容器级字段现在改**容器段**(e2e stage10 已改为 `container_overrides`);模板级(策略 B 类)不变。

## 迁移与发布顺序(运维红线)

```sql
-- 存量库先手工 ALTER(框架 init_table 只 create_all 不补列;容器表自动建)
ALTER TABLE service_config_template ADD COLUMN main_container_id VARCHAR(100) NULL;
ALTER TABLE service_config_template ADD COLUMN sidecar_container_ids JSON NULL;
ALTER TABLE service_config_template ADD COLUMN volumes JSON NULL;
```

① ALTER → ② 滚动升级**全部** agent-runtime 副本(期间 Manager 仍发 legacy 载荷,双收保证读写都通)→ ③ Manager 切三段式契约 → ④ 重放一次 config_sync 收敛(容器行落库、模板行转新形态;legacy 重放会反向清空容器行回内联——正确行为)→ ⑤ legacy 容器列成死数据(后续可选手工 DROP)。

**★rolling upgrade 最高风险**:升级未完成时 vOld 副本读新形态行得 `agent_image=""` 坏模板,其启动 `ensure_snapshot()` 会把坏模板 SET 进**共享** `routing:snapshot` 污染全舰队——"全部副本升级后再切新契约"是唯一防线(与 sidecars 上线先例同款)。

## 被否方案

- **扩展顶层 `sidecars.py` 做容器解析**:sidecars.py 是 SM/RM 共享模块且规范形输出被指纹冻结;主容器 wire 解析是 SM 私有关注点,入内会扩大误改冻结面的表面积 → 新建 SM 私有 `session_manager/container_spec.py`。
- **顶层 `container_spec.py`**:顶层目录语义是 SM/RM 共享契约区(spec_fields/sidecars/mounts 先例),RM 不感知容器表 → 放 session_manager 下。
- **新形态双写内联镜像列(旧副本可读)**:双源漂移(旧副本 legacy 写不清引用列),且与"模板只持引用"决策冲突。
- **容器内联融合挂载(仅字段名靠拢 K8s)**:不是 K8s 原生结构,同 PVC 跨容器共享仍靠 RM `pvc_seen` hack → 采用 volumes/volumeMounts 分离。
- **一次性迁移脚本**:读兼容 + 全量重放天然收敛(config_sync 是唯一写入口),脚本沦为额外维护面。

## 验证

- 组件/集成:`uv run pytest` **395 通过**(306 既有零语义迁移 + 21 envFrom + 58 container_spec + 9 config_store 三段式 + 1 双实例三段式);既有 `conftest.seed_template` 保持 legacy 形态,全套件免费覆盖读兼容回退。
- e2e(2026-08-31 真环境实测:本机 server 模式 + 真 Redis(DB1)/MySQL/K8s,`--with-sidecar` 替身):**80/80 PASS**——三段式下发/DB 三表落库/快照/路由/双容器 Pod/M-A 容器覆盖热更新/阶段 11b 快照不变量全绿;`--with-mounts`(PVC/挂载/envFrom 阶段)按指示暂缓,待 PVC 环境修复后按 CLAUDE.md 门禁命令补跑。e2e 抓到并修复一处真实缺陷:`--with-sidecar` 分支漏注册 `c-tpl-box` 主容器 → 全量下发 400(组件测试未覆盖该组合,e2e 价值实证)。
- 运维脚本 `scripts/config_sync_seed.sh`(真实联调载荷切三段式)真环境验证:200 + `containers_synced=2`;DB 模板行新形态(`main_container_id`/`sidecar_container_ids`/`volumes` 落库,`agent_image=""` 死值);容器表 2 行;RM `scope:config` 推送 `deploy_ver`+水合扁平 `pod_spec_json`;真实拉起 `jiuwenclaw-agentserver-*` 0/2 双容器 Pod(`nodeName=arm-master` 生效,5 个挂载点全渲染;Pending 为目标 arm 集群镜像/节点在本 x86 集群不可运行——环境性,契约链路已通)。验证过程另修复一处实现缺陷:模板级 wire 键 `nodeName`(K8s 拼写)未翻译被静默丢弃(节点绑定失效)——已加显式别名映射 + snake 双形态 400 + 回归用例。
