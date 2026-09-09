"""Non-executing, bounded OPC reader for native spreadsheet evidence."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import posixpath
import re
import zipfile

from defusedxml import ElementTree as SafeXML

CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
R = 'http://schemas.openxmlformats.org/package/2006/relationships'
O = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/'


class PackageError(ValueError):
    """Only fixed reason codes; original names/body never enter exceptions."""


@dataclass(frozen=True, slots=True)
class PackageLimits:
    source_bytes: int = 25 * 1024 * 1024
    entries: int = 2048
    entry_bytes: int = 16 * 1024 * 1024
    expanded_bytes: int = 128 * 1024 * 1024
    compression_ratio: int = 100
    xml_nodes: int = 400_000
    xml_depth: int = 128

    def __post_init__(self):
        if any(type(getattr(self, key)) is not int or getattr(self, key) < 1 for key in self.__dataclass_fields__):
            raise ValueError('OOXML_LIMITS_INVALID')


def read_package(source, expected_sha256, *, limits=PackageLimits()):
    if not isinstance(source, bytes) or not 1 <= len(source) <= limits.source_bytes:
        raise PackageError('OOXML_SOURCE_BUDGET_EXCEEDED')
    source_sha = hashlib.sha256(source).hexdigest()
    if source_sha != expected_sha256:
        raise PackageError('OOXML_SOURCE_SHA_MISMATCH')
    parts, roots, manifest, edges = {}, {}, [], []
    try:
        with zipfile.ZipFile(io.BytesIO(source)) as archive:
            infos = archive.infolist()
            names = {info.filename for info in infos}
            if not infos or len(infos) > limits.entries or len(names) != len(infos):
                raise PackageError('OOXML_PACKAGE_ENTRY_LIMIT')
            expanded, nodes = 0, 0
            for info in infos:
                name = info.filename
                if name.startswith('/') or any(p in {'', '.', '..'} for p in name.split('/')) or any(c in name for c in ('\\', ':', '%', '?', '#', '\x00')) or info.flag_bits & 1 or re.search(r'\.(zip|7z|rar|tar|gz|docx|xlsx|docm|xlsm)$', name, re.I) or 'vbaproject' in name.lower() or '/embeddings/' in f'/{name}':
                    raise PackageError('OOXML_UNSAFE_PART')
                expanded += info.file_size
                if info.file_size > limits.entry_bytes or expanded > limits.expanded_bytes or (info.file_size and info.file_size > info.compress_size * limits.compression_ratio):
                    raise PackageError('OOXML_PACKAGE_SIZE_LIMIT')
            for info in infos:
                with archive.open(info) as stream:
                    raw = stream.read(limits.entry_bytes + 1)
                if len(raw) != info.file_size or len(raw) > limits.entry_bytes:
                    raise PackageError('OOXML_PACKAGE_SIZE_LIMIT')
                parts[info.filename] = raw
                manifest.append({'part': info.filename, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
                if info.filename.endswith(('.xml', '.rels')):
                    depth = 0
                    try:
                        parser = SafeXML.iterparse(io.BytesIO(raw), events=('start', 'end'), forbid_dtd=True, forbid_entities=True, forbid_external=True)
                        for event, _node in parser:
                            if event == 'start':
                                depth += 1
                                nodes += 1
                                if depth > limits.xml_depth or nodes > limits.xml_nodes:
                                    raise PackageError('OOXML_XML_BUDGET_EXCEEDED')
                            else:
                                depth -= 1
                        roots[info.filename] = parser.root
                    except PackageError:
                        raise
                    except Exception:
                        raise PackageError('OOXML_XML_UNSAFE_OR_CORRUPT') from None
        types = roots.get('[Content_Types].xml')
        if types is None or types.tag != f'{{{CT}}}Types' or types.attrib or (types.text or '').strip():
            raise PackageError('OOXML_CONTENT_TYPES_INVALID')
        defaults, overrides = {}, {}
        for item in types:
            is_default = item.tag == f'{{{CT}}}Default'
            if not is_default and item.tag != f'{{{CT}}}Override':
                raise PackageError('OOXML_CONTENT_TYPES_INVALID')
            field, table = ('Extension', defaults) if is_default else ('PartName', overrides)
            key, value = item.get(field), item.get('ContentType')
            if not key or not value or key in table or set(item.attrib) != {field, 'ContentType'} or list(item) or (item.text or '').strip() or (item.tail or '').strip():
                raise PackageError('OOXML_CONTENT_TYPES_INVALID')
            if not is_default and (not key.startswith('/') or key[1:] not in parts):
                raise PackageError('OOXML_CONTENT_TYPES_INVALID')
            table[key] = value
        content_types = {name: overrides.get('/' + name, defaults.get(name.rsplit('.', 1)[-1])) for name in parts if name != '[Content_Types].xml'}
        for name, root in roots.items():
            if not name.endswith('.rels'):
                continue
            if name == '_rels/.rels':
                origin = ''
            elif '/_rels/' in name:
                directory, tail = name.rsplit('/_rels/', 1)
                origin = directory + '/' + tail[:-5]
            else:
                raise PackageError('OOXML_RELATIONSHIP_INVALID')
            if (origin and origin not in parts) or root.tag != f'{{{R}}}Relationships' or root.attrib or (root.text or '').strip():
                raise PackageError('OOXML_RELATIONSHIP_INVALID')
            ids = set()
            for rel in root:
                rid, target, kind = rel.get('Id'), rel.get('Target', ''), rel.get('Type')
                if rel.tag != f'{{{R}}}Relationship' or not rid or rid in ids or not kind or set(rel.attrib) - {'Id', 'Type', 'Target', 'TargetMode'} or list(rel) or (rel.text or '').strip() or (rel.tail or '').strip():
                    raise PackageError('OOXML_RELATIONSHIP_INVALID')
                ids.add(rid)
                if rel.get('TargetMode', 'Internal') != 'Internal':
                    raise PackageError('OOXML_EXTERNAL_RELATIONSHIP')
                if not target or any(c in target for c in ('\\', ':', '%', '?', '#', '\x00')):
                    raise PackageError('OOXML_RELATIONSHIP_INVALID')
                resolved = target[1:] if target.startswith('/') else posixpath.normpath(posixpath.join(posixpath.dirname(origin), target))
                if resolved.startswith('../') or resolved not in parts:
                    raise PackageError('OOXML_RELATIONSHIP_TARGET_MISSING')
                edges.append((origin, rid, kind, resolved))
        return source_sha, parts, roots, content_types, edges, sorted(manifest, key=lambda item: item['part'])
    except PackageError:
        raise
    except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile, NotImplementedError):
        raise PackageError('OOXML_PACKAGE_CORRUPT') from None
