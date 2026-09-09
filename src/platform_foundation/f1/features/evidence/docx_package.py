"""Closed ancillary-part policies for the bounded DOCX body profile.

Parts are selected through verified OPC edges and content types. No original
part is removed; decisions are included in the extraction manifest. This does
not evaluate Word rendering, list numbering, fields or external resources.
"""
from __future__ import annotations

import re

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
O = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
R = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
BIB = "http://schemas.openxmlformats.org/officeDocument/2006/bibliography"
DS = "http://schemas.openxmlformats.org/officeDocument/2006/customXml"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
EFFECTS = "http://schemas.microsoft.com/office/2007/relationships/stylesWithEffects"
WP = "application/vnd.openxmlformats-officedocument.wordprocessingml."

# relationship type -> expected source, MIME type, expanded XML root
POLICIES = {
    O + "styles": ("word/document.xml", WP + "styles+xml", f"{{{W}}}styles"),
    EFFECTS: ("word/document.xml", "application/vnd.ms-word.stylesWithEffects+xml", f"{{{W}}}styles"),
    **{O + key: ("word/document.xml", WP + key + "+xml", f"{{{W}}}{root}")
       for key, root in (("settings", "settings"), ("fontTable", "fonts"), ("webSettings", "webSettings"), ("numbering", "numbering"))},
    O + "theme": ("word/document.xml", "application/vnd.openxmlformats-officedocument.theme+xml", f"{{{A}}}theme"),
    O + "customXml": ("word/document.xml", "application/xml", f"{{{BIB}}}Sources"),
    O + "customXmlProps": (None, "application/vnd.openxmlformats-officedocument.customXmlProperties+xml", f"{{{DS}}}datastoreItem"),
    R + "/metadata/thumbnail": ("", "image/jpeg", None),
    R + "/metadata/core-properties": ("", "application/vnd.openxmlformats-package.core-properties+xml", "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}coreProperties"),
    O + "extended-properties": ("", "application/vnd.openxmlformats-officedocument.extended-properties+xml", "{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}Properties"),
    O + "custom-properties": ("", "application/vnd.openxmlformats-officedocument.custom-properties+xml", "{http://schemas.openxmlformats.org/officeDocument/2006/custom-properties}Properties"),
}


def relationship_source(name):
    if name == "_rels/.rels":
        return ""
    if "/_rels/" not in name or not name.endswith(".rels"):
        return None
    directory, tail = name.rsplit("/_rels/", 1)
    return directory + "/" + tail[:-5]


def _empty_text(node):
    return not (node.text or "").strip() and not (node.tail or "").strip()


def _tree(node):
    # Ignore indentation only. Text values, attributes and child order remain.
    return (node.tag, tuple(sorted(node.attrib.items())),
            node.text if (node.text or "").strip() else "",
            node.tail if (node.tail or "").strip() else "",
            tuple(_tree(child) for child in node))


def inspect_package(parts, roots, edges, content_types, debt):
    """Return resolved style stores and body-free semantic manifest decisions."""
    defaults, overrides = {}, {}
    for child in content_types:
        if child.tag == f"{{{CT}}}Default":
            key, value = child.get("Extension"), child.get("ContentType")
            target = defaults
            attrs = {"Extension", "ContentType"}
        elif child.tag == f"{{{CT}}}Override":
            key, value = child.get("PartName"), child.get("ContentType")
            target = overrides
            attrs = {"PartName", "ContentType"}
        else:
            debt("DOCX_CONTENT_TYPES_INVALID", "[Content_Types].xml", "/Types")
            continue
        if not key or not value or key in target or set(child.attrib) != attrs or list(child) or not _empty_text(child):
            debt("DOCX_CONTENT_TYPES_INVALID", "[Content_Types].xml", "/Types")
        target[key] = value
    if content_types.attrib or not _empty_text(content_types):
        debt("DOCX_CONTENT_TYPES_INVALID", "[Content_Types].xml", "/Types")
    incoming, outgoing, by_type = {}, {}, {}
    for source, kind, target, relpart, relid in edges:
        incoming.setdefault(target, []).append((source, kind))
        outgoing.setdefault(source, []).append((kind, target))
        by_type.setdefault(kind, []).append(target)
        if kind == O + "officeDocument" and source == "" and target == "word/document.xml":
            continue
        policy = POLICIES.get(kind)
        if policy is None:
            debt("DOCX_RELATIONSHIP_UNSUPPORTED", relpart, "/Relationships/" + relid)
        elif policy[0] is not None and source != policy[0]:
            debt("DOCX_PROFILE_PART_UNSUPPORTED", relpart, "/Relationships/" + relid)
    for kind, targets in by_type.items():
        if kind in POLICIES and kind not in {O + "customXml", O + "customXmlProps"} and len(targets) != 1:
            debt("DOCX_PROFILE_PART_UNSUPPORTED", "_rels/.rels", "/Relationships/duplicate-type")
    decisions, stores = [], {}
    guids = set()
    for name, raw in parts.items():
        if name in {"[Content_Types].xml", "word/document.xml"} or name.endswith(".rels"):
            continue
        ins = incoming.get(name, [])
        if len(ins) != 1 or ins[0][1] not in POLICIES:
            debt("DOCX_PART_UNSUPPORTED", name, "/")
            continue
        source, kind = ins[0]
        policy = POLICIES[kind]
        mime = overrides.get("/" + name, defaults.get(name.rsplit(".", 1)[-1]))
        root = roots.get(name)
        if mime != policy[1] or (policy[2] is not None and (root is None or root.tag != policy[2])):
            debt("DOCX_PROFILE_PART_UNSUPPORTED", name, "/")
            continue
        if outgoing.get(name) and kind != O + "customXml":
            debt("DOCX_PROFILE_PART_UNSUPPORTED", name, "/relationships")
        if root is not None and (set(root.attrib) - {MC + "Ignorable"}) and kind in {O + "styles", EFFECTS, O + "webSettings", O + "numbering", O + "settings", O + "fontTable"}:
            debt("DOCX_MARKUP_UNSUPPORTED", name, "/")
        if kind in {O + "styles", EFFECTS}:
            stores[kind] = (name, root)
            decision = "effective-style-store"
        elif kind == O + "webSettings":
            seen = set()
            for node in root:
                if node.tag not in {f"{{{W}}}allowPNG", f"{{{W}}}doNotSaveAsSingleFile"} or node.tag in seen or list(node) or not _empty_text(node) or set(node.attrib) - {f"{{{W}}}val"} or node.get(f"{{{W}}}val", "true") not in {"true", "false", "on", "off", "1", "0"}:
                    debt("DOCX_MARKUP_UNSUPPORTED", name, "/webSettings")
                seen.add(node.tag)
            if not _empty_text(root):
                debt("DOCX_MARKUP_UNSUPPORTED", name, "/webSettings")
            decision = "web-output-options-only"
        elif kind == O + "numbering":
            # The caller separately rejects every effective numPr in main and
            # both style stores, so definitions cannot supply evidence text.
            decision = "numbering-definitions-unused-by-body"
        elif kind == O + "customXml":
            if list(root) or not _empty_text(root) or set(root.attrib) - {"SelectedStyle", "StyleName"} or len(outgoing.get(name, [])) != 1 or outgoing[name][0][0] != O + "customXmlProps":
                debt("DOCX_CUSTOM_XML_UNRESOLVED", name, "/Sources")
            decision = "empty-bibliography-no-body-binding"
        elif kind == O + "customXmlProps":
            guid = root.get(f"{{{DS}}}itemID", "").upper()
            refs = list(root)
            if (source not in by_type.get(O + "customXml", []) or not re.fullmatch(r"\{[0-9A-F]{8}(?:-[0-9A-F]{4}){3}-[0-9A-F]{12}\}", guid) or guid in guids or set(root.attrib) != {f"{{{DS}}}itemID"} or not _empty_text(root) or len(refs) != 1 or refs[0].tag != f"{{{DS}}}schemaRefs" or refs[0].attrib or not _empty_text(refs[0]) or len(refs[0]) != 1):
                debt("DOCX_CUSTOM_XML_UNRESOLVED", name, "/datastoreItem")
            else:
                ref = refs[0][0]
                if ref.tag != f"{{{DS}}}schemaRef" or ref.attrib != {f"{{{DS}}}uri": BIB} or list(ref) or not _empty_text(ref):
                    debt("DOCX_CUSTOM_XML_UNRESOLVED", name, "/datastoreItem/schemaRefs")
            guids.add(guid)
            decision = "unique-empty-bibliography-schema"
        elif kind == R + "/metadata/thumbnail":
            if source != "" or not raw.startswith(b"\xff\xd8\xff") or not raw.rstrip(b"\x00").endswith(b"\xff\xd9"):
                debt("DOCX_THUMBNAIL_UNRESOLVED", name, "/")
            decision = "package-only-jpeg-thumbnail"
        elif kind in {O + "settings", O + "fontTable", O + "theme"}:
            _ancillary_vocabulary(root, kind, name, debt)
            decision = "bounded-body-independent-definitions"
        else:
            # Property parts cannot contain Word/drawing/custom binding nodes.
            if any(node.tag.startswith((f"{{{W}}}", f"{{{A}}}", f"{{{DS}}}")) for node in root.iter()):
                debt("DOCX_MARKUP_UNSUPPORTED", name, "/metadata")
            decision = "package-metadata"
        decisions.append({"part": name, "role": kind, "assessment": decision})
    if EFFECTS in stores and O + "styles" not in stores:
        debt("DOCX_STYLE_UNRESOLVED", stores[EFFECTS][0], "/styles/missing-primary")
    return stores, sorted(decisions, key=lambda value: value["part"])


def _ancillary_vocabulary(root, kind, part, debt):
    # Closed vocabularies. Unsupported consumers in main/styles are separately
    # rejected; these definitions never become extracted body paragraphs.
    if kind == O + "theme":
        names = "theme themeElements clrScheme dk1 lt1 dk2 lt2 accent1 accent2 accent3 accent4 accent5 accent6 hlink folHlink sysClr srgbClr fontScheme majorFont minorFont latin ea cs font fmtScheme fillStyleLst solidFill schemeClr tint shade satMod gradFill gsLst gs alpha lin path fillToRect lnStyleLst ln prstDash effectStyleLst effectStyle effectLst outerShdw scene3d camera lightRig rot sp3d bevelT bgFillStyleLst objectDefaults spDef spPr bodyPr lstStyle style lnRef fillRef effectRef fontRef lnDef extraClrSchemeLst".split()
        vocabulary = {f"{{{A}}}{name}" for name in names}
        attrs = set("algn ang b blurRad cap cmpd dir dist h idx l lastClr lat lon name path pos prst r rev rig rotWithShape scaled script t typeface val w".split())
    elif kind == O + "fontTable":
        vocabulary = {f"{{{W}}}{name}" for name in "fonts font altName panose1 charset family notTrueType pitch sig".split()}
        attrs = {f"{{{W}}}{name}" for name in "name val usb0 usb1 usb2 usb3 csb0 csb1".split()}
    else:
        vocabulary = {f"{{{W}}}{name}" for name in "settings zoom proofState defaultTabStop characterSpacingControl savePreviewPicture compat useFELayout compatSetting rsids rsidRoot rsid themeFontLang clrSchemeMapping doNotAutoCompressPictures shapeDefaults decimalSymbol listSeparator".split()}
        attrs = {f"{{{W}}}{name}" for name in "val spelling grammar name uri eastAsia bg1 t1 bg2 t2 accent1 accent2 accent3 accent4 accent5 accent6 hyperlink followedHyperlink".split()}
        math = "http://schemas.openxmlformats.org/officeDocument/2006/math"
        w14 = "http://schemas.microsoft.com/office/word/2010/wordml"
        vocabulary |= {f"{{{math}}}{name}" for name in "mathPr mathFont brkBin brkBinSub smallFrac dispDef lMargin rMargin defJc wrapIndent intLim naryLim".split()}
        vocabulary |= {f"{{{w14}}}docId", f"{{{w14}}}defaultImageDpi"}
        vocabulary |= {"{urn:schemas-microsoft-com:office:office}" + name for name in ("shapedefaults", "shapelayout", "idmap")}
        attrs |= {f"{{{math}}}val", f"{{{w14}}}val", "{urn:schemas-microsoft-com:vml}ext", "spidmax", "data"}
    for node in root.iter():
        allowed = attrs | ({MC + "Ignorable"} if node is root else set())
        if node.tag not in vocabulary or set(node.attrib) - allowed or not _empty_text(node):
            debt("DOCX_MARKUP_UNSUPPORTED", part, "/ancillary-vocabulary")
        if node.tag == f"{{{W}}}compatSetting" and (node.get(f"{{{W}}}name") not in {"compatibilityMode", "overrideTableStyleFontSizeAndJustification", "enableOpenTypeFeatures", "doNotFlipMirrorIndents"} or node.get(f"{{{W}}}uri") != "http://schemas.microsoft.com/office/word"):
            debt("DOCX_MARKUP_UNSUPPORTED", part, "/settings/compat")
