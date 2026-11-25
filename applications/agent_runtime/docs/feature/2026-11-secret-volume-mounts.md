# 模板 Secret 文件挂载与部署指纹兼容

- 日期：2026-11-25
- 涉及模块：session_manager / resource_manager / service-core / 测试 / 文档

## 背景与动机

Runtime 已支持 envFrom.secretRef 和内部 mTLS 专用 Secret，但模板 volumes
不能声明通用 Secret 文件卷。HTTPS MCP 的私有 CA 需要以文件形式提供给
AgentServer，故增加原生 secret 卷源，保持证书校验开启。

## 方案

- 模板 volumes 支持 secret.secretName 与可选 items（key/path）。容器通过
  volumeMounts 按卷名引用，主容器与 sidecar 共用解析与渲染逻辑。
- Secret 挂载只读，支持 subPath。optional/defaultMode/items.mode 等尚未
  支持的字段返回校验错误。配置存储只持有引用，不保存 Secret 内容。
- canonical 保留 secret_mounts 空列表，旧配置在 SM/RM 入口补齐。
- SM/RM 共用 deploy_fingerprint：仅在指纹投影中省略空 secret_mounts，
  保持新增 Secret 功能前的已注册 Pod hash；非空挂载完整参与指纹。
  兼容基线包含既有 empty_dir_mounts，不恢复更早的历史指纹。
- 补齐 RM 对 empty_dir_mounts 的缺省处理，修复旧缓存与 SM 规范形不一致。

## 实现

container_spec.py 解析与关联卷；mounts.py 校验 Secret；containers.py
补齐规范形；k8s.py 生成 V1SecretVolumeSource，按 Secret 名称及 items
跨容器复用卷；spec_fields.py 统一 SM/RM 部署指纹入口。无数据库 DDL 变更。

运行时模板 volumes：

```json
[{"name":"ca","secret":{"secretName":"shared-ca","items":[{"key":"ca.crt","path":"ca-bundle.pem"}]}}]
```

主容器模板增加：

```json
{
  "volumeMounts": [{"name":"ca","mountPath":"/etc/agent-ca","readOnly":true}],
  "env": [{"name":"SSL_CERT_FILE","value":"/etc/agent-ca/ca-bundle.pem"}]
}
```

## 验证

- 单测覆盖旧 Pod hash、缺省/空列表、SM/RM 一致性、主/sidecar 版本变化、
  items 顺序、DB 往返与 route 传递。Pod 渲染覆盖 key/path、只读与卷去重。
- `scripts/verify_secret_volume.py` 已在 OpenShift 使用真实 AgentServer
  0.0.20y 镜像通过：服务就绪；只投射指定 CA key；默认信任拒绝测试 CA；
  SSL_CERT_FILE 指向挂载文件后 HTTPS 验证通过；临时 Pod/Secret 自动清理。
- 公仓基线亦失败的三项并发用例：
  `test_concurrent_route_no_oversell_no_lua`、
  `test_concurrent_routes_peer_reuse_single_idle_pod`、
  `test_concurrent_routes_after_return_to_idle`。该问题未纳入本次修改。

## 影响面

Secret 必须位于目标 Pod 的 namespace，CA key 应提供所需的信任链。
使用 items 可避免挂载同一 Secret 的其他 key。完整 config_sync 必须同时
包含卷和挂载引用。所有相关 Runtime 副本升级后再下发 Secret 模板。

仅更新 Secret 内容不会改变 deploy_ver，也不会主动重建 HTTPX SSLContext；
轮换需结合新建 Pod 或应用重载。subPath 不跟随 Secret 投射更新。
subprocess 模式不实现 Kubernetes Secret 投射。
