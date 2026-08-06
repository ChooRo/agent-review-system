---
name: understand-document-structure
description: Analyze MinerU-derived Document JSON for procurement, response, and contract files; build a traceable document outline, section responsibilities, party and term glossary, table and attachment inventory, cross-reference graph, and global constraints. Use before requirement extraction, response mapping, contract comparison, or any review that needs whole-document context. Do not use for legal conclusions or business compliance judgments.
---

# 文档结构理解

## 目标

把按阅读顺序排列的原子Block组织为可复用的全文结构画像。保持原文事实和Block证据，不提取采购要求，不作合规、响应或合同一致性判断。

## 输入前提

接收包含以下字段的Document JSON：

- `document_id`、`document_version_id`或`version_id`、`document_role`；
- `blocks[].block_id`、`block_type`、`text`、`heading_path`、`page_no`、`bbox`、`reading_order`；
- 可选表格、图片和附件元数据；
- 解析质量报告。

解析状态为`reparse_recommended`时停止自动理解并返回阻断结果。状态为`manual_review`时继续处理，但在输出中保留质量告警。

需要生成或校验完整字段时，读取[输出契约](references/output-contract.md)。

## 工作流

1. 按`reading_order`恢复全文顺序，不按向量相似度重排原文。
2. 根据标题Block、编号模式和已有`heading_path`建立章节树；不因标题缺号而删除正文。
3. 识别每个章节的职责，例如公告、须知、资格、技术需求、评审办法、合同范本、报价及附件格式。
4. 识别文件中的采购人、代理机构、供应商、投标人、中标人、甲乙方等角色及同义称呼。
5. 提取文内定义、缩写和关键术语，只记录原文能够支持的含义。
6. 建立正文、表格、图片、附件及跨章节引用关系；区分`resolved`、`unresolved`和`ambiguous`。
7. 汇总日期、金额、评分总分、期限等全局约束，但不判断其是否合法。
8. 对超长文件先生成章节级画像，再合并全文画像；第二遍只处理未解析引用、术语冲突和全局约束。
9. 输出结构画像、质量告警和待确认事项，并为每个事实保留`block_ids`。

## 硬约束

- 不补造目录、附件、定义、角色或数值。
- 不把页眉、页脚、页码和目录重复项当作新的业务章节。
- 不用一段全文摘要替代章节树、引用关系和证据ID。
- 不输出“违法”“不合规”“有效响应”或“合同不一致”等审查结论。
- 不修改Block原文、页码、坐标或阅读顺序。
- 无法定位到真实Block的结构事实标记为`unverified`，不得伪造证据。
- 同一引用存在多个候选目标时标记`ambiguous`，不得擅自选择。

## 长文档处理

按完整章节分批。每批输入必须包含：

```text
文档角色
已有全局画像摘要
当前章节路径
当前章节原始Block
相关定义和引用目标Block
```

只压缩全局背景，不改写用于取证的Block原文。超长表格可运行时分片，但所有分片继续引用同一个原始`block_id`和字符区间。

## 输出检查

提交结果前确认：

- 章节树中的节点至少有一个真实Block；
- 角色、术语、约束和引用均携带证据Block；
- 所有`resolved`引用的目标Block真实存在；
- 输出不包含采购事项台账或审查结论；
- 质量告警和未解析关系没有被静默丢弃。
