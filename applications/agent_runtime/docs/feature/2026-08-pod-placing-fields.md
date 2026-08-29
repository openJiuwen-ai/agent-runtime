# Template 扩展 pod 落位字段(node_name/run_as_user/run_as_group)+ PVC 同 claim 跨容器去重

- 日期:2026-08-29(代码 2026-08-28 随 026450be 入库,收尾同日补齐)
- 里程碑 / commit:026450be(chenhui,联调主线)/ 收尾:校验 + 单测 + 文档(本提交)
- 涉及模块:session_manager / resource_manager / service 框架(redis 密码,8265bc90)/ 测试 / 文档

## 背景与动机

deploy tool 联调(chenhui)修「启动 agentserver 的各种问题」:镜像本地 tag 预载在特定节点
(`IfNotPresent`,无仓库可拉),需要模板点名绑节点;容器 uid 需对齐存储属主;同 claim PVC
建两个卷在 kubelet 侧有挂载死锁/超时风险(gateway 先遇到并改为共享一卷)。RM `PodModels.node_name`
自首提交即预留,本改动把链路从配置模板打通到渲染层。

## 方案

- 三字段归 **A 类**(`spec_fields.DEPLOY_FIELDS`,进 `deploy_ver` 指纹,变更即日落)——
  值烘焙进运行中的 Pod,与 agent_image 同语义。
- 默认 `None`:**fingerprint 只滤 None → 存量模板 deploy_ver 不因字段引入漂移**(零全量伪日落;
  与 sidecars 的 None 归一先例同款不变式);渲染侧 None = 不绑节点 / 不设 securityContext,
  与历史 Pod 逐字节一致。
- PVC 去重:`_render_volume_mounts` 增加 `pvc_seen` 登记簿,`_build_pod_body` 内贯穿主容器与
  sidecars;同 claim 只建一个卷(卷名取首现容器,主容器先渲染),后继容器 volumeMount 复用。
  直调(单容器/旧测试)不传 `pvc_seen` 则不去重,行为不变。
- **决策(2026-08-29 需求方确认)**:三字段为联调合法能力。与 2026-08-28「fs_group 整体回退
  暂缓」**不冲突**:那次会议否的是「Pod spec 层解决 PVC 写权限」(volume plugin 不做属主管理,
  存储侧预属主才是根治);`run_as_user` 只改进程身份不改卷属主,不在被否范围。

被否/暂缓的备选:pod 级 fsGroup、initContainer chown——见 `2026-08-e2e-full-mounts-stage.md`
真实缺陷②考证;PV 目录预属主为现行解法。

## 实现

- `session_manager/models.py`:Template +3 字段(默认 None)。
- `spec_fields.py`:DEPLOY_FIELDS +3(SM/RM 共用,指纹字段集两端一致的前提)。
- `config_store.py`:表 +3 列(nullable,框架 `_sync_missing_columns` 启动自动 ALTER——
  存量库免手工迁移);`_COLUMN_OF`/`_INT_FIELDS` 映射;**收尾新增** `_validate_pod_placing_fields`
  (run_as_user/group ≥0 对齐 sidecars.py `minimum=0` 先例;node_name hostname 形态 ≤253——
  坏值 Pod 永久 Pending 挂满 ready_timeout 才暴露,提前到 config_sync 锁外确定性 400;
  空串归一 None 同未设)。
- `resource_manager/k8s.py`:`pvc_seen` 去重;主容器 securityContext(给了才设);
  `V1PodSpec.node_name` 透传。
- 卷级 `read_only` 取首现容器值——kubelet 语义:卷源 ro 压 mount 级 rw(主 ro + sidecar rw
  组合下 sidecar 实际只读)。单测锁定现状;若产品要求混合读写需改为 400 或拆卷。

## 验证

- 单测:295 → 304(+9)。`test_k8s_pod_body.py` +5:主容器 securityContext 设/半设/不设、
  node_name 渲染与空串归 None、同 claim 去重(单卷/复用卷名/不误伤异 claim)、read_only
  首现锁定;`test_config_store.py` +4:三字段下发→DB→回读→deploy_subset 回环(数字串容忍
  转 int)、畸形 400(bool/非数字串/负值/坏节点名,零副作用)、A 类判据(改 node_name →
  deploy_ver 变化)、空串 node_name 归一 None。
- 真环境:三字段与 PVC 去重的**真环境生效实证未覆盖**(渲染层单测已锁);e2e 门禁 tpl-mnt
  主/sidecar 用不同 claim,去重路径未被走到——已列 `e2e-test-cases.md` §8.3 缺口清单。
- 同笔(8265bc90)redis 密码:`REDIS_PASSWORD` env → 客户端 password kwarg(同设覆盖 URL
  内嵌);裸命名是 deploy tool Secret envFrom 注入约定,已记 service-core.md。

## 影响面

- 文档同步:HLD 模板字段表 +2 行(node_name、run_as_user/group——语义权威);
  `spec/session-manager.md`(config_sync 校验清单、Template 描述);
  `spec/resource-manager.md`(卷名去重语义、pod 落位渲染);`spec/service-core.md`
  (REDIS_PASSWORD);`spec/e2e-test-cases.md`(缺陷②陈述修正——原文「主容器不渲染
  securityContext」自 026450be 起不再无条件成立、§8.3 缺口增补)。
- 兼容性:存量模板/存量 Redis 缓存 pod_spec(无三键)渲染不变;升级滚动双向安全。
- 遗留:§8.3 新增三项真环境实证缺口;read_only 混合语义是否收紧待产品口径。
