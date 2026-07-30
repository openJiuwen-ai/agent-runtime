# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""最小 echo server（附录 A.1）：返回 {echo, idx}，idx 由 Redis 原子计数，跨副本全局递增。

业务只写 ctx + env，框架统一入口 + 无内存状态多副本（idx 走 Redis，进程内无状态）。

部署配置全部来自环境变量（详见 README.md / .env.example）：
  OPENJIUWEN_SERVICE_HOST / OPENJIUWEN_SERVICE_PORT
  OPENJIUWEN_SERVICE_REDIS_URL / OPENJIUWEN_SERVICE_REDIS_KEY_PREFIX

运行：
  pip install -e ../../service          # 安装 openjiuwen_runtime.service（框架）
  python echo_server.py                 # 读环境变量；多副本：不同 OPENJIUWEN_SERVICE_PORT 起多实例
"""
from openjiuwen_runtime.service import App, Envelope, SystemContext


def make_ctx() -> SystemContext:
    return SystemContext.from_settings()        # 生产：读 OPENJIUWEN_SERVICE_REDIS_URL 等


app = App(make_ctx, prefix="/api")              # make_ctx 作为构造参数；内部持有 router


@app.handle("echo")                             # 自动同时暴露 POST /api/echo 与 WS type="echo"
async def echo(ctx, env: Envelope):
    idx = await ctx.kv.incr("echo:idx")         # Redis INCR：跨副本原子递增，进程内无状态
    return {"echo": env.rawdata.get("message", ""), "idx": idx}


if __name__ == "__main__":
    app.run()                                   # 部署（uvicorn）：读 OPENJIUWEN_SERVICE_HOST/PORT
