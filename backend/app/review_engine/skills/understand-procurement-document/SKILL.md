---
name: understand-procurement-document
description: Extract traceable atomic procurement requirements from procurement-document Blocks before compliance review or cross-file comparison. Use for qualification, technical, evaluation, commercial, contract, schedule, attachment, and response-material requirements. Do not issue legal conclusions.
---

# 采购要求提取

从当前批次 `blocks` 完整提取原文明示、可核验的采购要求，供后端建立台账；不得进行合规审查。

## 提取规则

1. 扫描项目日程、资格与实质性条件、技术与验收、评审与评分、报价与付款、合同履约、附件及响应材料，不因分类为空而编造事项。
2. 一个候选只表达一个主要动作。复合条款、编号子项、不同条件或不同数值分别提取，并保留共同前提、例外、否定词、金额、日期、比例、期限、单位和比较方向。
3. 只使用原文，不引入法规、常识或审查结论；不把目录、标题、页眉页脚、孤立符号和纯引用提示当作要求。
4. `statement` 必须是脱离上下文仍完整的独立句。不得输出残句，也不得概括、合并或删除重复、冲突及疑似错误的原文要求。
5. `evidence_block_ids` 只能使用输入中的真实 `id`；`evidence_quote` 必须是对应 `x` 中的连续原文。无法同时提供有效ID和摘录时不输出。
6. `r=primary` 是本批主要内容；`r=repeated_context` 仅辅助理解，不得仅凭重复上下文再次生成候选。`structure_context` 不能作为证据，与Block冲突时以Block为准。
7. 表格按完整业务行提取并保留表头语义。`tf` 只限定当前数据行范围；重复表头不生成候选，证据继续引用原表格Block ID，不引用 `fragment_id`。
8. `primary_category` 必须来自 `allowed_categories`。不确定强制性时不得把描述性内容升级为必须、否决或无效条件。

## 输出

只输出调用方规定的严格JSON，不附解释、覆盖报告、拒绝项或未解析引用汇总。每项至少包含 `primary_category`、`requirement_type`、`statement`、`evidence_block_ids` 和 `evidence_quote`；其他字段仅在原文明示且非空时输出。没有合格事项时返回空数组。
