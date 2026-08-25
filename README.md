# 招采智审 Agent 设计工作区

本项目用于采购文件智能审查。

## 使用说明

- 新环境部署：[DEPLOYMENT.md](./DEPLOYMENT.md)
- 正式且唯一的运行时审查引擎：[backend/app/review_engine](./backend/app/review_engine)

`backend/app/review_engine/` 是当前唯一权威的审查引擎实现；根目录历史 `src/` 不再作为运行入口。运行产物写入 `runs/<run_id>/`，由后端服务管理。

当前已正式化并接入运行时的Skill：

- `understand-document-structure`
- `understand-procurement-document`

响应文件理解、合同理解及相关审查能力尚未开放；当前仅运行采购文件单文件审查。

## 当前开发入口（2026-08-06）

当前只开发采购文件单文件审核：

- Vue 前端骨架：[frontend](./frontend)
- Python/FastAPI 后端骨架：[backend](./backend)
响应文件审核和合同审核入口保持禁用。知识库当前提供法规文档及法规知识单元的只读检索；可执行审查规则的治理、发布与写入流程尚未开放，`/knowledge/rules` 因而返回空列表。

## 测试

唯一权威测试入口：`cd backend && uv run --no-sync pytest -q`。
