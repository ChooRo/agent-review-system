# 招采智审 Agent 设计工作区

本目录按“需求—架构—当前规格—变更—研究—决策—交接—实现”分层维护采购文件、响应文件和合同三个审查 Agent 的设计。

## 文档入口

- 新环境部署：[docs/DEPLOYMENT.md](./docs/DEPLOYMENT.md)
- 产品边界：[docs/PRD.md](./docs/PRD.md)
- 总体架构：[docs/Architecture.md](./docs/Architecture.md)
- 接口索引：[docs/API.md](./docs/API.md)
- Agent当前规格：[docs/spec/feature/review-agents.md](./docs/spec/feature/review-agents.md)
- Agent Tool规格：[docs/spec/feature/agent-tools.md](./docs/spec/feature/agent-tools.md)
- 文档理解当前规格：[docs/spec/feature/document-understanding.md](./docs/spec/feature/document-understanding.md)
- Tool接口契约：[docs/spec/api/tool-contracts.md](./docs/spec/api/tool-contracts.md)
- 正式且唯一的运行时审查引擎：[backend/app/review_engine](./backend/app/review_engine)
- MVP运行与监控：[docs/MVP.md](./docs/MVP.md)
- 架构决策：[docs/ADR/ADR.md](./docs/ADR/ADR.md)

`backend/app/review_engine/` 是当前唯一权威的审查引擎实现；根目录历史 `src/` 不再作为运行入口。运行产物写入 `runs/<run_id>/`，由后端服务管理。

当前已正式化并接入运行时的Skill：

- `understand-document-structure`
- `understand-procurement-document`

响应文件理解、合同理解及相关审查能力尚未开放；当前仅运行采购文件单文件审查。

## 当前开发入口（2026-08-06）

当前只开发采购文件单文件审核：

- Vue 前端骨架：[frontend](./frontend)
- Python/FastAPI 后端骨架：[backend](./backend)
- 前后端对接文档：[docs/FRONTEND_BACKEND_INTEGRATION.md](./docs/FRONTEND_BACKEND_INTEGRATION.md)
- 开发交接文档：[docs/DEVELOPMENT_HANDOFF.md](./docs/DEVELOPMENT_HANDOFF.md)

响应文件审核和合同审核入口保持禁用。知识库当前提供法规文档及法规知识单元的只读检索；可执行审查规则的治理、发布与写入流程尚未开放，`/knowledge/rules` 因而返回空列表。

## 测试

唯一权威测试入口：`cd backend && uv run --no-sync pytest -q`。
