# 采购候选事项输出契约

## 目录

1. 顶层结构
2. 候选事项
3. 分类与类型
4. 证据与关系
5. 覆盖报告
6. 输出示例

## 1. 顶层结构

```json
{
  "skill": "understand-procurement-document",
  "skill_version": "1.0.0",
  "document_id": "DOC-001",
  "document_version_id": "DV-001",
  "structure_profile_id": "PROFILE-DV-001",
  "candidate_items": [],
  "coverage": [],
  "rejected_items": [],
  "unresolved_references": [],
  "warnings": []
}
```

## 2. 候选事项

```json
{
  "candidate_id": "CAND-0001",
  "primary_category": "技术需求与验收",
  "category_tags": ["技术需求与验收", "合同履约与责任"],
  "requirement_type": "delivery_deadline",
  "statement": "中标供应商应在合同签订后30日内完成交付",
  "subject": "中标供应商",
  "action": "完成交付",
  "object": "采购标的",
  "condition": "合同签订后",
  "source_value": "30日内",
  "normalized_value": {"value": 30, "unit": "day", "operator": "within"},
  "mandatory_signal": "explicit_mandatory",
  "response_materials": [],
  "evidence_block_ids": ["procurement:B-0128"],
  "evidence_quote": "中标供应商应在合同签订后30日内完成交付",
  "heading_path": ["第五章 技术需求", "5.2 交付要求"],
  "page_no": 32,
  "relations": [],
  "confidence": 0.98
}
```

`evidence_quote`必须是某个`evidence_block_ids`对应原文的连续子串。

## 3. 分类与类型

`primary_category`只允许：

```text
项目与日程
资格与实质性条件
技术需求与验收
评审办法与评分
商务报价与付款
合同履约与责任
附件与引用
```

`requirement_type`使用`backend/app/review_engine/topic_vocabulary.json`中的稳定英文键。采购侧常用键例如：

```text
project_budget
submission_deadline
qualification
mandatory_rejection
technical_parameter
service_period
delivery_deadline
delivery_location
acceptance_standard
evaluation_score
price_ceiling
payment_term
bid_bond
warranty
breach_liability
response_material
```

未知类型使用`other`并在`attributes.original_requirement_type`中保留模型原值，不临时创造不受控枚举。进入`source_assertion`后同时保存受控`topics`；法规单元使用同一词表，二者只能按词表键连接。

## 4. 证据与关系

关系字段：

```json
{
  "relation_type": "possible_duplicate",
  "target_candidate_id": "CAND-0038",
  "reason": "资格章节与评审表出现相同资质要求",
  "evidence_block_ids": ["procurement:B-0042", "procurement:B-0310"]
}
```

`relation_type`允许：`possible_duplicate`、`cross_reference`、`scoring_link`、`contract_link`、`possible_conflict`和`exception_to`。

不要在本Skill中合并或删除关联候选。

## 5. 覆盖报告

```json
{
  "category": "评审办法与评分",
  "scanned_section_ids": ["SEC-030"],
  "scanned_table_block_ids": ["procurement:B-0310"],
  "candidate_count": 26,
  "status": "covered",
  "notes": []
}
```

`status`允许`covered`、`empty_after_scan`、`not_applicable`和`needs_confirmation`。`empty_after_scan`只表示没有提取到候选，不能直接认定文件缺项。

## 6. 输出示例

```json
{
  "skill": "understand-procurement-document",
  "skill_version": "1.0.0",
  "document_id": "DOC-001",
  "document_version_id": "DV-001",
  "structure_profile_id": "PROFILE-DV-001",
  "candidate_items": [
    {
      "candidate_id": "CAND-0001",
      "primary_category": "商务报价与付款",
      "category_tags": ["商务报价与付款"],
      "requirement_type": "price_ceiling",
      "statement": "本项目最高限价为100万元",
      "subject": "供应商",
      "action": "报价不得超过最高限价",
      "object": "响应报价",
      "condition": null,
      "source_value": "100万元",
      "normalized_value": {"value": 1000000, "unit": "CNY", "operator": "lte"},
      "mandatory_signal": "explicit_mandatory",
      "response_materials": ["报价表"],
      "evidence_block_ids": ["procurement:B-0068"],
      "evidence_quote": "本项目最高限价为100万元",
      "heading_path": ["第二章 投标人须知"],
      "page_no": 12,
      "relations": [],
      "confidence": 0.99
    }
  ],
  "coverage": [],
  "rejected_items": [],
  "unresolved_references": [],
  "warnings": []
}
```
