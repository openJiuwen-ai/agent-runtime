# echo —— 最小分布式服务样例

基于 `openjiuwen_runtime.service` 通用分布式服务框架的最小示例：返回 `{echo, idx}`，`idx` 由
Redis 原子计数，**跨副本全局递增**（验证「统一入口 + 无内存状态多副本 + 极简上手」）。

## 运行

```bash
# 1) 安装框架（openjiuwen_runtime.service）为可编辑包
pip install -e ../../service

# 2) 准备一个 redis（本地或远端），按需设置环境变量（见 .env.example）
export OPENJIUWEN_SERVICE_REDIS_URL=redis://localhost:6379/0

# 3) 启动
python echo_server.py
```

启动后：
- REST：`POST /api/echo`，body = 完整 Envelope：
  ```bash
  curl -XPOST localhost:8090/api/echo \
       -H 'content-type: application/json' \
       -d '{"type":"echo","metadata":{"request_id":"r1"},"rawdata":{"message":"hi"}}'
  # → {"type":"echo","metadata":{...},"rawdata":{"echo":"hi","idx":1},"ok":true,...}
  ```
- WebSocket：连接 `/ws`，每条文本帧发一个 Envelope JSON，回帧含 `idx`。

## 多副本（验证全局递增）

不同端口起两个实例，共享同一 redis，交替调用 → `idx` 全局递增（不各自从 1 开始）：

```bash
OPENJIUWEN_SERVICE_PORT=8091 python echo_server.py &
OPENJIUWEN_SERVICE_PORT=8092 python echo_server.py &
```

## 环境变量

| 变量 | 含义 | 默认 |
|---|---|---|
| `OPENJIUWEN_SERVICE_HOST` | 监听地址 | `0.0.0.0` |
| `OPENJIUWEN_SERVICE_PORT` | 监听端口 | `8090` |
| `OPENJIUWEN_SERVICE_REDIS_URL` | 协调用 redis 连接串 | `redis://localhost:6379/0` |
| `OPENJIUWEN_SERVICE_REDIS_KEY_PREFIX` | redis 键命名空间前缀 | `service` |
| `OPENJIUWEN_SERVICE_TITLE` | 服务标题 | `service` |
