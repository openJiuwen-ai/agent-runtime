# CLAUDE.md(根)

多模块嵌套 git 仓库(独立于外层 jiuwenclaw,提交分开做)。

核心模块 agent_runtime(会话编排服务,M0–M8 已完成,维护期):一切开发指引见 `applications/agent_runtime/CLAUDE.md`(红线规则/测试/部署/文档体系,先读)。

其余内容:`service/`(openjiuwen_runtime 服务框架:App/Envelope/SystemContext)、`applications/echo`(App 范式参考)、`docs/{zh,en}/`(平台用户文档,与 agent_runtime 服务无关)。
