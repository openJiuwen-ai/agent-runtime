# agent-runtime 服务镜像。
#
# 构建上下文必须是**仓库根**（applications/agent_runtime 的
# [tool.uv.sources] 指向 ../../foundation 与 ../../service 的本地 editable
# 源，镜像内必须保持同样的相对布局才能解析）：
#   cd <repo-root>
#   docker build -f applications/agent_runtime/deploy/Dockerfile -t <tag> .
#   （或直接用同目录 build_image.sh）
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 保持仓库相对布局：foundation / service / applications/agent_runtime
COPY foundation/ /app/foundation/
COPY service/ /app/service/
COPY applications/agent_runtime/ /app/applications/agent_runtime/

# uv 的 [tool.uv.sources] 相对路径以 CWD 锚定 —— 必须在应用目录内执行 sync
# （../../foundation → /app/foundation）。--frozen 用已提交 uv.lock（含
# server extra）；--no-dev 去掉 pytest/fakeredis。
# logs/：框架日志 file handler（logging.yaml 的 CompressedRotatingFileHandler）
# 写 CWD 相对路径 logs/，须预建且归 appuser，否则 dictConfig 直接失败。
WORKDIR /app/applications/agent_runtime
RUN uv sync --frozen --extra server --no-dev \
 && useradd --create-home --uid 1000 appuser \
 && mkdir -p logs && chown appuser:appuser logs

USER appuser

EXPOSE 8091
ENTRYPOINT ["/opt/venv/bin/agent-runtime", "--mode", "server"]
CMD []
