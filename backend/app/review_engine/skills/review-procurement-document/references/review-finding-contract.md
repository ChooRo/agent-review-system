# 采购文件审查结论契约

## 顶层结构

```json
{
  "skill": "review-procurement-document",
  "skill_version": "1.0.0",
  "overall_conclusion": "AI初审总体意见",
  "coverage_summary": [],
  "findings": [],
  "unresolved": [],
  "warnings": []
}
```

## 问题字段

```json
{
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
parse_quality
evidence_insufficient
```

`risk_level`允许`high`、`medium`、`low`和`pending`。

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
- 法规效力或适用性未确认时，使用`potential`或`insufficient_metadata`并要求人工确认。
- 不返回审批、核销、终版锁定或正式法律定性。
