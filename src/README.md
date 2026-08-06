# 实现目录说明

本目录是按照“全文理解—台账—场景视图—Block证据审查”重新实现的独立MVP，不导入旧`backend/`代码。

- `main.py`：run、resume、status、events和serve命令；
- `settings.py`：配置默认值、JSON读取、环境变量覆盖、路径解析和启动校验的唯一入口；
- `api/app.py`：FastAPI监控接口；
- `services/mineru.py`：PDF/DOCX原生MinerU调用、旧DOC转PDF和统一Block；
- `services/legal_knowledge.py`：法规MinerU解析、DOCX直读降级以及条款知识单元构建；
- `services/llm.py`：OpenAI-compatible模型调用和脱敏追踪；
- `services/runtime.py`：状态、事件日志和步骤检查点；
- `services/workflow.py`：目录规则优先、疑难结构LLM补充、事项提取、台账和三个Agent的十一阶段流水线；
- `tools/registry.py`、`tools/schemas.py`：Tool统一注册、Agent白名单、审计和调用契约；
- `tools/*_tools.py`：按文档、台账、检索、规则、校验、采购、响应、合同和整改分组的业务Tool；
- `skills/understand-document-structure`：正式文档结构理解Skill及输出契约；
- `skills/understand-procurement-document`：正式采购文件理解Skill及候选事项契约；
- `skills/review-procurement-document`：正式采购文件审查Skill、法规适用性边界及问题证据契约；
- `skills.json`：尚未正式化的响应、合同理解和三个审查Agent临时配置。

`services`负责底层实现与流程产物，`tools`负责给Agent提供受控接口。Agent不能绕过Tool直接操作文件、数据库或任务状态。

本机配置统一放在`config.json`，模板为`config.example.json`。真实密钥优先通过`REVIEW_LLM_API_KEY`环境变量提供，两个文件均不得承担Skill提示词、法规正文或任务状态管理。

原子事项提取默认使用`workflow.extract_workers=3`有限并发；若外部模型限流，可在本机配置中降为1或2。
每批成功结果立即写入`runs/<run_id>/batch_artifacts/`，恢复时按输入指纹复用，避免单批失败导致其他批次重复调用。

法规知识处理说明见[法规知识处理流程](../docs/spec/feature/legal-knowledge-processing.md)。法规原文JSON默认写入`knowledge/rules/`，不与`rules.example.json`中的可执行审查规则混为一体。

运行说明见[../docs/MVP.md](../docs/MVP.md)。
