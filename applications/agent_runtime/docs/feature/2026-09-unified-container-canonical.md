# 容器规范形统一——主/sidecar 单一 canonical,加容器字段只改一处

- 日期:2026-09-11
- 涉及模块:session_manager / resource_manager / service-core / 测试 / 文档

## 背景与动机

2026-08 容器表拆分后,存储层(wire 三段式 + `service_config_container` 表)已统一,但**水合出口仍投影回两套历史形状**:主容器拍平成 `Template` 的 ~23 个 `agent_*` 扁平字段,sidecar 用 `sidecars.py` 冻结的 24 键规范形(含 `env_from` 条件键)。这是当时为保 `deploy_ver` 指纹连续(暖 Pod 不被伪日落)而刻意保留的适配层。

后果:**加一个容器字段要改六处**——container_spec 解析 + 两个投影 / Template 扁平字段 / spec_fields 指纹字段集 / k8s.py 两处渲染,外加测试与文档;且主/sidecar 默认值在两处各写一份,存在漂移隐患。

**决策前提(用户确认)**:当前开发阶段,无生产存量 Pod——`deploy_ver` 指纹一次性重置可接受,换取"加字段只改一处"(canonical 定义 + 渲染分支)。

## 方案

1. **单一 canonical(13 键,role 不影响键集,只影响值域/默认值)**:新顶层共享模块 `containers.py` 吸收 `sidecars.py` 全部职责并**删除后者**(不留 re-export shim——双名指同一层违背单一规范形,旧不变式 docstring 必误导后人)。canonical = `name/image/image_pull_policy/ports/env/env_from/resources/三类挂载/nfs/security_context/readiness_probe`。
2. **全键填满,废除条件键**:`env_from` 值可为 None 但键恒在。条件键的唯一理由(存量指纹零扰动)随指纹重置消失;它反而是"有值才出现"的等价异形来源。NFS 从模板级三元组**收进主容器**(`nfs` 键,main 独有)——水合单输出、渲染天然适配、Template 实现净切。
3. **指纹不变式在新形状重建**:canonical 幂等、容器间 name 升序、`capabilities_add/drop` **排序去重**(值序无 K8s 语义,借重置窗口收紧)、`ports` http 端口号==sse 时丢弃(RM 渲染同名端口去重的既有约定,渲染同形 ⟺ canonical 同形)、probe path 前导 `/` 归一迁入 canonical。基线常量重新冻结(`test_containers.py`:0ac9bf7494973132 / 3c1ba3cbcf0b1ed7 / 4ca65eb0ce7220a8;旧常量随扁平字段集作废)。
4. **`normalize_pod_spec`(承重补强)**:RM `_deploy_ver` 与渲染入口统一前置——模板级透传,容器段**只补 canonical 缺省键、绝不改已有值、不丢项**。未来加容器字段(新键默认值==旧行为)时,Redis 里未刷新的旧 `pod_spec_json` 正规化后与新算指纹相等 → 零伪 A 类日落;行为性新键应当日落,日落正确。配套承重测试:"补缺省==全键指纹"+"合法不同值必不等"(防过度归一造伪相等 → 旧 Pod 误复用)。
5. **`spec_fields.py` 收缩**:DEPLOY_FIELDS = 模板级 pod 字段(namespace/node_name/pod_name/sse_path)+ `main_container` + `sidecars` 两键,容器字段增删**不再动本文件**——这就是"只改一处"的落点。
6. **Template 重构**:删 23 个扁平容器字段,`main_container`(canonical dict)+ `sidecars`(canonical 列表);保留只读兼容 property `agent_image/sse_port/health_path/container_name`(UI 摘要/诊断,不参与指纹序列化)。`container_spec.py` 缩为纯 wire 翻译 + `build_canonical` 水合出口(读/写路径共用 `config_store._hydrate_containers`)。
7. **RM 单一渲染器**:`_build_sidecar_container`/`_build_sidecar_security_context`/`_build_sidecar_probe` 与主容器内联段合并为 `_build_container(c, cont, role, idx, pod_id, pvc_seen)`,role 分支五处(NFS 仅 main 首序/ports 命名契约/探针形态/secctx 键域/apparmor annotation)。**渲染输出与历史逐字节一致**(黄金断言 + e2e 阶段 2c 锚定)。
8. **被否方案**:①RM 双读 shim(新旧两形兼容一个升级窗口)——指纹重置已强制全量日落,shim 净亏;②sidecars.py 改名保留——引用面纯机械但双名永生;③分两个中间提交切 SM/RM——deploy_subset 与 RM 渲染是同一运行契约,中间形态需保留被删投影当过渡适配器,净成本高于原子切换。

## 实现

- **新增** `src/agent_runtime/containers.py`(SM/RM 共享顶层,与 spec_fields/mounts 同款先例):canonical/normalize/validate/conflict/派生 helper/normalize_pod_spec/默认值单源常量(DEFAULT_*、MAIN/SIDECAR_PROBE_DEFAULT、SECCTX_DEFAULT、RESOURCES_DEFAULT)。
- **删除** `src/agent_runtime/sidecars.py`;引用方(models/container_spec/config_store/k8s/tests)全部切 containers。
- `spec_fields.py`:新 DEPLOY_FIELDS(8 键);`models.py`:Template 重构 + 兼容 property;`container_spec.py`:删两个投影、`_parse_*` 默认值 import 单源、`build_canonical`;`config_store.py`:删 legacy 内列水合(无 `main_container_id` 行 → WARNING+None,fail-closed)、`_COLUMN_OF` 缩到模板级、新增 `_LEGACY_INLINE_CONTAINER_KEYS`(mixed-400 检测)、`_hydrate_containers` 单出口;`routing.py`:`template_from_json` legacy 扁平快照检测(→ ValueError 判坏重建);`k8s.py`:单一 `_build_container` + shape 探测(缺 `main_container` → DeployFailed);RM `orchestrator.py`(`_deploy_ver` 正规化前置/sse_url/REGISTER helper 化)、`sweeper.py`(autoscale legacy 缓存 skip_legacy_spec/探测回退读 main_container)。
- **不动**:6 个 Lua(纯透传)、`state.py` 键 schema、`pod:info` 烘焙字段、`mounts.py`、config_sync wire(三段式契约零变化)、全部 scripts 载荷构造(e2e/load_test/config_sync_seed)、DB schema(**无 ALTER**:容器表 15 列原样,模板表 legacy 列继续死值,`agent_image=""` 死值写法保留)。

## 验证

- 单测:497/497 全绿(拆分提交:C1 新增 containers.py+50 用例纯增 → C2 投影内核+适配器等价证明(既有断言零改动通过) → C3 原子切换+测试改造)。承重断言:指纹基线冻结(新三常量)、`normalize_pod_spec` 补缺省==全键指纹、不同值必不等、canonical 幂等、legacy 行/快照/缓存三方 fail-closed、渲染黄金断言(kwargs 级,含主容器空 secctx 省 kwarg、sidecar `is None`)。
- 真环境:C3 后本地替身冒烟 + 真镜像发布门禁(三件套 + `--with-sidecar --with-mounts`,阶段 2c 24 项逐字段断言 = 渲染不变锚)在 C4 执行。

## 影响面

- 文档同步(同一提交):HLD(内部实现注/场景 M A 类字段表三分类)、spec/session-manager.md(单轨水合/containers.py 段/水合出口)、spec/resource-manager.md(`_build_container` 五分支/`_deploy_and_register` helper)、spec/service-core.md(容器字段不碰 spec_fields 指引)、api/config-plane-api.md(pod_spec_json 示例换嵌套形)、CLAUDE.md(用例计数/模块描述)。
- **`deploy_ver` 一次性重置**:升级后首个 config_sync 的版本收敛把旧 idle Pod 全部软摘除,按 `pod_ttl` 回收 + autoscale 重建(dev 可 config_refresh 加速)。这是本重构的**有意决策**,非缺陷。
- **升级操作序列**:①前置检查(存量库):`SELECT template_id FROM service_config_template WHERE main_container_id IS NULL OR main_container_id='';`——非空则**先重放 config_sync**(否则这些模板 fail-closed 跳过,scope 落兜底);②**全量重启**换镜像(不做新旧混版:混版下两套指纹算法 → 暖池互不复用 + autoscale 误判 stale);③启动后重放一次 config_sync 或调 config_refresh(Redis `pod_spec_json`/快照收敛,RM 侧对旧形缓存 autoscale skip_legacy_spec 待重推);④dev 环境可 FLUSHDB 简化。
- **后续加容器字段的标准路径**:containers.py(canonical 键 + 默认值常量 + role 校验)→ container_spec.py(wire 键 + `_parse_*`)→ k8s.py `_build_container`(渲染分支)→(可选)容器表新列(框架只 create_all,存量库手工 ALTER)。spec_fields/Template/指纹/投影**零改动**。
