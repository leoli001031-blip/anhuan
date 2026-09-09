"""Preserved ordinary original plus independent semantic mutations.

Expected values are fixed from the synthetic source, not parser projections.
Every mutation retains all original package parts, including unused definitions.
"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path
import unittest
from xml.etree import ElementTree as ET
import zipfile

from platform_foundation.f1.features.evidence.contracts import DocxBlockLocator
from platform_foundation.f1.features.evidence.docx_native import CT, O, R, W
from tests.test_native_evidence import extract

ORIGINAL = Path(__file__).parent / 'fixtures/docx/ordinary-generated.docx'


def rewrite(changes=None, edit=None):
    with zipfile.ZipFile(ORIGINAL) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    if changes:
        for name, transform in changes.items():
            root = ET.fromstring(parts[name])
            transform(root)
            parts[name] = ET.tostring(root, encoding='utf-8')
    if edit:
        edit(parts)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name, raw in parts.items():
            archive.writestr(name, raw)
    return output.getvalue()


def q(name):
    return f'{{{W}}}{name}'


def style(root, sid='Normal'):
    return next(node for node in root if node.get(q('styleId')) == sid)


def child(node, name, **attrs):
    return ET.SubElement(node, q(name), {q(k): value for k, value in attrs.items()})


class OrdinaryDocxCompatibility(unittest.TestCase):
    def partial(self, data, reason=None):
        result = extract(data)
        self.assertEqual(result.coverage_state, 'partial')
        self.assertFalse(result.report_source_eligible)
        self.assertEqual(result.blocks, ())
        self.assertEqual((result.expected_block_count, result.processed_block_count), (5, 5))
        if reason:
            self.assertIn(reason, {d.reason_code for d in result.debts})
        return result

    def test_original_unmodified_bytes_full_text_and_exact_coordinates(self):
        raw = ORIGINAL.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), '9cfcbfedfbc4eb70ba4160064a437e5cd11ed14456509b1f16253d754ee29df2')
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.assertEqual(len(archive.namelist()), 17)
        result = extract(raw)
        self.assertEqual(result.coverage_state, 'complete')
        self.assertTrue(result.report_source_eligible)
        self.assertEqual(result.debts, ())
        self.assertEqual([b.text for b in result.blocks], ['合成巡检记录', '项目', '结果', '标识', '清楚'])
        self.assertEqual([b.locator for b in result.blocks], [DocxBlockLocator(1),
            DocxBlockLocator(2, 1, 1, 1, 1, 1), DocxBlockLocator(2, 1, 2, 2, 1, 1),
            DocxBlockLocator(2, 2, 1, 1, 1, 1), DocxBlockLocator(2, 2, 2, 2, 1, 1)])
        self.assertEqual((result.expected_block_count, result.processed_block_count), (5, 5))
        self.assertEqual(hashlib.sha256(ORIGINAL.read_bytes()).hexdigest(), result.source_sha256)

    def test_two_style_stores_effective_numbering_visibility_and_type_mismatch(self):
        for mutate in [
            lambda root: child(child(style(root), 'pPr'), 'numPr'),
            lambda root: child(child(style(root), 'rPr'), 'vanish'),
            lambda root: child(child(style(root), 'rPr'), 'color', val='FFFFFF'),
            lambda root: style(root).set(q('type'), 'character'),
            lambda root: child(style(root), 'link', val='Missing'),
        ]:
            for part in ('word/styles.xml', 'word/stylesWithEffects.xml'):
                with self.subTest(part=part, mutation=mutate):
                    self.partial(rewrite({part: mutate}))

    def test_inherited_colors_numbering_missing_base_and_cycles(self):
        def mutate(root, prop):
            child(style(root), 'basedOn', val='Base')
            base = child(root, 'style', styleId='Base', type='paragraph')
            if prop == 'cycle':
                child(base, 'basedOn', val='Normal')
            elif prop == 'missing':
                child(base, 'basedOn', val='Missing')
            elif prop == 'color':
                child(child(base, 'rPr'), 'color', val='FFFFFF')
            else:
                child(child(base, 'pPr'), 'numPr')
        for prop in ('cycle', 'missing', 'color', 'numbering'):
            with self.subTest(prop=prop):
                self.partial(rewrite({name: lambda root: mutate(root, prop)
                    for name in ('word/styles.xml', 'word/stylesWithEffects.xml')}))

    def test_duplicate_style_ids_and_default_types(self):
        for mutate in [lambda root: child(root, 'style', styleId='Normal', type='paragraph'),
                       lambda root: child(root, 'style', styleId='Other', type='paragraph', default='1')]:
            self.partial(rewrite({'word/styles.xml': mutate}), 'DOCX_STYLE_UNRESOLVED')

    def test_default_character_and_conditional_table_effects(self):
        def conditional(root):
            cond = child(style(root, 'TableNormal'), 'tblStylePr', type='firstRow')
            child(child(cond, 'rPr'), 'color', val='FFFFFF')
        self.partial(rewrite({'word/styles.xml': conditional}), 'DOCX_STYLE_CONTENT_UNSUPPORTED')
        self.partial(rewrite({'word/stylesWithEffects.xml': lambda root:
            child(child(style(root, 'DefaultParagraphFont'), 'rPr'), 'webHidden')}), 'DOCX_VISIBILITY_UNRESOLVED')

    def test_unused_style_differences_and_whitespace_are_compatible(self):
        def mutate(root):
            extra = child(root, 'style', styleId='UnusedExtra', type='paragraph')
            child(child(extra, 'pPr'), 'numPr')
            child(child(extra, 'rPr'), 'color', val='FFFFFF')
            ET.indent(root)
        result = extract(rewrite({'word/stylesWithEffects.xml': mutate}))
        self.assertTrue(result.report_source_eligible)
        self.assertEqual(len(result.blocks), 5)

    def test_unknown_style_default_and_latent_content_not_normalized_away(self):
        for mutate in [
            lambda root: setattr(style(root), 'text', 'unexpected value'),
            lambda root: child(root.find(q('docDefaults')), 'unknown'),
            lambda root: root.find(q('docDefaults')).set(q('semantic'), 'new'),
            lambda root: child(root.find(q('latentStyles')), 'unknown'),
        ]:
            self.partial(rewrite({'word/styles.xml': mutate}), 'DOCX_STYLE_CONTENT_UNSUPPORTED')

    def test_web_output_flags_only_with_legal_values(self):
        for value in ('0', '1', 'true', 'false', 'on', 'off'):
            self.assertTrue(extract(rewrite({'word/webSettings.xml': lambda root: root[0].set(q('val'), value)})).report_source_eligible)
        for mutate in [lambda root: root[0].set(q('val'), 'maybe'),
                       lambda root: child(root, 'frameset'),
                       lambda root: root.set(q('unknown'), '1'),
                       lambda root: child(root[0], 'unknown')]:
            self.partial(rewrite({'word/webSettings.xml': mutate}), 'DOCX_MARKUP_UNSUPPORTED')

    def test_numbering_body_and_reverse_style_link_are_partial(self):
        self.partial(rewrite({'word/document.xml': lambda root:
            child(child(root.find(q('body'))[0], 'pPr'), 'numPr')}), 'DOCX_NUMBERING_UNRESOLVED')
        self.partial(rewrite({'word/numbering.xml': lambda root:
            child(root[0], 'styleLink', val='Normal')}), 'DOCX_NUMBERING_UNRESOLVED')

    def test_custom_xml_nonempty_unknown_schema_and_malformed_guid(self):
        self.partial(rewrite({'customXml/item1.xml': lambda root:
            ET.SubElement(root, '{http://schemas.openxmlformats.org/officeDocument/2006/bibliography}Source')}), 'DOCX_CUSTOM_XML_UNRESOLVED')
        self.partial(rewrite({'customXml/itemProps1.xml': lambda root:
            root.set('{http://schemas.openxmlformats.org/officeDocument/2006/customXml}itemID', 'bad')}), 'DOCX_CUSTOM_XML_UNRESOLVED')
        self.partial(rewrite({'customXml/itemProps1.xml': lambda root:
            root[0][0].set('{http://schemas.openxmlformats.org/officeDocument/2006/customXml}uri', 'urn:business')}), 'DOCX_CUSTOM_XML_UNRESOLVED')

    def test_two_empty_custom_stores_cannot_share_guid(self):
        def edit(parts):
            parts['customXml/item2.xml'] = parts['customXml/item1.xml']
            parts['customXml/itemProps2.xml'] = parts['customXml/itemProps1.xml']
            parts['customXml/_rels/item2.xml.rels'] = parts['customXml/_rels/item1.xml.rels'].replace(b'itemProps1.xml', b'itemProps2.xml')
        def types(root):
            ET.SubElement(root, f'{{{CT}}}Override', PartName='/customXml/itemProps2.xml', ContentType='application/vnd.openxmlformats-officedocument.customXmlProperties+xml')
        def rels(root):
            ET.SubElement(root, f'{{{R}}}Relationship', Id='custom2', Type=O + 'customXml', Target='../customXml/item2.xml')
        self.partial(rewrite({'[Content_Types].xml': types, 'word/_rels/document.xml.rels': rels}, edit), 'DOCX_CUSTOM_XML_UNRESOLVED')

    def test_thumbnail_outgoing_relation_and_conflicting_type_declaration(self):
        def edit(parts):
            parts['docProps/_rels/thumbnail.jpeg.rels'] = f'<Relationships xmlns="{R}"><Relationship Id="back" Type="{O}customXml" Target="../customXml/item1.xml"/></Relationships>'.encode()
        self.partial(rewrite(edit=edit), 'DOCX_PROFILE_PART_UNSUPPORTED')
        self.partial(rewrite({'[Content_Types].xml': lambda root:
            ET.SubElement(root, f'{{{CT}}}Default', Extension='jpeg', ContentType='application/xml')}), 'DOCX_CONTENT_TYPES_INVALID')

    def test_sdt_binding_and_fields_cannot_trust_cached_text(self):
        for name in ('sdt', 'dataBinding', 'fldSimple'):
            self.partial(rewrite({'word/document.xml': lambda root:
                child(root.find(q('body'))[0], name)}))

    def test_thumbnail_reused_in_body_missing_identity_and_wrong_bytes(self):
        self.partial(rewrite({'word/_rels/document.xml.rels': lambda root:
            ET.SubElement(root, f'{{{R}}}Relationship', Id='image', Type=O + 'image', Target='../docProps/thumbnail.jpeg')}))
        self.partial(rewrite({'_rels/.rels': lambda root:
            root.remove(next(node for node in root if node.get('Type') == R + '/metadata/thumbnail'))}), 'DOCX_PART_UNSUPPORTED')
        self.partial(rewrite(edit=lambda parts: parts.__setitem__('docProps/thumbnail.jpeg', b'<xml/>')), 'DOCX_THUMBNAIL_UNRESOLVED')

    def test_wrong_content_type_root_and_relationship_source(self):
        self.partial(rewrite({'[Content_Types].xml': lambda root:
            next(node for node in root if node.get('PartName') == '/word/stylesWithEffects.xml').set('ContentType', 'application/xml')}), 'DOCX_PROFILE_PART_UNSUPPORTED')
        self.partial(rewrite({'word/webSettings.xml': lambda root: setattr(root, 'tag', q('settings'))}), 'DOCX_PROFILE_PART_UNSUPPORTED')
        self.partial(rewrite({'customXml/_rels/item1.xml.rels': lambda root:
            ET.SubElement(root, f'{{{R}}}Relationship', Id='web', Type=O + 'webSettings', Target='../word/webSettings.xml')}))

    def test_duplicate_effects_edge_and_primary_style_absence(self):
        effects = 'http://schemas.microsoft.com/office/2007/relationships/stylesWithEffects'
        self.partial(rewrite({'word/_rels/document.xml.rels': lambda root:
            ET.SubElement(root, f'{{{R}}}Relationship', Id='copy', Type=effects, Target='stylesWithEffects.xml')}))
        self.partial(rewrite({'word/_rels/document.xml.rels': lambda root:
            root.remove(next(node for node in root if node.get('Type') == O + 'styles'))}), 'DOCX_STYLE_UNRESOLVED')

    def test_unused_definitions_cannot_smuggle_unknown_active_semantics(self):
        for part, mutate in [
            ('word/settings.xml', lambda root: child(root, 'mailMerge')),
            ('word/fontTable.xml', lambda root: child(root[0], 'embedRegular')),
            ('word/theme/theme1.xml', lambda root: ET.SubElement(root, '{urn:unknown}effect')),
            ('word/settings.xml', lambda root: root[0].set('{urn:unknown}semantic', '1')),
        ]:
            self.partial(rewrite({part: mutate}), 'DOCX_MARKUP_UNSUPPORTED')

    def test_relocated_style_part_is_resolved_by_graph_not_filename(self):
        def edit(parts):
            parts['word/ordinary-style.xml'] = parts.pop('word/styles.xml')
        def rels(root):
            next(node for node in root if node.get('Type') == O + 'styles').set('Target', 'ordinary-style.xml')
        def types(root):
            next(node for node in root if node.get('PartName') == '/word/styles.xml').set('PartName', '/word/ordinary-style.xml')
        self.assertTrue(extract(rewrite({'word/_rels/document.xml.rels': rels, '[Content_Types].xml': types}, edit)).report_source_eligible)

    def test_manifest_changes_when_unused_definition_changes(self):
        result = extract(ORIGINAL.read_bytes())
        changed = extract(rewrite({'word/webSettings.xml': lambda root: root[0].set(q('val'), 'false')}))
        self.assertTrue(changed.report_source_eligible)
        self.assertNotEqual(changed.manifest_sha256, result.manifest_sha256)
        self.assertEqual([b.text for b in changed.blocks], [b.text for b in result.blocks])


if __name__ == '__main__':
    unittest.main()
