---
name: extract-legal-applicability-profile
description: Extract an evidence-bound applicability profile from preselected Chinese legal or policy document units. Use after deterministic parsing and candidate-unit selection when the system needs consistent applicability activities, subjects, phases, trigger conditions, project types, exclusions, and precedence rules for administrator confirmation. Do not use for file parsing, legal validity verification, project-to-law matching, or final legal conclusions.
---

# 法规适用条件提取

只从输入的候选法规单元提取文档层面的适用范围候选。不得补充常识、判断法规有效性、判断具体项目是否适用，或给出最终法律结论。

## 输入

读取 `units` 数组中的：

- `legal_unit_id`
- `article_no`
- `chapter`
- `text`
- `parent_context`

输入已经由后端筛选。不要要求整份文档，也不要处理上传、解析、分批、存储或权限。

## 提取步骤

1. 优先识别总则、定义、适用范围、例外、附则和优先适用条款。
2. 仅保留能影响文档适用边界的内容，不枚举每项程序义务、期限、处罚或业务动作。
3. 合并同义项；每个分类最多 8 项。
4. 每项绑定至少一个输入单元，并逐字引用连续原文。
5. 无法用输入原文证明的内容不输出；存在歧义时宁可留空。

## 字段口径

- `activities`：法规整体规范的活动类型，不是每个条款中的动作。
- `subjects`：主体身份会影响法规适用时才提取，不罗列所有参与者。
- `business_phases`：采购、招标投标或合同生命周期中的宽泛阶段。
- `trigger_conditions`：触发法规或特定适用范围的事实条件。
- `project_types`：工程、货物、服务等被规范对象。
- `exclusions`：明确的不适用、除外或例外情形。
- `precedence_rules`：另有规定、优先适用或特别规则关系。

## 输出契约

只返回严格 JSON，不要使用 Markdown：

```json
{
  "applicability": {
    "activities": [],
    "subjects": [],
    "business_phases": [],
    "trigger_conditions": [],
    "project_types": [],
    "exclusions": [],
    "precedence_rules": []
  }
}
```

每一项格式固定为：

```json
{
  "value": "输入原文中可以直接定位的简短表述",
  "evidence": [
    {
      "legal_unit_id": "输入中的单元ID",
      "quote": "对应单元中的连续原文"
    }
  ]
}
```

`value` 和 `quote` 都必须能在对应法规单元原文中找到。不要输出置信度、摘要、说明文字或契约之外的字段；摘要由后端根据通过证据校验的结构化结果生成。
