from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from app.integrations.docx import Docx2PythonService


def test_docx2python_builds_unified_blocks(tmp_path: Path) -> None:
    source = tmp_path / "采购文件.docx"
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
                xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <w:body>
        <w:p><w:r><w:t>第一条 采购要求</w:t></w:r></w:p>
        <w:tbl>
          <w:tr><w:tc><w:p><w:r><w:t>项目</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>要求</w:t></w:r></w:p></w:tc></w:tr>
          <w:tr><w:tc><w:p><w:r><w:t>交付</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>按期完成</w:t></w:r></w:p></w:tc></w:tr>
        </w:tbl>
      </w:body>
    </w:document>"""
    with ZipFile(source, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8"?>
        <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
          <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
          <Default Extension="xml" ContentType="application/xml"/>
          <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
        </Types>""")
        archive.writestr("_rels/.rels", """<?xml version="1.0" encoding="UTF-8"?>
        <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
          <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
        </Relationships>""")
        archive.writestr("word/document.xml", document_xml)

    document = Docx2PythonService().parse(source, tmp_path / "out", "procurement")

    assert document["parser"]["name"] == "docx2python"
    assert [block["block_type"] for block in document["blocks"]] == ["paragraph", "table"]
    assert document["blocks"][0]["block_id"] == "procurement:B-00001"
    assert "交付 | 按期完成" in document["blocks"][1]["text"]
