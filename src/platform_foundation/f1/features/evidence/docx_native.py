"""Bounded, non-executing DOCX structural extraction from verified original bytes.

The initial profile supports ordinary body paragraphs and simple horizontal
table grids. Any unresolved content makes the whole source ineligible. It does
not establish Word layout, pagination, field evaluation or visual equivalence.
"""
from __future__ import annotations

import hashlib
import io
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as SafeXML

from .contracts import DocxBlockLocator, canonical_json
from .docx_package import EFFECTS, MC, _tree, inspect_package, relationship_source

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
O = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
MAIN_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
PARSER_VERSION = "docx-native-1"
SUPPORT_PROFILE = "transitional-main-body-simple-table-1"


class DocxExtractionError(ValueError):
    """Fixed, body-free reason codes; a failed package is never partial success."""


@dataclass(frozen=True, slots=True)
class ExtractionLimits:
    source_bytes: int = 25 * 1024 * 1024
    entries: int = 2048
    entry_bytes: int = 16 * 1024 * 1024
    expanded_bytes: int = 128 * 1024 * 1024
    compression_ratio: int = 100
    xml_nodes: int = 400_000
    xml_depth: int = 128
    blocks: int = 20_000
    characters: int = 2_000_000

    def __post_init__(self):
        if any(type(v) is not int or v < 1 for v in self.__dict_values()):
            raise ValueError("DOCX_LIMITS_INVALID")

    def __dict_values(self):
        return (getattr(self, name) for name in self.__dataclass_fields__)


@dataclass(frozen=True, slots=True)
class CoverageDebt:
    reason_code: str
    part: str
    path: str


@dataclass(frozen=True, slots=True, repr=False)
class NativeBlock:
    locator: DocxBlockLocator
    text: str = field(repr=False)
    token_sha256: str

    @property
    def body_sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    def __repr__(self):
        return f"NativeBlock(locator={self.locator!r}, text=<redacted>)"


@dataclass(frozen=True, slots=True)
class DocxExtraction:
    source_sha256: str
    manifest_sha256: str
    blocks: tuple[NativeBlock, ...]
    debts: tuple[CoverageDebt, ...]
    expected_block_count: int
    processed_block_count: int
    parser_version: str = PARSER_VERSION
    support_profile: str = SUPPORT_PROFILE

    @property
    def coverage_state(self) -> str:
        return "partial" if self.debts else "complete"

    @property
    def report_source_eligible(self) -> bool:
        return not self.debts and any(block.text.strip() for block in self.blocks)


def _w(name: str) -> str:
    return f"{{{W}}}{name}"


def _parse_xml(body: bytes, limits: ExtractionLimits, used_nodes: list[int]) -> Element:
    depth = 0
    try:
        iterator = SafeXML.iterparse(io.BytesIO(body), events=("start", "end"),
            forbid_dtd=True, forbid_entities=True, forbid_external=True)
        for event, _node in iterator:
            if event == "start":
                depth += 1
                used_nodes[0] += 1
                if depth > limits.xml_depth or used_nodes[0] > limits.xml_nodes:
                    raise DocxExtractionError("DOCX_XML_BUDGET_EXCEEDED")
            else:
                depth -= 1
        return iterator.root
    except DocxExtractionError:
        raise
    except Exception:
        raise DocxExtractionError("DOCX_XML_UNSAFE_OR_CORRUPT") from None


def _walk(root: Element):
    # XML depth is already bounded. Paths preserve source sibling positions.
    stack = [(root, "/" + root.tag.rsplit("}", 1)[-1] + "[1]")]
    while stack:
        node, path = stack.pop()
        yield node, path
        counts: dict[str, int] = {}
        children = []
        for child in node:
            counts[child.tag] = counts.get(child.tag, 0) + 1
            children.append((child, path + "/" + child.tag.rsplit("}", 1)[-1] + f"[{counts[child.tag]}]"))
        stack.extend(reversed(children))


def _target(part: str, target: str, names: set[str]) -> str:
    if not target or any(char in target for char in ("\\", ":", "%", "?", "#", "\x00")):
        raise DocxExtractionError("DOCX_RELATIONSHIP_INVALID")
    if target.startswith("/"):
        resolved = target[1:]
    else:
        base = "" if part == "_rels/.rels" else part.rsplit("/_rels/", 1)[0]
        resolved = posixpath.normpath(posixpath.join(base, target))
    if resolved.startswith("../") or resolved not in names:
        raise DocxExtractionError("DOCX_RELATIONSHIP_TARGET_MISSING")
    return resolved


_GLOBAL_REASONS = {
    "vanish": "DOCX_VISIBILITY_UNRESOLVED", "webHidden": "DOCX_VISIBILITY_UNRESOLVED",
    "specVanish": "DOCX_VISIBILITY_UNRESOLVED", "numPr": "DOCX_NUMBERING_UNRESOLVED",
    "fldChar": "DOCX_FIELD_UNRESOLVED", "instrText": "DOCX_FIELD_UNRESOLVED",
    "fldSimple": "DOCX_FIELD_UNRESOLVED", "altChunk": "DOCX_ALTCHUNK_UNSUPPORTED",
    "drawing": "DOCX_NON_TEXT_CONTENT", "pict": "DOCX_NON_TEXT_CONTENT",
    "object": "DOCX_NON_TEXT_CONTENT", "txbxContent": "DOCX_NON_TEXT_CONTENT",
    "headerReference": "DOCX_STORY_UNSUPPORTED", "footerReference": "DOCX_STORY_UNSUPPORTED",
    "footnoteReference": "DOCX_STORY_UNSUPPORTED", "endnoteReference": "DOCX_STORY_UNSUPPORTED",
    "commentReference": "DOCX_STORY_UNSUPPORTED", "vMerge": "DOCX_TABLE_GRID_UNSUPPORTED",
    "hMerge": "DOCX_TABLE_GRID_UNSUPPORTED", "gridBefore": "DOCX_TABLE_GRID_UNSUPPORTED",
    "gridAfter": "DOCX_TABLE_GRID_UNSUPPORTED", "bidiVisual": "DOCX_TABLE_DIRECTION_UNSUPPORTED",
}
_REVISION = {"ins", "del", "delText", "cellIns", "cellDel", "cellMerge", "numberingChange"}
# Known formatting vocabulary. Color/shading still require visibility resolution;
# unknown properties remain partial in this bounded structural profile.
_PROPERTIES = {
    "pPr": {"pStyle", "keepNext", "keepLines", "pageBreakBefore", "widowControl", "spacing", "ind", "contextualSpacing", "jc", "outlineLvl", "tabs", "tab", "pBdr", "top", "left", "bottom", "right", "between", "bar", "shd", "rPr"},
    "rPr": {"rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike", "outline", "shadow", "emboss", "imprint", "color", "spacing", "w", "kern", "position", "sz", "szCs", "highlight", "u", "effect", "bdr", "shd", "fitText", "vertAlign", "lang", "noProof", "snapToGrid"},
    "tblPr": {"tblStyle", "tblW", "jc", "tblInd", "tblBorders", "top", "left", "bottom", "right", "insideH", "insideV", "tblLayout", "tblCellMar", "tblLook", "tblCaption", "tblDescription", "shd"},
    "trPr": {"cantSplit", "trHeight", "tblHeader", "jc", "tblCellSpacing"},
    "tcPr": {"tcW", "gridSpan", "tcBorders", "top", "left", "bottom", "right", "insideH", "insideV", "tl2br", "tr2bl", "shd", "noWrap", "tcMar", "vAlign", "hideMark"},
    "sectPr": {"pgSz", "pgMar", "pgBorders", "top", "left", "bottom", "right", "cols", "docGrid", "type", "titlePg", "pgNumType"},
}
_STRUCTURE_ATTRIBUTES = {
    "document": {"{http://schemas.openxmlformats.org/markup-compatibility/2006}Ignorable"},
    "body": set(), "p": {_w(n) for n in ("rsidR", "rsidRPr", "rsidRDefault", "rsidP", "rsidDel")} | {"{http://schemas.microsoft.com/office/word/2010/wordml}paraId", "{http://schemas.microsoft.com/office/word/2010/wordml}textId"},
    "r": {_w(n) for n in ("rsidR", "rsidRPr", "rsidDel")},
    "tbl": set(), "tblGrid": set(), "gridCol": {_w("w")},
    "tr": {_w(n) for n in ("rsidR", "rsidRPr", "rsidDel", "rsidTr")}, "tc": set(),
    "bookmarkStart": {_w("id"), _w("name")}, "bookmarkEnd": {_w("id")}, "proofErr": {_w("type")},
}
_PROPERTY_ATTRIBUTES = {_w(name) for name in (
    "val", "ascii", "hAnsi", "eastAsia", "cs", "hint", "asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme",
    "themeColor", "themeTint", "themeShade", "color", "fill", "themeFill", "themeFillTint", "themeFillShade",
    "w", "h", "type", "sz", "space", "shadow", "frame", "top", "left", "bottom", "right", "start", "end",
    "header", "footer", "gutter", "orient", "code", "linePitch", "charSpace", "num", "equalWidth", "sep",
    "before", "after", "beforeLines", "afterLines", "beforeAutospacing", "afterAutospacing", "line", "lineRule",
    "firstLine", "firstLineChars", "hanging", "hangingChars", "leftChars", "rightChars", "startChars", "endChars",
    "hRule", "leader", "pos", "firstRow", "lastRow", "firstColumn", "lastColumn", "noHBand", "noVBand",
    "eastAsia", "bidi", "rsidR", "rsidRPr", "rsidSect", "rsidDel", "countBy", "restart", "distance", "fmt",
)}


def extract_docx(source: bytes, *, expected_sha256: str,
                 limits: ExtractionLimits = ExtractionLimits()) -> DocxExtraction:
    if not isinstance(source, bytes) or len(source) > limits.source_bytes:
        raise DocxExtractionError("DOCX_SOURCE_BUDGET_EXCEEDED")
    source_sha = hashlib.sha256(source).hexdigest()
    if source_sha != expected_sha256:
        raise DocxExtractionError("DOCX_SOURCE_SHA_MISMATCH")
    debts: list[CoverageDebt] = []

    def debt(reason: str, part: str, path: str):
        item = CoverageDebt(reason, part, path)
        if item not in debts:
            if len(debts) >= 256:
                raise DocxExtractionError("DOCX_COVERAGE_BUDGET_EXCEEDED")
            debts.append(item)

    manifest = []
    main = None
    parts, roots, edges = {}, {}, []
    root_office_targets = []
    content_types = None
    used_nodes = [0]
    try:
        with zipfile.ZipFile(io.BytesIO(source)) as archive:
            infos = archive.infolist()
            names = {info.filename for info in infos}
            if not infos or len(infos) > limits.entries:
                raise DocxExtractionError("DOCX_PACKAGE_ENTRY_LIMIT")
            if len(names) != len(infos):
                raise DocxExtractionError("DOCX_DUPLICATE_PART")
            if not {"[Content_Types].xml", "_rels/.rels", "word/document.xml"} <= names:
                raise DocxExtractionError("DOCX_PROFILE_UNSUPPORTED")
            expanded = 0
            for info in infos:
                name = info.filename
                if name.startswith("/") or any(p in {"", ".", ".."} for p in name.split("/")) or any(c in name for c in ("\\", ":", "%", "?", "#", "\x00")):
                    raise DocxExtractionError("DOCX_UNSAFE_PART")
                if info.flag_bits & 1 or re.search(r"\.(zip|7z|rar|tar|gz|docx|xlsx|docm|xlsm)$", name, re.I) or "vbaproject" in name.lower() or "/embeddings/" in f"/{name}":
                    raise DocxExtractionError("DOCX_UNSAFE_PART")
                expanded += info.file_size
                if info.file_size > limits.entry_bytes or expanded > limits.expanded_bytes or (info.file_size and info.file_size > info.compress_size * limits.compression_ratio):
                    raise DocxExtractionError("DOCX_PACKAGE_SIZE_LIMIT")
            for info in infos:
                name = info.filename
                with archive.open(info) as handle:
                    body = handle.read(limits.entry_bytes + 1)
                if len(body) != info.file_size or len(body) > limits.entry_bytes:
                    raise DocxExtractionError("DOCX_PACKAGE_SIZE_LIMIT")
                manifest.append({"part": name, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)})
                parts[name] = body
                root = _parse_xml(body, limits, used_nodes) if name.endswith((".xml", ".rels")) else None
                if root is not None:
                    roots[name] = root
                if name == "[Content_Types].xml":
                    content_types = root
                elif name == "word/document.xml":
                    main = root
                if name.endswith(".rels"):
                    if root.tag != f"{{{R}}}Relationships":
                        raise DocxExtractionError("DOCX_RELATIONSHIP_INVALID")
                    origin = relationship_source(name)
                    if origin is None or (origin and origin not in names) or root.attrib or (root.text or "").strip():
                        raise DocxExtractionError("DOCX_RELATIONSHIP_INVALID")
                    ids = set()
                    for rel in root:
                        if rel.tag != f"{{{R}}}Relationship" or not rel.get("Id") or rel.get("Id") in ids:
                            raise DocxExtractionError("DOCX_RELATIONSHIP_INVALID")
                        if set(rel.attrib) - {"Id", "Type", "Target", "TargetMode"} or list(rel) or (rel.text or "").strip() or (rel.tail or "").strip():
                            raise DocxExtractionError("DOCX_RELATIONSHIP_INVALID")
                        ids.add(rel.get("Id"))
                        if rel.get("TargetMode", "Internal") != "Internal":
                            raise DocxExtractionError("DOCX_EXTERNAL_RELATIONSHIP")
                        target = _target(name, rel.get("Target", ""), names)
                        rel_type = rel.get("Type", "")
                        edges.append((origin, rel_type, target, name, rel.get("Id")))
                        if name == "_rels/.rels" and rel_type == O + "officeDocument":
                            root_office_targets.append(target)
                if root is not None:
                    for node, path in _walk(root):
                        if any(MC + attr in node.attrib for attr in ("MustUnderstand", "ProcessContent", "PreserveElements", "PreserveAttributes")) or node.tag == MC + "AlternateContent":
                            debt("DOCX_MARKUP_UNSUPPORTED", name, path)
                        if node.tag.startswith("{" + W + "}"):
                            local = node.tag.split("}")[1]
                            reason = _GLOBAL_REASONS.get(local)
                            # Numbering in unused style definitions has no effect.
                            # Effective/default style chains are checked below.
                            if local == "numPr" and root.tag == _w("styles"):
                                reason = None
                            if local in _REVISION or local.endswith("Change") or local.startswith(("moveFrom", "moveTo")):
                                reason = "DOCX_REVISION_UNRESOLVED"
                            if reason:
                                debt(reason, name, path)
    except DocxExtractionError:
        raise
    except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile, NotImplementedError):
        raise DocxExtractionError("DOCX_PACKAGE_CORRUPT") from None
    if root_office_targets != ["word/document.xml"] or main is None or main.tag != _w("document"):
        raise DocxExtractionError("DOCX_PROFILE_UNSUPPORTED")
    if content_types is None or content_types.tag != f"{{{CT}}}Types":
        raise DocxExtractionError("DOCX_CONTENT_TYPES_INVALID")
    overrides = [node.get("ContentType") for node in content_types if node.tag == f"{{{CT}}}Override" and node.get("PartName") == "/word/document.xml"]
    if overrides != [MAIN_TYPE]:
        raise DocxExtractionError("DOCX_PROFILE_UNSUPPORTED")
    stores, assessments = inspect_package(parts, roots, edges, content_types, debt)
    bodies = main.findall(_w("body"))
    if len(bodies) != 1 or len(main) != 1:
        raise DocxExtractionError("DOCX_DOCUMENT_STRUCTURE_INVALID")
    body = bodies[0]
    blocks: list[NativeBlock] = []
    chars = 0
    expected = sum(1 for node in body.iter() if node.tag == _w("p"))
    for node, path in _walk(main):
        local = node.tag.rsplit("}", 1)[-1]
        allowed_attrs = _STRUCTURE_ATTRIBUTES.get(local)
        if allowed_attrs is not None and set(node.attrib) - allowed_attrs:
            debt("DOCX_MARKUP_UNSUPPORTED", "word/document.xml", path)
        if node.tag != _w("t") and node.text and node.text.strip():
            debt("DOCX_MARKUP_UNSUPPORTED", "word/document.xml", path)
        if node.tail and node.tail.strip():
            debt("DOCX_MARKUP_UNSUPPORTED", "word/document.xml", path)

    def unsupported(path: str, reason: str = "DOCX_MARKUP_UNSUPPORTED"):
        debt(reason, "word/document.xml", path)

    def properties(node: Element, path: str, part: str = "word/document.xml"):
        parent = node.tag.rsplit("}", 1)[-1]
        allowed = _PROPERTIES.get(parent, set())
        for child, child_path in _walk(node):
            if set(child.attrib) - _PROPERTY_ATTRIBUTES:
                debt("DOCX_MARKUP_UNSUPPORTED", part, path + child_path)
            if child is node:
                continue
            local = child.tag.rsplit("}", 1)[-1]
            # This profile cannot resolve foreground/background inheritance,
            # themes or auto colors. Check effective properties, not every style.
            if child.tag in {_w("color"), _w("shd")}:
                debt("DOCX_VISIBILITY_UNRESOLVED", part, path + child_path)
            if child.tag == _w("rPr") or (parent == "pPr" and child.tag in {_w(v) for v in _PROPERTIES["rPr"]}):
                continue
            if child.tag != _w(local) or local not in allowed:
                debt("DOCX_MARKUP_UNSUPPORTED", part, path + child_path)
            if local == "cols" and child.get(_w("num"), "1") != "1":
                debt("DOCX_READING_ORDER_UNSUPPORTED", part, path + child_path)

    # Resolve both stores independently; equality ignores indentation only.
    referenced = {node.get(_w("val")) for node in main.iter()
        if node.tag in {_w("pStyle"), _w("rStyle"), _w("tblStyle")}}

    def style_projection(part, styles):
        style_map, defaults, selected = {}, {}, set(referenced)
        for node in styles:
            if node.tag not in {_w("style"), _w("docDefaults"), _w("latentStyles")}:
                debt("DOCX_STYLE_CONTENT_UNSUPPORTED", part, "/styles")
            if (node.tail or "").strip():
                debt("DOCX_STYLE_CONTENT_UNSUPPORTED", part, "/styles")
            if node.tag == _w("latentStyles"):
                allowed = {_w(value) for value in ("defLockedState", "defUIPriority", "defSemiHidden", "defUnhideWhenUsed", "defQFormat", "count")}
                if set(node.attrib) - allowed or (node.text or "").strip():
                    debt("DOCX_STYLE_CONTENT_UNSUPPORTED", part, "/styles/latentStyles")
                for latent in node:
                    if latent.tag != _w("lsdException") or list(latent) or set(latent.attrib) - {_w(value) for value in ("name", "locked", "uiPriority", "semiHidden", "unhideWhenUsed", "qFormat")} or (latent.text or "").strip() or (latent.tail or "").strip():
                        debt("DOCX_STYLE_CONTENT_UNSUPPORTED", part, "/styles/latentStyles")
        if (styles.text or "").strip():
            debt("DOCX_STYLE_CONTENT_UNSUPPORTED", part, "/styles")
        doc_defaults = styles.findall(_w("docDefaults"))
        if len(doc_defaults) > 1:
            debt("DOCX_STYLE_UNRESOLVED", part, "/styles/docDefaults")
        for default in doc_defaults:
            if default.attrib:
                debt("DOCX_STYLE_CONTENT_UNSUPPORTED", part, "/styles/docDefaults")
            for node in default.iter():
                if (node.text or "").strip() or (node.tail or "").strip():
                    debt("DOCX_STYLE_CONTENT_UNSUPPORTED", part, "/styles/docDefaults")
                if node.tag in {_w("pPr"), _w("rPr")}:
                    properties(node, "/styles/docDefaults", part)
            for wrapper in default:
                if wrapper.tag not in {_w("pPrDefault"), _w("rPrDefault")} or wrapper.attrib or len(wrapper) > 1 or any(child.tag != (_w("pPr") if wrapper.tag == _w("pPrDefault") else _w("rPr")) for child in wrapper):
                    debt("DOCX_STYLE_CONTENT_UNSUPPORTED", part, "/styles/docDefaults")
        for style in styles.findall(_w("style")):
            sid, kind = style.get(_w("styleId")), style.get(_w("type"))
            if not sid or sid in style_map:
                debt("DOCX_STYLE_UNRESOLVED", part, "/styles/style")
                continue
            style_map[sid] = style
            if style.get(_w("default")) in {"1", "true", "on"}:
                if kind in defaults or kind not in {"paragraph", "character", "table", "numbering"}:
                    debt("DOCX_STYLE_UNRESOLVED", part, "/styles/default")
                defaults[kind] = sid
                selected.add(sid)
            elif style.get(_w("default"), "0") not in {"0", "false", "off"}:
                debt("DOCX_STYLE_UNRESOLVED", part, "/styles/default")
        checked = set()
        for ref in main.iter():
            expected_type = {_w("pStyle"): "paragraph", _w("rStyle"): "character", _w("tblStyle"): "table"}.get(ref.tag)
            target = style_map.get(ref.get(_w("val")))
            if expected_type and target is not None and target.get(_w("type")) != expected_type:
                debt("DOCX_STYLE_UNRESOLVED", part, "/styles/reference-type")
        for sid in sorted(selected, key=lambda value: value or ""):
            chain = set()
            while sid is not None and sid not in checked:
                if sid in chain or sid not in style_map:
                    debt("DOCX_STYLE_UNRESOLVED", part, "/styles/style")
                    break
                chain.add(sid)
                style = style_map[sid]
                if style.get(_w("type")) not in {"paragraph", "character", "table", "numbering"}:
                    debt("DOCX_STYLE_UNRESOLVED", part, "/styles/style/type")
                if set(style.attrib) - {_w(value) for value in ("styleId", "type", "default", "customStyle")}:
                    debt("DOCX_STYLE_CONTENT_UNSUPPORTED", part, "/styles/style")
                for node, path in _walk(style):
                    if (node.text or "").strip() or (node.tail or "").strip():
                        debt("DOCX_STYLE_CONTENT_UNSUPPORTED", part, path)
                    if node.tag == _w("numPr"):
                        debt("DOCX_NUMBERING_UNRESOLVED", part, path)
                for node in style:
                    if node.tag in {_w(value) for value in _PROPERTIES}:
                        properties(node, "/styles/style", part)
                    elif node.tag not in {_w(value) for value in ("name", "aliases", "basedOn", "next", "hidden", "uiPriority", "semiHidden", "unhideWhenUsed", "qFormat", "locked", "personal", "personalCompose", "personalReply", "rsid")} or list(node) or set(node.attrib) - {_w("val")}:
                        # Linked/auto-redefined and conditional styles need their
                        # own interpretation; never pick just the benign store.
                        debt("DOCX_STYLE_CONTENT_UNSUPPORTED", part, "/styles/style/" + node.tag.rsplit("}", 1)[-1])
                bases = style.findall(_w("basedOn"))
                if len(bases) > 1 or (bases and not bases[0].get(_w("val"))):
                    debt("DOCX_STYLE_UNRESOLVED", part, "/styles/style/basedOn")
                    break
                base = bases[0].get(_w("val")) if bases else None
                if base in style_map and style_map[base].get(_w("type")) != style.get(_w("type")):
                    debt("DOCX_STYLE_UNRESOLVED", part, "/styles/style/basedOn/type")
                sid = base
            checked.update(chain)
        return (tuple(sorted(defaults.items(), key=lambda item: item[0] or "")),
                tuple(_tree(node) for node in doc_defaults),
                tuple((sid, _tree(style_map[sid])) for sid in sorted(checked) if sid in style_map))

    projections = {kind: style_projection(part, root) for kind, (part, root) in stores.items()}
    if referenced and O + "styles" not in stores:
        debt("DOCX_STYLE_UNRESOLVED", "word/document.xml", "/styles/missing")
    if EFFECTS in projections and projections.get(O + "styles") != projections[EFFECTS]:
        debt("DOCX_STYLE_UNRESOLVED", stores[EFFECTS][0], "/styles/effective-copy-mismatch")
    effective_ids = {sid for projection in projections.values() for sid, _value in projection[2]}
    for part, root in roots.items():
        if root.tag == _w("numbering"):
            for node in root.iter():
                if node.tag in {_w("pStyle"), _w("styleLink"), _w("numStyleLink")} and node.get(_w("val")) in effective_ids:
                    debt("DOCX_NUMBERING_UNRESOLVED", part, "/numbering/effective-style-link")

    def paragraph(node: Element, locator: DocxBlockLocator, path: str):
        nonlocal chars
        tokens = []
        for child in node:
            if child.tag == _w("pPr"):
                properties(child, path)
            elif child.tag == _w("r"):
                for leaf in child:
                    local = leaf.tag.rsplit("}", 1)[-1]
                    if leaf.tag == _w("rPr"):
                        properties(leaf, path)
                        continue
                    if list(leaf) or leaf.tag not in {_w(v) for v in ("t", "tab", "br", "cr")}:
                        unsupported(path)
                        continue
                    permitted_attrs = {"{http://www.w3.org/XML/1998/namespace}space"} if local == "t" else ({_w("type"), _w("clear")} if local == "br" else set())
                    if set(leaf.attrib) - permitted_attrs:
                        unsupported(path)
                    if local == "t":
                        # Preserve exact source whitespace, including default-mode
                        # edge whitespace; require explicit preserve for ambiguity.
                        text = leaf.text or ""
                        if text != text.strip() and leaf.get("{http://www.w3.org/XML/1998/namespace}space") != "preserve":
                            unsupported(path, "DOCX_WHITESPACE_UNRESOLVED")
                    elif local == "tab":
                        text = "\t"
                    else:
                        text = "\n"
                        if local == "br" and (leaf.get(_w("type"), "textWrapping") != "textWrapping" or leaf.get(_w("clear"), "none") != "none"):
                            unsupported(path, "DOCX_BREAK_UNSUPPORTED")
                    tokens.append({"kind": local, "text": text, "attributes": dict(leaf.attrib)})
            elif child.tag not in {_w("bookmarkStart"), _w("bookmarkEnd"), _w("proofErr")} or list(child):
                unsupported(path)
        text = "".join(t["text"] for t in tokens)
        chars += len(text)
        if len(blocks) >= limits.blocks or chars > limits.characters:
            raise DocxExtractionError("DOCX_TEXT_BUDGET_EXCEEDED")
        blocks.append(NativeBlock(locator, text, hashlib.sha256(canonical_json(tokens)).hexdigest()))

    for body_index, node in enumerate(body, 1):
        path = f"/document/body/*[{body_index}]"
        if node.tag == _w("p"):
            paragraph(node, DocxBlockLocator(body_index), path)
        elif node.tag == _w("sectPr"):
            properties(node, path)
        elif node.tag == _w("tbl"):
            row_index = 0
            for row in node:
                if row.tag == _w("tblPr"):
                    properties(row, path)
                elif row.tag == _w("tblGrid"):
                    if any(c.tag != _w("gridCol") or list(c) for c in row):
                        unsupported(path, "DOCX_TABLE_GRID_UNSUPPORTED")
                elif row.tag == _w("tr"):
                    row_index += 1
                    cell_index, column = 0, 1
                    for cell in row:
                        if cell.tag == _w("trPr"):
                            properties(cell, path)
                            continue
                        if cell.tag != _w("tc"):
                            unsupported(path)
                            continue
                        cell_index += 1
                        spans = cell.findall(f"{_w('tcPr')}/{_w('gridSpan')}")
                        span = spans[0].get(_w("val")) if spans else "1"
                        if len(spans) > 1 or not isinstance(span, str) or re.fullmatch(r"[1-9][0-9]{0,4}", span) is None:
                            raise DocxExtractionError("DOCX_TABLE_SPAN_INVALID")
                        span = int(span)
                        if column + span > 1_000_001:
                            raise DocxExtractionError("DOCX_TABLE_BUDGET_EXCEEDED")
                        paragraph_index = 0
                        for entry in cell:
                            if entry.tag == _w("tcPr"):
                                properties(entry, path)
                            elif entry.tag == _w("p"):
                                paragraph_index += 1
                                locator = DocxBlockLocator(body_index, row_index, cell_index, column, span, paragraph_index)
                                paragraph(entry, locator, path + f"/tr[{row_index}]/tc[{cell_index}]/p[{paragraph_index}]")
                            else:
                                unsupported(path, "DOCX_TABLE_CONTENT_UNSUPPORTED")
                        column += span
                else:
                    unsupported(path)
        else:
            unsupported(path)
    # In this first profile partial documents expose no usable fragments. This
    # prevents unresolved fields or inherited visibility leaking into evidence.
    usable = () if debts else tuple(blocks)
    identity = {"source_sha256": source_sha, "parser_version": PARSER_VERSION,
        "support_profile": SUPPORT_PROFILE, "package_semantics": 2, "assessments": assessments, "parts": sorted(manifest, key=lambda x: x["part"]),
        "blocks": [{"locator": b.locator.to_dict(), "tokens": b.token_sha256, "body": b.body_sha256} for b in blocks],
        "debts": [{"reason": d.reason_code, "part": d.part, "path": d.path} for d in debts]}
    return DocxExtraction(source_sha, hashlib.sha256(canonical_json(identity)).hexdigest(),
        usable, tuple(debts), expected, len(blocks))
