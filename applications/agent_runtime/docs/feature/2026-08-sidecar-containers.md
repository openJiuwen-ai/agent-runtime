# Template 扩展 sidecars 字段 + _build_pod_body 多容器(Pod 内可含 jiuwen box)+ 主/sidecar 卷挂载

- 日期:2026-08-27
- 里程碑 / commit:M0–M8 维护期(新增 feature)
- 涉及模块:session_manager / resource_manager / 测试 / 文档

> 同日增量(与需求方确认):主 agent 容器与 sidecar 容器**都**补齐 hostPath/ConfigMap/PVC
> 三种卷挂载能力——主容器走 Template 顶层 `agent_host_path_mounts`/`agent_configmap_mounts`/
> `agent_pvc_mounts`(沿用 `agent_` 前缀),sidecar 走子 schema `host_path_mounts`/
> `configmap_mounts`/`pvc_mounts`;规范形/校验抽到新顶层共享模块 `mounts.py`(SM fail-fast
> 400 + RM 渲染兜底共用);渲染统一 `_render_volume_mounts`,卷名 `_scoped_volume_name`
> (`hp-`/`cm-`/`pvc-` 前缀 + 容器名净化 + 双索引,≤63 防撞,与 NFS 卷名 `{pod_id}-nfs`
> 天然不撞);ConfigMap 支持 `sub_path`(单 key 挂到文件,老 SDK config.yaml 同款)与
> `items`;同容器 mount_path 重复(含撞 nfs_mount_path)→ 400。三字段进 DEPLOY_FIELDS
> (A 类),空列表/坏值归一 None 保存量指纹不变。DB 加三个 JSON 列(存量库同款先 ALTER
> 后发版)。

## 背景与动机

agent-runtime 服务此前只能拉起单容器 Pod:`Template.deploy_subset()` 下发扁平 pod_spec,`RealK8sPodClient._build_pod_body` 硬编码 `containers=[container]`。而 EE 侧 SDK(management 模块 `K8sServiceHandler`)天然多容器——jiuwenclaw EE 把 agent server 与 jiuwenbox 沙箱放同一 Pod(agent 经 `127.0.0.1:8321` 访问)。要在 agent-runtime 服务上对等承载 jiuwenbox 形态的 AgentServer,必须打通 Template → pod_spec → Pod body 的多容器链路;不改则此类模板无法在本服务上部署。

与需求方确认过的决策:
- **通用 `sidecars` 列表建模**(单 JSON DB 列,每项一个容器规格 dict),jiuwenbox 只是第一个使用者——非 jiuwenbox 专用扁平字段(前者与"支持多容器"的目标贴合,后续任何 sidecar 免改模型);
- **仅改 agent_runtime 模块**,不动 manager/前端(manager→gateway 下发是整行透传,gateway→agent-runtime 的字段翻译层本就在仓外;本期 config_sync 载荷由运维/e2e 下发,manager 侧支持留作后续)。

## 方案

- **指纹兼容(★本改动最关键决策)**:`sidecars` 默认 `None` 而非 `[]`,且 `Template.__post_init__` 经 `normalize_sidecars` 把空列表/坏值统一归一为 `None`。原因:`util.fingerprint` **只滤 None 不过滤空容器**,若以 `[]` 为默认,所有存量模板 deploy_ver 全变 → 全量伪 A 类日落(暖 Pod 清零)。默认 None 时存量模板指纹逐字节不变,滚动升级窗口新旧实例对"无 sidecar 模板"算同一指纹。
- **规范形(canonical form)填满全部默认键 + 列表按 name 升序**:"显式给默认值"与"省略键"、下发顺序重排、DB JSON 列键序重排,三者必须同指纹(2026-08-26 缺陷④——MySQL JSON 键序重排曾使暖 Pod 复用失效——的 sidecar 版防线)。
- **新顶层共享模块 `src/agent_runtime/sidecars.py`**:SM(校验/归一)与 RM(渲染兜底/冲突谓词)共用;RM 不得 import SM,沿 `spec_fields.py` 的顶层共享先例。宽容路径 `normalize_sidecars`(读路径防御,坏项静默丢弃)与严格路径 `validate_sidecars`(config_sync fail-fast 400)共用 `_canonical_sidecar`,保证产物逐字节一致。
- **sidecars 进 `DEPLOY_FIELDS`(A 类)**:sidecar 镜像/env/特权/挂载全部烘焙进运行中的 Pod,变更不日落会导致同 scope 新旧 Pod 行为不一致(有的 agent 连得上 box、有的连不上),危害大于多日落一次。
- **未知键 400 拒绝**(与模板级 `_COLUMN_OF` 白名单静默丢弃不同):sidecar 是安全敏感面,`capabilites_add` 这类拼写错误被吞 = "看似配置了特权实际没有"的运行期疑难。代价是牺牲"下发方先行携带新子字段"的前向兼容——可翻转决策,翻转变点在 `_canonical_sidecar` 单处。
- **sidecar 端口纯声明性无名**(`V1ContainerPort(container_port=...)` 不带 name):主容器端口名 sse/http 在 Pod 内必须唯一,sidecar 只被同 Pod 127.0.0.1 访问、不进 Service,无名彻底消灭端口名撞号一类 bug。
- **RM 侧冲突 fail-fast**:pod_spec 可能来自 Redis `pod_spec_json` 缓存(旧版本写入/手改),`_build_pod_body` 入口 normalize 兜底坏项,但撞主容器名/撞 sse_port·container_port·兄弟 sidecar 端口直接 `DeployFailed`(防 Pod 建出来 agent 经 127.0.0.1 连错进程)。同 Pod 共享网络命名空间,撞号几乎必然是配错——有意的严格。

被否掉的备选:
- **默认 `[]`(default_factory=list)**:全部存量模板指纹变化 → 全量 A 类日落,否决(见上);
- **塞进 Template.data 列**(EE 兼容透传字段):`data` 不在 `_COLUMN_OF` deploy 子集、不入指纹、无校验,等于绕开 A 类语义,否决;
- **只在 k8s 层校验、SM 不 fail-fast**:错误配置要到 deploy 时才爆(且 autoscale 后台路径无人工在场),不如 config_sync 锁外校验 400 直达下发方,否决;
- **jiuwenbox 专用扁平字段**(仿 manager 的 jiuwenbox_cpu_request 列风格):字段爆炸且锁死单一 sidecar,与"支持多容器"目标不符,否决(需求方二选一确认)。

## 实现

五个注册点(新增 deploy 字段的完整链路):

| # | 文件 | 改动 |
|---|---|---|
| ① | `session_manager/config_store.py` | `SERVICE_CONFIG_TEMPLATE_TABLE_DEF` 加 `ColumnDefinition("sidecars", "json", nullable=True)`(agent_env 同款先例) |
| ② | 同上 | `_COLUMN_OF` 加 `"sidecars": "sidecars"` 直映射 |
| ③ | `session_manager/models.py` | `Template.sidecars: list[dict] | None = None` + `__post_init__` 归一(frozen dataclass 用 `object.__setattr__`) |
| ④ | `spec_fields.py` | `DEPLOY_FIELDS` 追加 `"sidecars"`(自动进 DEPLOY_VER_FIELDS/deploy_subset;RM `_deploy_ver` 同字段集零改动) |
| ⑤ | `resource_manager/k8s.py` | `_build_pod_body` 多容器:`normalize_sidecars` 兜底 + `find_sidecar_conflict` → DeployFailed + `_build_sidecar_container`(V1Container/security_context/apparmor→Pod annotation/tcp·http 探针/独立 resources)+ `_host_path_volume_name`(`hp-{name净化}-{容器idx}-{挂载idx}` ≤63,沿老 SDK 约定);`annotations or None`/`[container, *sidecars]` 展开保证无 sidecars 时与历史逐字节一致 |

- 新模块 `src/agent_runtime/sidecars.py`:schema 常量(DNS-1123 名、≤8 条、host_path_type 7 枚举、探针 tcp/http)+ `_canonical_sidecar`(逐键校验,消息带 `sidecars[i]` 定位,风格对齐 agent_env 校验)+ `normalize_sidecars`(宽容)/`validate_sidecars`(严格)/`find_sidecar_conflict`(纯谓词,SM 包 InvalidParams、RM 包 DeployFailed 共用)。
- `config_store.template_from_payload` 循环后调 `validate_sidecars`(锁外校验路径,400);`template_from_row` 无需兜底代码(`__post_init__` 单点收敛),防御注释已补。
- `FakeK8sPodClient.deployed_specs` 录制每次 deploy 收到的 pod_spec(不改行为,断言端到端透传用)。
- routing.py 快照序列化**零改动**(`_TEMPLATE_FIELDS` 由 dataclass 自动派生;`template_from_json` 对默认 None 字段不矫正、list 原样透传——已核实)。
- 红线相关:不改 Lua/Redis 键;跨模块仍只经 pod_spec/共享顶层模块,无 SM↔RM import;错误路径仍走 `errors.py` 异常类型。

**存量库升级(先 ALTER 后发版,强制顺序)**:

```sql
-- MySQL 8 / PostgreSQL(生产)
ALTER TABLE service_config_template ADD COLUMN sidecars JSON NULL;
ALTER TABLE service_config_template ADD COLUMN agent_host_path_mounts JSON NULL;
ALTER TABLE service_config_template ADD COLUMN agent_configmap_mounts JSON NULL;
ALTER TABLE service_config_template ADD COLUMN agent_pvc_mounts JSON NULL;
-- SQLite(仅 local 调试库)同款四条(SQLite 写 JSON 不带 NULL)
```

框架 `init_table` 只 create_all 不补列;ORM 按显式列名 SELECT,旧表 + 新代码 = 查询直接 SQL 报错(`agent_env`/`health_path` 当年同款义务)。

## 验证

- 单测:pytest 计数 **191 → 244 → 277**(sidecar 批 +33 挂载批,全绿)。新增:
  - `tests/session_manager/test_sidecars.py`(37):承重断言 `test_sidecars_none_keeps_deploy_ver_byte_identical`(无 sidecar 指纹与旧字段集逐字节相等——存量暖 Pod 不被全量日落的直接证据)、`test_sidecars_order_and_key_order_do_not_change_fingerprint`(键序/列表序重排同指纹,缺陷④回归网 sidecar 版)、`test_sidecars_empty_list_normalized_to_none`、jiuwenbox 全量样例规范化、拒绝矩阵(缺 name/image、坏 name、重名、撞 container_name、未知键、坏 env、探针无 port、port 三类冲突、坏 hostPath×4、超 8 条)、`normalize_sidecars` 容忍坏输入;
  - `tests/resource_manager/test_k8s_pod_body.py`(10,_V1 记录型替身注入 `client._client`,零环境依赖):单容器黄金断言(annotations=None/单容器/卷只 nfs)、jiuwenbox 全量渲染(特权/caps/seccomp/无名端口/env/tcp 探针参数/resources/hp- 卷与挂载/apparmor annotation)、http 探针、无 port sidecar、port 冲突 DeployFailed、脏缓存坏项跳过、卷名规则参数化;
  - `tests/session_manager/test_config_store.py`(+6,sidecar 批):sidecars 下发→SQLite JSON 列 roundtrip=规范形、坏 sidecars 400 零副作用、**sidecar 镜像变更触发 A 类日落**(软摘除+新 deploy_ver+FakeK8s 收到新 pod_spec)、**移除 sidecars 同样 A 类**、`template_from_row` 归一(None/[]/garbage/坏项)、端到端(seed→route→`deployed_specs`/`pod_spec_json` 均含规范形)。
  - 挂载批(+33):`tests/session_manager/test_mounts.py` 25 个(三规范形/默认值/排序/显式=省略/拒绝矩阵 13 参/mount_path 冲突含撞 NFS/归一/指纹承重断言:空挂载与旧字段集逐字节同指纹、顺序重排同指纹、挂载变更 A 类);`test_k8s_pod_body.py` +2(主容器三挂载卷+挂载点全量断言含 sub_path/items/read_only 默认、sidecar cm+pvc);`test_sidecars.py` +3(sidecar cm/pvc 规范形、跨种类 mount_path 重复 400、坏 ConfigMap 名);`test_config_store.py` +3(三挂载 DB roundtrip、主容器挂载变更 A 类、行归一)。
- 真环境(2026-08-27,单实例 server 模式 PG + 真 Redis/K8s,`--with-sidecar`):
  - 手工验证:curl config_sync 下发**兜底 scope(空 routing_rules)+ sidecar 模板**(`min_idle_pods=1`)→ autoscale 无请求预热拉起 Pod `agentserver-box-*`,**2 容器(`agent`+`jiuwenbox`)2/2 Running 零重启**,sidecar 渲染含无名端口 8096/TCP 探针/env 注入;`route` 经兜底 scope 复用该 Pod 返回 `pod_sse_url`。
  - 全量冒烟:**78/78 PASS**(75 基础 + 3 sidecar 阶段)。首轮 74/78,4 失败复盘:①DB 落库计数 [5,5] 硬编码、②D-不变量5 `len(reg)==2`、③K-notify `len(reg)==0` 未做 `--with-sidecar` 感知(box Pod 加入注册表),④**既有缺陷**:表达式 or 支检查原在阶段 2 尾,彼时 e2e-main 已被 s1–s3 占满(cc=3),or 支 route 必然排队 504——原断言仅在「部署慢、会话先过期」时序下碰巧 200,镜像预分发后的快跑必现失败(与 sidecar 无关,系 routing_rules 表达式改动引入的时序脆弱点);修复=计数动态化/sidecar 感知 + tpl-box `pod_ttl=3600` 长存消除中途回收竞态 + or 支检查移至新增阶段 12b(清场后确定性 200)。
  - 替身镜像真环境缺陷(已修进脚本):同 Pod 双 influxdb 实例抢绑 RPC 端口 `127.0.0.1:8088` → sidecar CrashLoop;`_sidecar_standin` 补 `INFLUXDB_BIND_ADDRESS=:8098`。
- 遗留:多副本 e2e(`e2e_multi_replica.py`)未在本轮范围。
- **真 jiuwenbox 镜像闭环(2026-08-28)**:`jiuwenclaw-sandbox-amd64:0.0.6s` 手工验证(Pod 2/2 Running,特权/caps/seccomp/apparmor 全落地,cgroup hostPath 可见 cgroup.controllers,PVC 容器内写入节点后端落盘,agent 容器经 `127.0.0.1:8321` 得到 MCP 服务 HTTP 应答)+ 全量冒烟 **80/80 PASS**(`--sidecar-image` 真镜像模式:sidecar 切完整 jiuwenbox 规格,特权四件套 + cgroup hostPath + 8321)。**关键契约:该版本 `JIUWENBOX_LISTEN` 只接受 `http://`/`unix://` scheme,`tcp://`(EE 旧默认)启动即拒——真环境实测教训**。
- PVC 真环境补验(同日完成):本环境 nfs-provisioner 不供给(PVC 挂起无事件),改用**手动 hostPath PV + 空 storageClassName PVC** 绑定(nodeAffinity 指向可调度节点 0001——master 带 NoSchedule 污点,PV 亲和误指 master 时 Pod 永久 Pending「volume node affinity conflict」;PV nodeAffinity **不可变**,改亲和须删 PV/PVC 重建);全量样例(主容器 cm/hp/pvc + sidecar cm/hp/pvc 六挂载)Pod 2/2 Running,双容器 ConfigMap 内容可读、双 PVC 容器内写入在节点后端目录真实落盘。

## 挂载增量验证(2026-08-27 同日真环境)

- 手工:curl config_sync 下发兜底 scope + 模板(主容器 ConfigMap subPath + hostPath DirectoryOrCreate、sidecar ConfigMap subPath)→ 预热 Pod 2/2 Running;`kubectl get -o jsonpath` 卷名/挂载点与设计一致(`hp-agent-0-0`/`cm-agent-0-0`/`cm-jiuwenbox-0-0`);`kubectl exec cat` 主容器 `/etc/agent/config.yaml`=CM 内容、sidecar `/etc/box/policy.yaml`=CM 内容、hostPath 目录挂载可见。
- 全量冒烟 **80/80 PASS**(75 基础 + 5 sidecar 阶段,含新增「ConfigMap 资源就绪」「subPath 挂载内容可见」两项)。
- 教训:重启服务必须确认旧进程真死透(pkill 按命令行全文匹配会误杀自身 shell;healthz=200 可能来自未死旧实例——旧实例 sidecar 键集无 configmap_mounts 的 400 响应暴露了这一点)。

## 影响面

- 文档同步:HLD §3.1(template 字段表 sidecars 行 + config_sync 校验清单)、`spec/session-manager.md`(DB 列/ALTER 义务/sidecars.py schema 与指纹不变式/锁外校验)、`spec/resource-manager.md`(`_build_pod_body` 多容器条目 + FakeK8s deployed_specs)、本 feature 记录 + README 索引、CLAUDE.md 用例计数。
- 兼容性:无 sidecars 的模板行为/指纹/序列化与历史完全一致(承重断言固化);滚动升级窗口内**带 sidecars 的模板必须在全部副本升级完成后才下发**(旧副本对同一模板算"无 sidecar 指纹"→ 永不匹配 → 混合舰队),低峰滚动 + 完成后重放一次 config_sync 收敛。
- 运维前置:jiuwenbox 需 privileged + hostPath `/sys/fs/cgroup`,目标 namespace 须允许特权容器(PSA restricted 会拒);apparmor unconfined 在非 apparmor 节点 no-op;慢启动 sidecar 吃 ready_timeout 预算,按需调大模板 ready_timeout。
- 遗留开放问题:
  - RM 场景 N 只探 agent sse_port,sidecar 崩溃靠 kubelet 原地重启(Pod NotReady 但 agent 健康时 RM 不 purge)——后续可在 watch 加 sidecar tcp 探测;
  - manager 侧(manager_server schema/API/前端)尚未支持 sidecars 字段,manager 链路打通待后续任务;
  - `/visualization` 模板摘要不展示 sidecars(诊断需求出现时再加"sidecar 数"摘要字段)。
