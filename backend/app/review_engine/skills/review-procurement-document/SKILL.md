---
name: review-procurement-document
description: Review a procurement, tender, inquiry, or bidding document for completeness, clarity, internal consistency, enforceability, and potential regulatory compliance risks using its whole-document profile, procurement ledger, deterministic checks, candidate rules, legal units, and traceable source Blocks. Use after document understanding has produced a procurement ledger, including first review and rectification re-review. Produce evidence-backed candidate findings for human confirmation; never issue an unsupported final legal conclusion.
---

# 采购文件审查

## 输入前提

本 Skill 只执行最终合并。仅在以下输入版本一致时开始：

- 七类主题的`coverage_matrix`；
- 已形成证据链的`review_candidates`；
- 可执行规则匹配摘要及经适用性门禁确认的`legal_context`；
- 候选中的采购文件Block ID和法规条款单元ID。

候选与覆盖矩阵版本不一致或证据不可定位时，停止合并相应候选并标记人工确认。输出字段和枚举见[审查结论契约](references/review-finding-contract.md)。

`document_structure`中的章节职责、术语、附件、引用、全局约束和未解析项只用于确定检查范围、补查方向和跨章节关系，不能替代采购文件Block作为问题证据。

## 审查流程

1. 读取`coverage_matrix`，确认七类主题均有`reviewed`或`evidence_insufficient`状态。
2. 只合并`review_candidates`中已经形成“法规义务/规则要求—采购事实—差异”的候选问题。
3. 对跨主题的同一问题去重，保留所有来源候选ID和两侧采购文件证据。
4. 不新增候选问题，不把确定性线索、解析告警或模型总结直接转换为业务问题。
5. 为每个问题绑定采购文件`evidence_block_ids`；引用法规时同时绑定`legal_unit_ids`。
6. 运行证据校验。无法回到采购文件原文的问题不得标记为已验证。
7. 汇总总体结论，分别说明业务问题、证据不足和主题覆盖，不在业务问题中混入解析/OCR告警。

## 强制覆盖表

以下七类必须逐类返回覆盖状态，不能因没有发现问题而省略：

| 主题 | 最低核验范围 |
| --- | --- |
| 项目与日程 | 项目范围、采购方式、关键日期、期限起算点 |
| 技术需求与验收 | 技术标准、交付物、验收方法、可量化性 |
| 资格与实质性条件 | 主体资格、业绩、禁止性条件、证明材料 |
| 商务报价与付款 | 预算限价、税率口径、报价组成、付款条件 |
| 附件与引用 | 附件齐备、引用唯一、正文与附件一致 |
| 评审办法与评分 | 分值合计、评分量化、否决条件、评审一致性 |
| 合同履约与责任 | 服务期、违约责任、验收付款衔接、风险分配 |

每类状态只能是`reviewed`或`evidence_insufficient`；每个法规风险候选必须同时具有具体法规条款和采购文件事实。

## 四类审查

### 完整性与明确性

- 检查七类主题、必备章节、附件、表格和引用目标是否覆盖；
- 识别主体、动作、条件、标准、责任、时限或证明材料不清；
- 缺项只能输出候选问题，必须说明检查范围，并标记人工确认。

### 内部一致性

- 对照日期、金额、比例、评分、资格、技术参数、付款、验收和合同条款；
- 保留两侧原文证据，不自行选择哪一处为正确版本；
- 不把同义改写误判为冲突，重点核验数值、否定词、例外和责任方向。

### 可执行性

- 识别无法验收、无法计价、责任主体缺失、触发条件不明或期限起点缺失；
- 区分“表达可以优化”和“实际无法履行”，避免泛化措辞问题。

### 法规与规则风险

- `all_eligible_laws`模式下，输入法规均视为有效且适用于本次审查，不得再次输出法规效力或适用性待确认；引用法规的问题使用`legal_applicability=applicable`；
- `applicability_gate`模式下，只有已确认适用的法规可支撑正式候选问题，其他状态按门禁结果处理；
- 法规原文与内部执行规则分开引用，不把内部规则冒充法律；
- 不使用“违法”“必然无效”等确定措辞，除非输入含已确认的适用规则且证据充分。

## 证据要求

- `evidence_quote`必须是对应采购Block的连续原文；
- 法规依据必须引用具体条、款、项，不得只引用法规名称；
- 问题涉及两处矛盾时至少绑定两侧Block；
- 目录、页眉页脚和模型总结不能单独作为问题证据；
- 找不到证据时输出`evidence_insufficient`，不得补造条款或页码。

## 总体结论

总体结论必须说明：

- 是否发现高、中、低风险候选问题；
- 是否存在解析、附件或证据不足；仅在`applicability_gate`模式下说明法规适用性状态；
- 结论是AI初审意见，需业务经办和采购监督确认；
- 不因“未发现问题”宣称文件绝对合规。
- 解析、OCR和分批提取失败只进入`system_warnings`，不得进入`findings`或业务风险数量。

## 整改复审

整改模式下逐项读取原问题、旧证据、新版候选条款和原规则快照。只建议`rectified`、`partially_rectified`、`not_rectified`或`unable_to_determine`，同时扫描整改引入的新风险；不得直接核销问题或锁定终版。
