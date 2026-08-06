---
name: review-procurement-document
description: Review a procurement, tender, inquiry, or bidding document for completeness, clarity, internal consistency, enforceability, and potential regulatory compliance risks using its whole-document profile, procurement ledger, deterministic checks, candidate rules, legal units, and traceable source Blocks. Use after document understanding has produced a procurement ledger, including first review and rectification re-review. Produce evidence-backed candidate findings for human confirmation; never issue an unsupported final legal conclusion.
---

# 采购文件审查

## 输入前提

仅在以下输入版本一致时开始审查：

- 解析质量报告、全文结构画像和采购要求台账；
- 七类采购主题视图及全文确定性检查结果；
- 候选法规条款、可执行规则及其效力元数据；
- 可定位的采购文件Block和法规条款单元。

解析质量阻断、台账与原文版本不一致或证据不可定位时，停止相应结论并标记人工确认。输出字段和枚举见[审查结论契约](references/review-finding-contract.md)。

## 审查流程

1. 读取全文画像和七类主题覆盖，先判断文档类型、采购方式及章节职责。
2. 处理确定性检查发现的编号、日期、金额、评分合计、引用和必备章节异常。
3. 按台账事项核验完整性、明确性、内部一致性和可执行性，不以单个Block替代全文判断。
4. 对同一事项跨公告、须知、技术需求、评审办法、合同范本和附件进行对照。
5. 仅在涉及程序、资格、禁止性、评分或法定时限时使用法规候选；先判断法规效力和项目适用性。
6. 为每个问题绑定采购文件`evidence_block_ids`；引用法规时同时绑定`legal_unit_ids`。
7. 运行证据校验。无法回到采购文件原文的问题不得标记为已验证。
8. 汇总总体结论，区分已发现问题、证据不足、法规待确认和未覆盖范围。

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

- 法规召回只是候选依据，不等于适用；
- `status=unknown`、缺少生效日期或项目适用条件不明时，只能输出`potential`；
- 适用性需结合审查日期、采购方式、项目类型、强制招标范围和例外；
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
- 是否存在解析、附件、法规效力或证据不足；
- 结论是AI初审意见，需业务经办和采购监督确认；
- 不因“未发现问题”宣称文件绝对合规。

## 整改复审

整改模式下逐项读取原问题、旧证据、新版候选条款和原规则快照。只建议`rectified`、`partially_rectified`、`not_rectified`或`unable_to_determine`，同时扫描整改引入的新风险；不得直接核销问题或锁定终版。
