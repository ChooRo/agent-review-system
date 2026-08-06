# 文档结构画像输出契约

## 目录

1. 顶层结构
2. 章节节点
3. 角色与术语
4. 引用关系
5. 全局约束
6. 输出示例

## 1. 顶层结构

```json
{
  "skill": "understand-document-structure",
  "skill_version": "1.0.0",
  "document_id": "DOC-001",
  "document_version_id": "DV-001",
  "document_role": "procurement",
  "quality_status": "reviewable",
  "outline": [],
  "section_responsibilities": [],
  "parties": [],
  "terms": [],
  "references": [],
  "global_constraints": [],
  "inventories": {
    "tables": [],
    "images": [],
    "attachments": []
  },
  "warnings": [],
  "unresolved": []
}
```

## 2. 章节节点

```json
{
  "section_id": "SEC-001",
  "title": "第三章 评审办法",
  "level": 1,
  "parent_section_id": null,
  "section_type": "evaluation_method",
  "block_ids": ["procurement:B-0100", "procurement:B-0101"],
  "page_range": [20, 30],
  "confidence": 0.96
}
```

`section_type`可使用：`announcement`、`instructions`、`qualification`、`technical_requirement`、`evaluation_method`、`commercial_pricing`、`contract_template`、`response_format`、`attachment`或`other`。

## 3. 角色与术语

角色：

```json
{
  "canonical_role": "supplier",
  "names": ["供应商", "投标人", "中标人"],
  "scope_note": "中标后称中标人",
  "evidence_block_ids": ["procurement:B-0021"]
}
```

术语：

```json
{
  "term": "实质性响应",
  "definition": "……",
  "evidence_block_ids": ["procurement:B-0045"],
  "status": "explicit"
}
```

`status`允许`explicit`、`contextual`或`conflicting`。

## 4. 引用关系

```json
{
  "reference_text": "详见附件3",
  "source_block_ids": ["procurement:B-0112"],
  "target_block_ids": ["procurement:B-0801"],
  "relation_type": "attachment_reference",
  "status": "resolved",
  "confidence": 0.98
}
```

`status`只允许`resolved`、`unresolved`或`ambiguous`。

## 5. 全局约束

```json
{
  "constraint_type": "submission_deadline",
  "normalized_value": "2026-08-20T09:00:00+08:00",
  "source_value": "2026年8月20日上午9时",
  "evidence_block_ids": ["procurement:B-0038"],
  "confidence": 0.99
}
```

允许记录日期、金额、比例、分值和期限。标准化失败时保留`source_value`，把`normalized_value`设为`null`。

## 6. 输出示例

```json
{
  "skill": "understand-document-structure",
  "skill_version": "1.0.0",
  "document_id": "DOC-001",
  "document_version_id": "DV-001",
  "document_role": "procurement",
  "quality_status": "reviewable",
  "outline": [
    {
      "section_id": "SEC-001",
      "title": "第一章 采购公告",
      "level": 1,
      "parent_section_id": null,
      "section_type": "announcement",
      "block_ids": ["procurement:B-0001"],
      "page_range": [1, 3],
      "confidence": 0.99
    }
  ],
  "section_responsibilities": [],
  "parties": [],
  "terms": [],
  "references": [],
  "global_constraints": [],
  "inventories": {"tables": [], "images": [], "attachments": []},
  "warnings": [],
  "unresolved": []
}
```
