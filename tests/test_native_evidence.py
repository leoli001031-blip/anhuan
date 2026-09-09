"""Independent expected text/locations and adversarial native package cases."""
from __future__ import annotations

import hashlib
import io
import unittest
import uuid
import warnings
import zipfile
from dataclasses import replace

from platform_foundation.f1.features.evidence.contracts import (
    DocxBlockLocator, FragmentIdentity, ImageLocator, PdfPageLocator,
    XlsxCellsLocator, format_location, parse_locator,
)
from platform_foundation.f1.features.evidence.docx_native import (
    CT, MAIN_TYPE, O, R, W, DocxExtractionError, ExtractionLimits, extract_docx,
)


def package(body: str, *, parts=None, document=None, relationships=None, duplicates=()):
    # Semantic-style fixtures must also have valid OPC identity; raw malformed
    # package tests can override either part explicitly below.
    style_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml'
    style_ct = f'<Override PartName="/word/styles.xml" ContentType="{style_type}"/>' if parts and 'word/styles.xml' in parts else ''
    entries = {
        "[Content_Types].xml": f'<Types xmlns="{CT}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="{MAIN_TYPE}"/>{style_ct}</Types>',
        "_rels/.rels": relationships or f'<Relationships xmlns="{R}"><Relationship Id="rId1" Type="{O}officeDocument" Target="word/document.xml"/></Relationships>',
        "word/document.xml": document or f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>',
        **(parts or {}),
    }
    if style_ct:
        entries.setdefault('word/_rels/document.xml.rels', f'<Relationships xmlns="{R}"><Relationship Id="styles" Type="{O}styles" Target="styles.xml"/></Relationships>')
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, value in [*entries.items(), *duplicates]:
            archive.writestr(name, value)
    return output.getvalue()


def p(text):
    return f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>'


def extract(source, **kwargs):
    return extract_docx(source, expected_sha256=hashlib.sha256(source).hexdigest(), **kwargs)


class LocatorContracts(unittest.TestCase):
    def test_non_pdf_locations_never_publish_page_numbers(self):
        locators = [DocxBlockLocator(3), XlsxCellsLocator(7, "危废台账", "xl/worksheets/sheet9.xml", "C5:F8"), ImageLocator(100, 200, 200, 100, "a" * 64, 6)]
        for location in locators:
            self.assertNotIn("page_number", location.to_dict())
            self.assertNotIn("页", format_location(location))
            self.assertEqual(parse_locator(location.to_dict()), location)
        self.assertEqual(format_location(PdfPageLocator(7)), "第 7 页")

    def test_locator_rejects_false_integer_extra_fields_and_noncanonical_ranges(self):
        for invalid in [True, 1.0, 0, -1]:
            with self.assertRaises(ValueError):
                PdfPageLocator(invalid)
        with self.assertRaises(ValueError):
            parse_locator({"schema_version": 2, "kind": "docx_block", "body_index": 1, "page_number": 1})
        for invalid in ["A0", "A1:A1", "B2:A3", "C3:D2", "a1", "XFE1", "A1048577"]:
            with self.assertRaises(ValueError):
                XlsxCellsLocator(1, "台账", "xl/worksheets/sheet1.xml", invalid)

    def test_locator_rejects_invalid_sheet_names_and_impossible_table_columns(self):
        for name in ["A/B", "[Sheet]", "'Sheet", "Sheet'", "A\nB"]:
            with self.assertRaises(ValueError):
                XlsxCellsLocator(1, name, "xl/worksheets/sheet1.xml", "C5")
        with self.assertRaises(ValueError):
            DocxBlockLocator(1, 1, 2, 1, 1, 1)
        self.assertIn("第 3 列", format_location(DocxBlockLocator(1, 1, 2, 3, 1, 1)))

    def test_identity_binds_all_source_and_locator_dimensions(self):
        value = FragmentIdentity(*(uuid.uuid4() for _ in range(5)), "a" * 64,
            "docx-native-1", 1, DocxBlockLocator(1), 0, "b" * 64)
        changes = {name: uuid.uuid4() for name in ("enterprise_id", "knowledge_scope_id", "document_record_id", "document_version_id", "extraction_revision_id")}
        changes.update(source_sha256="c" * 64, parser_version="docx-native-2", extraction_contract=2,
            locator=DocxBlockLocator(2), ordinal=1, body_sha256="d" * 64)
        for name, change in changes.items():
            other = replace(value, **{name: change})
            self.assertNotEqual(value.id, other.id, name)
            self.assertNotEqual(value.aad(), other.aad(), name)
        self.assertTrue(value.aad().startswith(b"anhuan.material.evidence.v2\x00"))


class NativeDocxContracts(unittest.TestCase):
    def assert_partial(self, source, reason):
        result = extract(source)
        self.assertEqual(result.coverage_state, "partial")
        self.assertFalse(result.report_source_eligible)
        self.assertEqual(result.blocks, ())
        self.assertIn(reason, {debt.reason_code for debt in result.debts})
        return result

    def test_order_text_tokens_empty_blocks_and_horizontal_grid_span(self):
        first = '<w:p><w:r><w:t>编号</w:t><w:tab/><w:t>A-01</w:t><w:br/><w:t>浓度</w:t></w:r><w:r><w:t xml:space="preserve"> 10 mg/m³</w:t><w:cr/><w:t>合格</w:t></w:r></w:p>'
        table = '<w:tbl><w:tblGrid><w:gridCol/><w:gridCol/><w:gridCol/></w:tblGrid><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr>' + p("跨两列") + p("第二段") + '</w:tc><w:tc>' + p("第三列") + '</w:tc></w:tr></w:tbl>'
        result = extract(package(first + '<w:p/>' + table + p("结束")))
        self.assertTrue(result.report_source_eligible)
        self.assertEqual(result.coverage_state, "complete")
        self.assertEqual([block.text for block in result.blocks], ["编号\tA-01\n浓度 10 mg/m³\n合格", "", "跨两列", "第二段", "第三列", "结束"])
        self.assertEqual(result.blocks[4].locator, DocxBlockLocator(3, 1, 2, 3, 1, 1))
        self.assertEqual(result.blocks[-1].locator, DocxBlockLocator(4))
        self.assertEqual(result.expected_block_count, 6)
        self.assertEqual(result.processed_block_count, 6)
        self.assertNotIn("浓度", repr(result))

    def test_complete_blank_document_is_not_a_report_source(self):
        result = extract(package('<w:p/>'))
        self.assertEqual(result.coverage_state, "complete")
        self.assertFalse(result.report_source_eligible)

    def test_visibility_in_unused_or_inherited_styles_is_conservative(self):
        for hidden in ['<w:vanish/>', '<w:vanish w:val="false"/>', '<w:webHidden/>']:
            self.assert_partial(package(p("秘密"), parts={"word/styles.xml": f'<w:styles xmlns:w="{W}"><w:style w:type="paragraph"><w:rPr>{hidden}</w:rPr></w:style></w:styles>'}), "DOCX_VISIBILITY_UNRESOLVED")
        self.assert_partial(package('<w:p><w:r><w:rPr><w:vanish/></w:rPr><w:t>秘密</w:t></w:r></w:p>'), "DOCX_VISIBILITY_UNRESOLVED")

    def test_direct_colors_and_shading_require_visibility_resolution(self):
        # Even apparently neutral values depend on the effective background/theme.
        for properties in [
            '<w:color w:val="FFFFFF"/><w:shd w:val="clear" w:fill="FFFFFF"/>',
            '<w:color w:val="000000"/>', '<w:color w:val="auto"/>',
            '<w:color w:themeColor="background1" w:themeTint="FF"/>',
            '<w:shd w:val="clear" w:fill="auto"/>',
            '<w:shd w:val="nil"/>',
            '<w:shd w:val="clear" w:themeFill="background1"/>',
        ]:
            with self.subTest(properties=properties):
                result = self.assert_partial(package('<w:p><w:r><w:rPr>' + properties + '</w:rPr><w:t>数值 123</w:t></w:r></w:p>'), "DOCX_VISIBILITY_UNRESOLVED")
                self.assertEqual((result.expected_block_count, result.processed_block_count), (1, 1))
                self.assertTrue(any(debt.part == "word/document.xml" for debt in result.debts))

    def test_paragraph_table_and_cell_backgrounds_require_visibility_resolution(self):
        shade = '<w:shd w:val="clear" w:fill="FFFFFF"/>'
        for body in [
            '<w:p><w:pPr>' + shade + '</w:pPr><w:r><w:t>段落</w:t></w:r></w:p>',
            '<w:p><w:pPr><w:rPr><w:color w:val="auto"/></w:rPr></w:pPr><w:r><w:t>段落标记</w:t></w:r></w:p>',
            '<w:tbl><w:tblPr>' + shade + '</w:tblPr><w:tr><w:tc>' + p("表格") + '</w:tc></w:tr></w:tbl>',
            '<w:tbl><w:tr><w:tc><w:tcPr>' + shade + '</w:tcPr>' + p("单元格") + '</w:tc></w:tr></w:tbl>',
        ]:
            with self.subTest(body=body):
                self.assert_partial(package(body), "DOCX_VISIBILITY_UNRESOLVED")

    def test_document_defaults_color_and_shading_require_visibility_resolution(self):
        for defaults in [
            '<w:rPrDefault><w:rPr><w:color w:val="FFFFFF"/></w:rPr></w:rPrDefault>',
            '<w:pPrDefault><w:pPr><w:shd w:val="clear" w:fill="FFFFFF"/></w:pPr></w:pPrDefault>',
        ]:
            styles = f'<w:styles xmlns:w="{W}"><w:docDefaults>{defaults}</w:docDefaults></w:styles>'
            result = self.assert_partial(package(p("默认属性正文"), parts={"word/styles.xml": styles}), "DOCX_VISIBILITY_UNRESOLVED")
            self.assertTrue(any(debt.part == "word/styles.xml" and "docDefaults" in debt.path for debt in result.debts))

    def test_selected_and_default_style_chains_require_visibility_resolution(self):
        base = '<w:style w:styleId="Base" w:type="paragraph"><w:rPr><w:color w:val="FFFFFF"/></w:rPr><w:pPr><w:shd w:val="clear" w:fill="FFFFFF"/></w:pPr></w:style>'
        for default, body in [
            ('w:default="1"', p("默认样式继承")),
            ('', '<w:p><w:pPr><w:pStyle w:val="Selected"/></w:pPr><w:r><w:t>显式样式继承</w:t></w:r></w:p>'),
        ]:
            styles = f'<w:styles xmlns:w="{W}"><w:style w:styleId="Selected" w:type="paragraph" {default}><w:basedOn w:val="Middle"/></w:style><w:style w:styleId="Middle" w:type="paragraph"><w:basedOn w:val="Base"/></w:style>{base}</w:styles>'
            result = self.assert_partial(package(body, parts={"word/styles.xml": styles}), "DOCX_VISIBILITY_UNRESOLVED")
            self.assertTrue(any(debt.part == "word/styles.xml" for debt in result.debts))

    def test_theme_foreground_and_cell_background_combination_is_partial(self):
        styles = f'<w:styles xmlns:w="{W}"><w:style w:styleId="ThemeText" w:type="character"><w:rPr><w:color w:themeColor="background1"/></w:rPr></w:style></w:styles>'
        body = '<w:tbl><w:tr><w:tc><w:tcPr><w:shd w:val="clear" w:themeFill="background1"/></w:tcPr><w:p><w:r><w:rPr><w:rStyle w:val="ThemeText"/></w:rPr><w:t>同主题色</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
        self.assert_partial(package(body, parts={"word/styles.xml": styles}), "DOCX_VISIBILITY_UNRESOLVED")

    def test_unused_style_color_and_shading_do_not_reject_plain_text(self):
        styles = f'<w:styles xmlns:w="{W}"><w:style w:styleId="Normal" w:type="paragraph" w:default="1"/><w:style w:styleId="Unused" w:type="paragraph"><w:basedOn w:val="UnusedBase"/></w:style><w:style w:styleId="UnusedBase" w:type="paragraph"><w:rPr><w:color w:val="FFFFFF"/></w:rPr><w:pPr><w:shd w:val="clear" w:fill="FFFFFF"/></w:pPr></w:style></w:styles>'
        result = extract(package(p("普通正文"), parts={"word/styles.xml": styles}))
        self.assertEqual(result.coverage_state, "complete")
        self.assertTrue(result.report_source_eligible)
        self.assertEqual([block.text for block in result.blocks], ["普通正文"])
        selected = '<w:p><w:pPr><w:pStyle w:val="Unused"/></w:pPr><w:r><w:t>现在使用该样式</w:t></w:r></w:p>'
        self.assert_partial(package(selected, parts={"word/styles.xml": styles}), "DOCX_VISIBILITY_UNRESOLVED")

    def test_direct_w14_transparent_text_remains_partial(self):
        body = '<w:p><w:r><w:rPr><w14:textFill xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"><w14:srgbClr w14:val="FFFFFF"><w14:alpha w14:val="0"/></w14:srgbClr></w14:textFill></w:rPr><w:t>透明字</w:t></w:r></w:p>'
        self.assert_partial(package(body), "DOCX_MARKUP_UNSUPPORTED")

    def test_revision_families_never_mix_old_and_new_values(self):
        for markup in ['<w:ins/>', '<w:del/>', '<w:delText>旧值</w:delText>', '<w:pPrChange/>', '<w:moveFromRangeStart/>', '<w:cellIns/>']:
            self.assert_partial(package(p("普通值") + '<w:p>' + markup + '</w:p>'), "DOCX_REVISION_UNRESOLVED")

    def test_cross_paragraph_field_cache_is_not_plain_evidence(self):
        for field in ['<w:fldSimple w:instr="DATE">' + p("缓存日期") + '</w:fldSimple>', '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r></w:p>' + p("跨段缓存")]:
            self.assert_partial(package(p("正文") + field), "DOCX_FIELD_UNRESOLVED")

    def test_unsupported_grid_or_nested_table_is_partial(self):
        for entry in ['<w:tcPr><w:vMerge/></w:tcPr>', '<w:tcPr><w:hMerge/></w:tcPr>', '<w:tbl/>']:
            self.assertFalse(extract(package('<w:tbl><w:tr><w:tc>' + entry + p("值") + '</w:tc></w:tr></w:tbl>')).report_source_eligible)
        self.assert_partial(package('<w:tbl><w:tr><w:trPr><w:gridBefore w:val="2"/></w:trPr><w:tc>' + p("值") + '</w:tc></w:tr></w:tbl>'), "DOCX_TABLE_GRID_UNSUPPORTED")

    def test_late_drawings_numbering_and_unknown_containers_are_not_lost(self):
        for late, reason in [('<w:p><w:r><w:drawing/></w:r></w:p>', "DOCX_NON_TEXT_CONTENT"), ('<w:p><w:pPr><w:numPr/></w:pPr></w:p>', "DOCX_NUMBERING_UNRESOLVED"), ('<w:unknown>' + p("隐藏在新容器") + '</w:unknown>', "DOCX_MARKUP_UNSUPPORTED"), ('<w:altChunk/>', "DOCX_ALTCHUNK_UNSUPPORTED")]:
            self.assert_partial(package(p("普通正文" * 3000) + late), reason)

    def test_extra_story_and_external_relationships_are_not_complete(self):
        self.assert_partial(package(p("正文"), parts={"word/header1.xml": f'<w:hdr xmlns:w="{W}">' + p("页眉") + '</w:hdr>'}), "DOCX_PART_UNSUPPORTED")
        rels = f'<Relationships xmlns="{R}"><Relationship Id="x" Type="{O}hyperlink" TargetMode="External" Target="https://example.invalid/"/></Relationships>'
        with self.assertRaisesRegex(DocxExtractionError, "DOCX_EXTERNAL_RELATIONSHIP"):
            extract(package(p("正文"), parts={"word/_rels/document.xml.rels": rels}))

    def test_default_edge_whitespace_and_page_break_are_explicit_debts(self):
        self.assert_partial(package(p(" 前后空白 ")), "DOCX_WHITESPACE_UNRESOLVED")
        self.assert_partial(package('<w:p><w:r><w:br w:type="page"/></w:r></w:p>'), "DOCX_BREAK_UNSUPPORTED")

    def test_direct_character_data_and_metadata_subtrees_cannot_disappear(self):
        for markup in ['<w:p>直接文字</w:p>', '<w:p><w:r>直接文字</w:r></w:p>', '<w:p><w:bookmarkStart>' + p("藏在书签") + '</w:bookmarkStart></w:p>', '<w:p><w:r><w:t w:unsupported="1">值</w:t></w:r></w:p>', '<w:p/>正文尾部文字']:
            self.assert_partial(package(markup), "DOCX_MARKUP_UNSUPPORTED")
        for markup in ['<w:p xmlns:x="urn:unknown" x:semantic="unknown"><w:r><w:t>值</w:t></w:r></w:p>', '<w:p><w:r xmlns:x="urn:unknown" x:semantic="unknown"><w:t>值</w:t></w:r></w:p>']:
            self.assert_partial(package(markup), "DOCX_MARKUP_UNSUPPORTED")

    def test_hidden_style_relationship_cannot_use_ignored_metadata_part(self):
        rels = f'<Relationships xmlns="{R}"><Relationship Id="style" Type="{O}styles" Target="../docProps/custom.xml"/></Relationships>'
        source = package(p("秘密"), parts={"word/_rels/document.xml.rels": rels,
            "docProps/custom.xml": f'<w:styles xmlns:w="{W}"><w:style w:type="paragraph"><w:rPr><w:vanish/></w:rPr></w:style></w:styles>'})
        self.assert_partial(source, "DOCX_PROFILE_PART_UNSUPPORTED")

    def test_only_effective_numbering_styles_block_plain_documents(self):
        styles = f'<w:styles xmlns:w="{W}"><w:style w:styleId="Normal" w:default="1" w:type="paragraph"/><w:style w:styleId="List" w:type="paragraph"><w:pPr><w:numPr/></w:pPr></w:style></w:styles>'
        self.assertTrue(extract(package(p("普通正文"), parts={"word/styles.xml": styles})).report_source_eligible)
        list_p = '<w:p><w:pPr><w:pStyle w:val="List"/></w:pPr><w:r><w:t>自动编号条目</w:t></w:r></w:p>'
        self.assert_partial(package(list_p, parts={"word/styles.xml": styles}), "DOCX_NUMBERING_UNRESOLVED")
        self.assert_partial(package(list_p), "DOCX_STYLE_UNRESOLVED")

    def test_active_style_cycle_and_inherited_unknown_property_are_partial(self):
        cycle = f'<w:styles xmlns:w="{W}"><w:style w:styleId="A" w:default="1" w:type="paragraph"><w:basedOn w:val="B"/></w:style><w:style w:styleId="B" w:type="paragraph"><w:basedOn w:val="A"/></w:style></w:styles>'
        self.assert_partial(package(p("正文"), parts={"word/styles.xml": cycle}), "DOCX_STYLE_UNRESOLVED")
        unknown = f'<w:styles xmlns:w="{W}"><w:style w:styleId="A" w:default="1" w:type="paragraph"><w:basedOn w:val="B"/></w:style><w:style w:styleId="B" w:type="paragraph"><w:rPr><w:rtl/></w:rPr></w:style></w:styles>'
        result = self.assert_partial(package(p("正文"), parts={"word/styles.xml": unknown}), "DOCX_MARKUP_UNSUPPORTED")
        self.assertTrue(any(debt.part == "word/styles.xml" for debt in result.debts))

    def test_active_conditional_table_style_cannot_hide_unsupported_direction(self):
        styles = f'<w:styles xmlns:w="{W}"><w:style w:styleId="Conditional" w:type="table"><w:tblStylePr w:type="firstRow"><w:rPr><w:rtl/></w:rPr></w:tblStylePr></w:style></w:styles>'
        body = '<w:tbl><w:tblPr><w:tblStyle w:val="Conditional"/><w:tblLook w:firstRow="1"/></w:tblPr><w:tr><w:tc>' + p("ABC 123") + '</w:tc></w:tr></w:tbl>'
        self.assert_partial(package(body, parts={"word/styles.xml": styles}), "DOCX_STYLE_CONTENT_UNSUPPORTED")

    def test_unknown_property_attributes_and_must_understand_are_partial(self):
        for attribute in ['xmlns:x="urn:unknown" x:semantic="unknown"', 'w:semantic="unknown"', 'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:x="urn:unknown" mc:MustUnderstand="x"']:
            markup = f'<w:p><w:r><w:rPr><w:color w:val="000000" {attribute}/></w:rPr><w:t>值</w:t></w:r></w:p>'
            self.assert_partial(package(markup), "DOCX_MARKUP_UNSUPPORTED")

    def test_xml_dtd_and_entities_rejected_in_utf8_and_utf16(self):
        for encoding in ("UTF-8", "UTF-16"):
            doc = f'<?xml version="1.0" encoding="{encoding}"?><!DOCTYPE document [<!ENTITY secret "EXPANDED">]><w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>&secret;</w:t></w:r></w:p></w:body></w:document>'
            with self.assertRaisesRegex(DocxExtractionError, "DOCX_XML_UNSAFE_OR_CORRUPT"):
                extract(package("", document=doc.encode(encoding)))

    def test_duplicate_zip_entries_rejected_before_named_reads(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            source = package(p("第一份"), duplicates=[("word/document.xml", "<different/>")])
        with self.assertRaisesRegex(DocxExtractionError, "DOCX_DUPLICATE_PART"):
            extract(source)

    def test_main_part_identity_and_namespace_are_checked(self):
        wrong = f'<Relationships xmlns="{R}"><Relationship Id="x" Type="{O}officeDocument" Target="word/other.xml"/></Relationships>'
        with self.assertRaisesRegex(DocxExtractionError, "DOCX_PROFILE_UNSUPPORTED"):
            extract(package(p("真实"), relationships=wrong, parts={"word/other.xml": "<other/>"}))
        with self.assertRaisesRegex(DocxExtractionError, "DOCX_PROFILE_UNSUPPORTED"):
            extract(package("", document='<document><body/></document>'))

    def test_budgets_and_source_hash_fail_without_success_prefix(self):
        source = package(p("abcdef") + p("第二段"))
        for limits, reason in [(ExtractionLimits(characters=3), "DOCX_TEXT_BUDGET_EXCEEDED"), (ExtractionLimits(blocks=1), "DOCX_TEXT_BUDGET_EXCEEDED"), (ExtractionLimits(xml_depth=2), "DOCX_XML_BUDGET_EXCEEDED"), (ExtractionLimits(xml_nodes=3), "DOCX_XML_BUDGET_EXCEEDED"), (ExtractionLimits(entry_bytes=3), "DOCX_PACKAGE_SIZE_LIMIT")]:
            with self.assertRaisesRegex(DocxExtractionError, reason):
                extract(source, limits=limits)
        with self.assertRaisesRegex(DocxExtractionError, "DOCX_SOURCE_SHA_MISMATCH"):
            extract_docx(source, expected_sha256="a" * 64)

    def test_manifest_binds_raw_token_identity_and_original_parts(self):
        one, two = package(p("同样文字")), package('<w:p><w:r><w:t>同样</w:t></w:r><w:r><w:t>文字</w:t></w:r></w:p>')
        first, second = extract(one), extract(two)
        self.assertEqual(first.blocks[0].text, second.blocks[0].text)
        self.assertNotEqual(first.blocks[0].token_sha256, second.blocks[0].token_sha256)
        self.assertNotEqual(first.manifest_sha256, second.manifest_sha256)
        self.assertEqual(first.manifest_sha256, extract(one).manifest_sha256)


if __name__ == "__main__":
    unittest.main()
