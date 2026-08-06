# 招采智审 Agent 设计工作区

本目录按“需求—架构—当前规格—变更—研究—决策—交接—实现”分层维护采购文件、响应文件和合同三个审查 Agent 的设计。

## 文档入口

- 产品边界：[docs/PRD.md](./docs/PRD.md)
- 总体架构：[docs/Architecture.md](./docs/Architecture.md)
- 接口索引：[docs/API.md](./docs/API.md)
- Agent当前规格：[docs/spec/feature/review-agents.md](./docs/spec/feature/review-agents.md)
- Agent Tool规格：[docs/spec/feature/agent-tools.md](./docs/spec/feature/agent-tools.md)
- 文档理解当前规格：[docs/spec/feature/document-understanding.md](./docs/spec/feature/document-understanding.md)
- Tool接口契约：[docs/spec/api/tool-contracts.md](./docs/spec/api/tool-contracts.md)
- 正式Skill目录：[src/skills](./src/skills)
- MVP运行与监控：[docs/MVP.md](./docs/MVP.md)
- 架构决策：[docs/ADR/ADR.md](./docs/ADR/ADR.md)

`src/`是按照当前方案重新实现的独立MVP，不引用项目旧`backend/`代码。运行产物写入`runs/<run_id>/`，可以逐步查看、暂停和恢复。

当前已正式化并接入运行时的Skill：

- `understand-document-structure`
- `understand-procurement-document`

响应文件理解、合同理解及三个审查Agent仍使用`src/skills.json`中的临时配置，后续逐项迁移为正式Skill。

## 当前开发入口（2026-08-06）

当前只开发采购文件单文件审核：

- Vue 前端骨架：[frontend](./frontend)
- Python/FastAPI 后端骨架：[backend](./backend)
- 前后端对接文档：[docs/FRONTEND_BACKEND_INTEGRATION.md](./docs/FRONTEND_BACKEND_INTEGRATION.md)
- 开发交接文档：[docs/DEVELOPMENT_HANDOFF.md](./docs/DEVELOPMENT_HANDOFF.md)

响应文件审核、合同审核、知识治理等入口在前端中保持禁用，待采购文件审核闭环完成后按交接文档扩展。
