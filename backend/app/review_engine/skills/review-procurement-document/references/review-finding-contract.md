# 采购文件审查结论契约

## 顶层结构

```json
{
  "skill": "review-procurement-document",
  "skill_version": "1.0.0",
  "overall_conclusion": "AI初审总体意见",
  "coverage_summary": [],
  "findings": [],
  "system_warnings": [],
  "unresolved": [],
  "warnings": []
}
```

## 问题字段

```json
{
  "source_candidate_ids": ["CND-001"],
  "finding_type": "inconsistency",
  "risk_level": "high",
  "title": "响应截止时间前后不一致",
  "description": "公告与供应商须知载明不同截止时间",
  "ledger_item_ids": ["REQ-001"],
  "evidence_block_ids": ["procurement:B-0012", "procurement:B-0088"],
  "evidence_quotes": ["……", "……"],
  "rule_ids": [],
  "legal_unit_ids": [],
  "legal_applicability": "not_assessed",
  "rationale": "两个Block描述同一事件但日期不同",
  "recommendation": "统一截止时间并同步修改相关章节",
  "confidence": 0.95,
  "needs_human_confirmation": true
}
```

`finding_type`允许：

```text
missing_element
ambiguity
inconsistency
reference_issue
unenforceable
legal_risk
rule_violation
evidence_insufficient
```

`risk_level`允许：

- `high`、`medium`、`low`：原文可识别、证据可定位，系统已经形成候选风险等级；
- `pending`：原文可识别，但证据充分性或风险定级仍需人工复核；
- `unknown`：仅用于 OCR、解析质量、附件缺失或采购文件识别质量不足，系统无法可靠判断。

不得把非法枚举或一般性的“待确认”转换成 `unknown`。

`legal_applicability`允许：

```text
not_assessed
applicable
potential
not_applicable
insufficient_metadata
```

## 输出硬约束

- 每个非缺项问题至少包含一个真实采购文件Block；矛盾问题至少包含两侧证据。
- `legal_risk`必须包含具体`legal_unit_ids`，否则降级为`evidence_insufficient`。
- `legal_context.mode=all_eligible_laws`时，输入法规均已通过`effective`准入并视为适用：引用法规的问题使用`applicable`，未引用法规使用`not_assessed`，不得输出法规效力或适用性待确认问题。
- 只有`legal_context.mode=applicability_gate`时，才允许使用`potential`或`insufficient_metadata`表达门禁尚未确认。
- 不返回审批、核销、终版锁定或正式法律定性。
- `findings`只能合并输入`review_candidates`，每项必须含有效`source_candidate_ids`。
- `parse_quality`、OCR和提取失败不得进入`findings`，统一进入独立的`system_warnings`。
- `coverage_summary`必须覆盖七类采购主题，每类标记`reviewed`或`evidence_insufficient`。
