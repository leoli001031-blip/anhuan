"""Deterministic canonical-leaf and parent/child chunk construction."""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
import unicodedata

from .contracts import (
    BlockDraft,
    CANONICAL_TEXT_RULE,
    CHUNK_RULE,
    CanonicalUnitDraft,
    ChunkBlockLinkDraft,
    ChunkDraft,
    F0IError,
    IdentityBinding,
    LEAF_RULE,
    LeafInput,
    MAX_CANONICAL_BYTES,
    MAX_CANONICAL_CHARACTERS,
    MAX_CHILD_CHUNKS,
    MAX_LEAF_BLOCKS,
    SensitiveCanonicalBody,
    Utf8Span,
    canonical_sha256,
    chain_sha256,
    stable_uuid4,
)


CHILD_MIN_CHARACTERS = 300
CHILD_MAX_CHARACTERS = 800


def canonicalize_text(value: str) -> str:
    """Normalize only the frozen F0-I NFC/LF representation."""

    if not isinstance(value, str):
        raise F0IError("CANONICAL_BODY_INVALID")
    try:
        normalized = unicodedata.normalize(
            "NFC", value.replace("\r\n", "\n").replace("\r", "\n")
        )
        normalized.encode("utf-8", errors="strict")
        return normalized
    except UnicodeError:
        raise F0IError("CANONICAL_BODY_INVALID") from None


def build_leaf_blocks(
    binding: IdentityBinding,
    leaves: Sequence[LeafInput],
    *,
    maximum_bytes: int = MAX_CANONICAL_BYTES,
    maximum_characters: int = MAX_CANONICAL_CHARACTERS,
) -> tuple[SensitiveCanonicalBody, tuple[BlockDraft, ...]]:
    """Build a canonical body and ordered leaves that cover every byte once."""

    if (
        not isinstance(binding, IdentityBinding)
        or not isinstance(leaves, Sequence)
        or isinstance(leaves, (str, bytes, bytearray, memoryview))
    ):
        raise F0IError("CANONICAL_CONTRACT_INVALID")
    if not 1 <= len(leaves) <= MAX_LEAF_BLOCKS:
        raise F0IError("RESOURCE_LIMIT_EXCEEDED")
    try:
        material = tuple(leaves)
    except TypeError:
        raise F0IError("CANONICAL_CONTRACT_INVALID") from None
    if len(material) != len(leaves) or any(
        not isinstance(leaf, LeafInput) for leaf in material
    ):
        raise F0IError("CANONICAL_CONTRACT_INVALID")

    material_specs: list[tuple[str, str, str, str]] = []
    total_characters = 0
    total_bytes = 0
    for source_ordinal, leaf in enumerate(material, start=1):
        text = canonicalize_text(leaf.text)
        separator = canonicalize_text(leaf.separator_after)
        material_specs.append(
            (text, leaf.block_kind, leaf.locator_kind, leaf.locator_sha256)
        )
        if separator:
            separator_sha256 = hashlib.sha256(
                separator.encode("utf-8", errors="strict")
            ).hexdigest()
            material_specs.append(
                (
                    separator,
                    "CANONICAL_SEPARATOR",
                    "SYNTHETIC_SEPARATOR",
                    canonical_sha256(
                        {
                            "kind": "CANONICAL_JOIN_SEPARATOR",
                            "rule": LEAF_RULE,
                            "separator_sha256": separator_sha256,
                            "source_leaf_ordinal": source_ordinal,
                        }
                    ),
                )
            )
        if len(material_specs) > MAX_LEAF_BLOCKS:
            raise F0IError("RESOURCE_LIMIT_EXCEEDED")
    for payload, _, _, _ in material_specs:
        encoded = payload.encode("utf-8", errors="strict")
        total_characters += len(payload)
        total_bytes += len(encoded)
        if total_characters > maximum_characters or total_bytes > maximum_bytes:
            raise F0IError("CANONICAL_BODY_LIMIT")
    joined = "".join(payload for payload, _, _, _ in material_specs)
    encoded_body = bytearray(joined.encode("utf-8", errors="strict"))
    body: SensitiveCanonicalBody | None = None
    try:
        body = SensitiveCanonicalBody(
            encoded_body,
            maximum_bytes=maximum_bytes,
            maximum_characters=maximum_characters,
        )
        offsets = _utf8_offsets(joined)
        blocks: list[BlockDraft] = []
        start_character = 0
        previous: str | None = None
        identity = binding.identity_payload()
        for ordinal, (
            payload,
            block_kind,
            locator_kind,
            locator_sha256,
        ) in enumerate(material_specs, start=1):
            end_character = start_character + len(payload)
            span = Utf8Span(
                start_byte=offsets[start_character],
                end_byte=offsets[end_character],
                start_character=start_character,
                end_character=end_character,
            )
            plaintext_sha256 = hashlib.sha256(
                body.slice(span.start_byte, span.end_byte)
            ).hexdigest()
            id_payload = {
                "binding": identity,
                "body_sha256": body.sha256,
                "block_kind": block_kind,
                "leaf_rule": LEAF_RULE,
                "locator_kind": locator_kind,
                "locator_sha256": locator_sha256,
                "ordinal": ordinal,
                "plaintext_sha256": plaintext_sha256,
                "span": span.to_dict(),
            }
            block_id = stable_uuid4("f0i.block.v1", id_payload)
            current = chain_sha256(
                "f0i.block-chain.v1",
                previous,
                {"block_id": block_id, **id_payload},
            )
            blocks.append(
                BlockDraft(
                    block_id=block_id,
                    ordinal=ordinal,
                    block_kind=block_kind,
                    locator_kind=locator_kind,
                    locator_sha256=locator_sha256,
                    span=span,
                    plaintext_sha256=plaintext_sha256,
                    plaintext_bytes=span.byte_count,
                    plaintext_characters=span.character_count,
                    previous_chain_sha256=previous,
                    chain_sha256=current,
                )
            )
            previous = current
            start_character = end_character
        _verify_blocks(binding, body, tuple(blocks))
        return body, tuple(blocks)
    except Exception:
        if body is not None:
            body.wipe()
        raise
    finally:
        encoded_body[:] = b"\0" * len(encoded_body)
        encoded_body.clear()
        material_specs.clear()


def build_parent_child_chunks(
    binding: IdentityBinding,
    body: SensitiveCanonicalBody,
    blocks: tuple[BlockDraft, ...],
    *,
    child_min_characters: int = CHILD_MIN_CHARACTERS,
    child_max_characters: int = CHILD_MAX_CHARACTERS,
) -> tuple[
    ChunkDraft,
    tuple[ChunkDraft, ...],
    tuple[ChunkBlockLinkDraft, ...],
    str,
]:
    """Create one parent and contiguous non-overlapping Unicode children."""

    _require_frozen_chunk_rule(child_min_characters, child_max_characters)
    _verify_blocks(binding, body, blocks)
    try:
        text = body.view().tobytes().decode("utf-8", errors="strict")
    except UnicodeError:
        raise F0IError("CANONICAL_RECONSTRUCTION_FAILED") from None
    offsets = _utf8_offsets(text)
    identity = binding.identity_payload()
    full_span = Utf8Span(0, body.byte_count, 0, body.character_count)
    parent_payload = {
        "binding": identity,
        "body_sha256": body.sha256,
        "chunk_level": "PARENT",
        "chunk_rule": CHUNK_RULE,
        "is_tail": False,
        "ordinal": 0,
        "plaintext_sha256": body.sha256,
        "span": full_span.to_dict(),
    }
    parent_id = stable_uuid4("f0i.chunk.v1", parent_payload)
    parent_chain = chain_sha256(
        "f0i.parent-chain.v1", None, {"chunk_id": parent_id, **parent_payload}
    )
    parent = ChunkDraft(
        chunk_id=parent_id,
        parent_chunk_id=None,
        chunk_level="PARENT",
        ordinal=0,
        is_tail=False,
        span=full_span,
        plaintext_sha256=body.sha256,
        plaintext_bytes=body.byte_count,
        plaintext_characters=body.character_count,
        previous_chain_sha256=None,
        chain_sha256=parent_chain,
    )

    ranges = _child_character_ranges(body.character_count, child_max_characters)
    if len(ranges) > MAX_CHILD_CHUNKS:
        raise F0IError("RESOURCE_LIMIT_EXCEEDED")
    children: list[ChunkDraft] = []
    previous: str | None = None
    for ordinal, (start_character, end_character) in enumerate(ranges, start=1):
        span = Utf8Span(
            offsets[start_character],
            offsets[end_character],
            start_character,
            end_character,
        )
        plaintext_sha256 = hashlib.sha256(
            body.slice(span.start_byte, span.end_byte)
        ).hexdigest()
        child_payload = {
            "binding": identity,
            "body_sha256": body.sha256,
            "chunk_level": "CHILD",
            "chunk_rule": CHUNK_RULE,
            "is_tail": ordinal == len(ranges),
            "ordinal": ordinal,
            "parent_chunk_id": parent_id,
            "plaintext_sha256": plaintext_sha256,
            "span": span.to_dict(),
        }
        chunk_id = stable_uuid4("f0i.chunk.v1", child_payload)
        current = chain_sha256(
            "f0i.child-chain.v1",
            previous,
            {"chunk_id": chunk_id, **child_payload},
        )
        children.append(
            ChunkDraft(
                chunk_id=chunk_id,
                parent_chunk_id=parent_id,
                chunk_level="CHILD",
                ordinal=ordinal,
                is_tail=ordinal == len(ranges),
                span=span,
                plaintext_sha256=plaintext_sha256,
                plaintext_bytes=span.byte_count,
                plaintext_characters=span.character_count,
                previous_chain_sha256=previous,
                chain_sha256=current,
            )
        )
        previous = current

    links = _build_links(binding, parent, tuple(children), blocks)
    unit_chain = chain_sha256(
        "f0i.unit-chain.v1",
        None,
        {
            "binding": identity,
            "block_chain_sha256": blocks[-1].chain_sha256,
            "body_sha256": body.sha256,
            "child_chain_sha256": children[-1].chain_sha256,
            "leaf_rule": LEAF_RULE,
            "links_sha256": _links_sha256(links),
            "parent_chain_sha256": parent.chain_sha256,
        },
    )
    return parent, tuple(children), links, unit_chain


def build_canonical_unit(
    binding: IdentityBinding,
    leaves: Sequence[LeafInput],
    *,
    maximum_bytes: int = MAX_CANONICAL_BYTES,
    maximum_characters: int = MAX_CANONICAL_CHARACTERS,
) -> CanonicalUnitDraft:
    """Build and immediately reverse-verify one processing-unit tree."""

    body, blocks = build_leaf_blocks(
        binding,
        leaves,
        maximum_bytes=maximum_bytes,
        maximum_characters=maximum_characters,
    )
    try:
        parent, children, links, unit_chain = build_parent_child_chunks(
            binding, body, blocks
        )
        result = CanonicalUnitDraft(
            binding=binding,
            body=body,
            blocks=blocks,
            parent=parent,
            children=children,
            links=links,
            unit_chain_sha256=unit_chain,
        )
        verify_reconstruction(result)
        return result
    except Exception:
        body.wipe()
        raise


def verify_reconstruction(unit: CanonicalUnitDraft) -> None:
    """Fail unless leaves and children independently reconstruct the body."""

    if not isinstance(unit, CanonicalUnitDraft):
        raise F0IError("CANONICAL_RECONSTRUCTION_FAILED")
    try:
        _verify_blocks(unit.binding, unit.body, unit.blocks)
        _verify_chunks(unit)
        expected_links = _build_links(
            unit.binding, unit.parent, unit.children, unit.blocks
        )
        if expected_links != unit.links:
            raise F0IError("CANONICAL_RECONSTRUCTION_FAILED")
        expected_unit_chain = chain_sha256(
            "f0i.unit-chain.v1",
            None,
            {
                "binding": unit.binding.identity_payload(),
                "block_chain_sha256": unit.blocks[-1].chain_sha256,
                "body_sha256": unit.body.sha256,
                "child_chain_sha256": unit.children[-1].chain_sha256,
                "leaf_rule": LEAF_RULE,
                "links_sha256": _links_sha256(unit.links),
                "parent_chain_sha256": unit.parent.chain_sha256,
            },
        )
        if expected_unit_chain != unit.unit_chain_sha256:
            raise F0IError("CANONICAL_RECONSTRUCTION_FAILED")
    except F0IError as error:
        if error.code == "CANONICAL_BODY_INVALID":
            raise F0IError("CANONICAL_RECONSTRUCTION_FAILED") from None
        raise
    except (TypeError, ValueError, UnicodeError):
        raise F0IError("CANONICAL_RECONSTRUCTION_FAILED") from None


def _verify_blocks(
    binding: IdentityBinding,
    body: SensitiveCanonicalBody,
    blocks: tuple[BlockDraft, ...],
) -> None:
    if (
        not isinstance(binding, IdentityBinding)
        or not isinstance(body, SensitiveCanonicalBody)
        or not isinstance(blocks, tuple)
        or not 1 <= len(blocks) <= MAX_LEAF_BLOCKS
    ):
        raise F0IError("CANONICAL_RECONSTRUCTION_FAILED")
    if tuple(block.ordinal for block in blocks) != tuple(range(1, len(blocks) + 1)):
        raise F0IError("CANONICAL_RECONSTRUCTION_FAILED")
    expected_byte = 0
    expected_character = 0
    previous: str | None = None
    reconstructed = bytearray()
    identity = binding.identity_payload()
    try:
        for block in blocks:
            if (
                block.span.start_byte != expected_byte
                or block.span.start_character != expected_character
            ):
                raise F0IError("CANONICAL_RECONSTRUCTION_FAILED")
            material = body.slice(block.span.start_byte, block.span.end_byte)
            if hashlib.sha256(material).hexdigest() != block.plaintext_sha256:
                raise F0IError("CANONICAL_RECONSTRUCTION_FAILED")
            try:
                decoded = material.tobytes().decode("utf-8", errors="strict")
            except UnicodeError:
                raise F0IError("CANONICAL_RECONSTRUCTION_FAILED") from None
            if len(decoded) != block.plaintext_characters:
                raise F0IError("CANONICAL_RECONSTRUCTION_FAILED")
            id_payload = {
                "binding": identity,
                "body_sha256": body.sha256,
                "block_kind": block.block_kind,
                "leaf_rule": LEAF_RULE,
                "locator_kind": block.locator_kind,
                "locator_sha256": block.locator_sha256,
                "ordinal": block.ordinal,
                "plaintext_sha256": block.plaintext_sha256,
                "span": block.span.to_dict(),
            }
            expected_id = stable_uuid4("f0i.block.v1", id_payload)
            expected_chain = chain_sha256(
                "f0i.block-chain.v1",
                previous,
                {"block_id": expected_id, **id_payload},
            )
            if (
                block.block_id != expected_id
                or block.previous_chain_sha256 != previous
                or block.chain_sha256 != expected_chain
            ):
                raise F0IError("CANONICAL_RECONSTRUCTION_FAILED")
            reconstructed.extend(material)
            previous = expected_chain
            expected_byte = block.span.end_byte
            expected_character = block.span.end_character
        if (
            expected_byte != body.byte_count
            or expected_character != body.character_count
            or reconstructed != body.view()
            or hashlib.sha256(reconstructed).hexdigest() != body.sha256
        ):
            raise F0IError("CANONICAL_RECONSTRUCTION_FAILED")
    finally:
        reconstructed[:] = b"\0" * len(reconstructed)
        reconstructed.clear()


def _verify_chunks(unit: CanonicalUnitDraft) -> None:
    body = unit.body
    identity = unit.binding.identity_payload()
    expected_parent_span = Utf8Span(0, body.byte_count, 0, body.character_count)
    parent_payload = {
        "binding": identity,
        "body_sha256": body.sha256,
        "chunk_level": "PARENT",
        "chunk_rule": CHUNK_RULE,
        "is_tail": False,
        "ordinal": 0,
        "plaintext_sha256": body.sha256,
        "span": expected_parent_span.to_dict(),
    }
    expected_parent_id = stable_uuid4("f0i.chunk.v1", parent_payload)
    expected_parent_chain = chain_sha256(
        "f0i.parent-chain.v1",
        None,
        {"chunk_id": expected_parent_id, **parent_payload},
    )
    if (
        unit.parent.chunk_id != expected_parent_id
        or unit.parent.span != expected_parent_span
        or unit.parent.plaintext_sha256 != body.sha256
        or unit.parent.chain_sha256 != expected_parent_chain
    ):
        raise F0IError("CANONICAL_RECONSTRUCTION_FAILED")

    expected_start_byte = 0
    expected_start_character = 0
    previous: str | None = None
    reconstructed = bytearray()
    try:
        for index, child in enumerate(unit.children):
            if (
                child.ordinal != index + 1
                or child.parent_chunk_id != unit.parent.chunk_id
                or child.is_tail is not (index + 1 == len(unit.children))
                or child.span.start_byte != expected_start_byte
                or child.span.start_character != expected_start_character
                or child.plaintext_characters > CHILD_MAX_CHARACTERS
                or (index + 1 < len(unit.children) and child.plaintext_characters < CHILD_MIN_CHARACTERS)
            ):
                raise F0IError("CANONICAL_RECONSTRUCTION_FAILED")
            material = body.slice(child.span.start_byte, child.span.end_byte)
            plaintext_sha256 = hashlib.sha256(material).hexdigest()
            try:
                decoded = material.tobytes().decode("utf-8", errors="strict")
            except UnicodeError:
                raise F0IError("CANONICAL_RECONSTRUCTION_FAILED") from None
            if (
                len(decoded) != child.plaintext_characters
                or plaintext_sha256 != child.plaintext_sha256
            ):
                raise F0IError("CANONICAL_RECONSTRUCTION_FAILED")
            child_payload = {
                "binding": identity,
                "body_sha256": body.sha256,
                "chunk_level": "CHILD",
                "chunk_rule": CHUNK_RULE,
                "is_tail": child.is_tail,
                "ordinal": child.ordinal,
                "parent_chunk_id": unit.parent.chunk_id,
                "plaintext_sha256": plaintext_sha256,
                "span": child.span.to_dict(),
            }
            expected_id = stable_uuid4("f0i.chunk.v1", child_payload)
            expected_chain = chain_sha256(
                "f0i.child-chain.v1",
                previous,
                {"chunk_id": expected_id, **child_payload},
            )
            if (
                child.chunk_id != expected_id
                or child.previous_chain_sha256 != previous
                or child.chain_sha256 != expected_chain
            ):
                raise F0IError("CANONICAL_RECONSTRUCTION_FAILED")
            reconstructed.extend(material)
            previous = expected_chain
            expected_start_byte = child.span.end_byte
            expected_start_character = child.span.end_character
        if (
            expected_start_byte != body.byte_count
            or expected_start_character != body.character_count
            or reconstructed != body.view()
        ):
            raise F0IError("CANONICAL_RECONSTRUCTION_FAILED")
    finally:
        reconstructed[:] = b"\0" * len(reconstructed)
        reconstructed.clear()


def _build_links(
    binding: IdentityBinding,
    parent: ChunkDraft,
    children: tuple[ChunkDraft, ...],
    blocks: tuple[BlockDraft, ...],
) -> tuple[ChunkBlockLinkDraft, ...]:
    links: list[ChunkBlockLinkDraft] = []
    for chunk in (parent, *children):
        selected: list[tuple[BlockDraft, Utf8Span]] = []
        for block in blocks:
            intersection = _intersection(chunk.span, block.span)
            if intersection is not None:
                selected.append((block, intersection))
        if not selected and len(blocks) == 1 and blocks[0].span.byte_count == 0:
            selected.append((blocks[0], blocks[0].span))
        for ordinal, (block, intersection) in enumerate(selected, start=1):
            link_payload = {
                "binding": binding.identity_payload(),
                "block_id": block.block_id,
                "chunk_id": chunk.chunk_id,
                "intersection_span": intersection.to_dict(),
                "link_ordinal": ordinal,
            }
            links.append(
                ChunkBlockLinkDraft(
                    link_id=stable_uuid4("f0i.chunk-block-link.v1", link_payload),
                    chunk_id=chunk.chunk_id,
                    block_id=block.block_id,
                    link_ordinal=ordinal,
                    intersection_span=intersection,
                )
            )
    return tuple(links)


def _intersection(left: Utf8Span, right: Utf8Span) -> Utf8Span | None:
    start_character = max(left.start_character, right.start_character)
    end_character = min(left.end_character, right.end_character)
    start_byte = max(left.start_byte, right.start_byte)
    end_byte = min(left.end_byte, right.end_byte)
    if start_character < end_character and start_byte < end_byte:
        return Utf8Span(start_byte, end_byte, start_character, end_character)
    if (
        right.character_count == 0
        and right.byte_count == 0
        and left.start_character <= right.start_character <= left.end_character
        and left.start_byte <= right.start_byte <= left.end_byte
    ):
        return right
    return None


def _links_sha256(links: tuple[ChunkBlockLinkDraft, ...]) -> str:
    return canonical_sha256(
        [
            {
                "block_id": link.block_id,
                "chunk_id": link.chunk_id,
                "intersection_span": link.intersection_span.to_dict(),
                "link_id": link.link_id,
                "link_ordinal": link.link_ordinal,
            }
            for link in links
        ]
    )


def _child_character_ranges(total: int, maximum: int) -> tuple[tuple[int, int], ...]:
    if total == 0:
        return ((0, 0),)
    return tuple(
        (start, min(total, start + maximum)) for start in range(0, total, maximum)
    )


def _utf8_offsets(text: str) -> tuple[int, ...]:
    offsets = [0]
    current = 0
    for character in text:
        current += len(character.encode("utf-8", errors="strict"))
        offsets.append(current)
    return tuple(offsets)


def _require_frozen_chunk_rule(minimum: object, maximum: object) -> None:
    if minimum != CHILD_MIN_CHARACTERS or maximum != CHILD_MAX_CHARACTERS:
        raise F0IError("CHUNK_RULE_INVALID")


__all__ = (
    "CHILD_MAX_CHARACTERS",
    "CHILD_MIN_CHARACTERS",
    "build_canonical_unit",
    "build_leaf_blocks",
    "build_parent_child_chunks",
    "canonicalize_text",
    "verify_reconstruction",
)
