# openjiuwen-runtime-access

Access 提供跨通道的统一消息模型、运行时上下文和生命周期基础接口。当前版本支持基础设施事件与消息事件的定义、注册、移除和异步执行。

## 当前能力

| 类型 | 用途 |
| --- | --- |
| `MsgType` | 定义 Access 支持的标准消息类型 |
| `UnifiedMessage` | 表示跨通道传递的标准消息 |
| `GatewayContext` | 保存消息处理过程中的链路、会话和租户信息 |
| `ContextCarrier` | 传递应用级全局上下文和请求上下文 |
| `RequestContextFactory` | 定义应用请求上下文的创建和补充接口 |
| `LifecyclePhase` | 定义基础设施和消息处理阶段 |
| `InfraContext` | 保存网关、Feature 和通道事件的上下文 |
| `MessageContext` | 保存消息处理事件的上下文并支持阶段派生 |
| `UserRequest` | 保存生命周期回调使用的消息和执行数据 |
| `LifecycleHookRegistry` | 注册、移除并执行生命周期回调 |

## 项目结构

```
access/
├── openjiuwen_runtime/
│   └── access/
│       ├── __init__.py
│       ├── models.py
│       ├── lifecycle/
│       │   ├── __init__.py
│       │   ├── models.py
│       │   └── registry.py
│       └── core/
│           ├── __init__.py
│           └── context.py
└── tests/
    └── unit_tests/
        └── access_gateway/
```

## 快速开始

运行环境要求 Python 3.11.4 及以上版本。

```bash
cd access
pip install -e .
```

## 消息模型

```python
from openjiuwen_runtime.access import MsgType, UnifiedMessage

message = UnifiedMessage(
    msg_id="message-1",
    msg_type=MsgType.USER_REQUEST,
    carrier="web",
    src="user-1",
    dst="session-1",
    payload={"text": "hello"},
)
```

`MsgType` 包含运营商消息和 Agent 网关消息类型。`UnifiedMessage.raw` 保存可选的原始报文，默认值为 `None`。

## 运行时上下文

```python
from openjiuwen_runtime.access import ContextCarrier, GatewayContext

global_context = {"region": "cn"}
request_context = {"user_id": "user-1"}
carrier = ContextCarrier(
    global_context=global_context,
    request_context=request_context,
)

gateway_context = GatewayContext.from_message(message, trace_id="trace-1")
gateway_context.context_carrier = carrier
child_context = gateway_context.fork(attrs={"stage": "normalized"})
```

`ContextCarrier.current()` 优先返回请求上下文。请求上下文为空时返回全局上下文。

`GatewayContext.from_message()` 按 `payload.session_id`、`payload.session`、`dst` 的顺序提取会话标识。`GatewayContext.fork()` 复制当前字段和属性，并应用传入的覆盖值。

应用通过实现 `RequestContextFactory` 接口提供全局上下文、创建请求上下文并补充归一化后的上下文信息。

## 生命周期机制

`LifecyclePhase` 将事件划分为基础设施阶段和消息阶段。基础设施回调接收 `InfraContext`，消息回调接收 `MessageContext` 和 `UserRequest`。

```python
from openjiuwen_runtime.access import (
    InfraContext,
    LifecycleHookRegistry,
    LifecyclePhase,
)

hooks = LifecycleHookRegistry()

async def record_start(context: InfraContext) -> None:
    context.set_attr("recorded", True)

hooks.on_infra(
    LifecyclePhase.GATEWAY_START,
    record_start,
    feature="audit",
)
```

`on_infra_all()` 和 `on_message_all()` 注册对应分类的全局回调。`off()` 按阶段移除回调，`off_feature()` 按 Feature 名称移除回调。单个回调发生异常时，注册中心记录异常并继续执行后续回调。

## 测试

```bash
uv sync --group dev
uv run pytest -q
```

## 许可证

本项目遵循仓库根目录中的 [LICENSE](../LICENSE)。
