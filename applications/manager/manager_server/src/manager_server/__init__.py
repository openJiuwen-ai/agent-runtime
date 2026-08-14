"""JiuwenClaw Manager — EE 管理平面服务。"""

# 重构:jiuwenclaw 仅本地 provision(subprocess 拉起 gateway/agent)时需要,服务本身(REST/验签)不需要。
# 迁移期不做 provision 且 jiuwenclaw 不在 runtime 内,故容错降级、不阻塞导入(provision 真用到时再报错)。
try:
    from manager_server.infrastructure.jiuwenclaw_importer import ensure_jiuwenclaw_importable

    ensure_jiuwenclaw_importable()
except ImportError:
    pass

__version__ = "0.1.0"
