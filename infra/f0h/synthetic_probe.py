#!/usr/bin/env python3
"""Produce deterministic synthetic F0-H PDF/JPEG envelopes without real data."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys


_TEXT_JPEG_B64 = (
    "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"
    "ERERERERERERERERERERERERERERERERERERERERERERERERERERERERERERERERERERERERERER"
    "ERERERERERERERERERERERERERERERERERERERF//9k="
)
_BLANK_JPEG_B64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAoHBwgHBgoICAgLCgoLDhgQDg0NDh0VFhEYIx8lJCIf"
    "IiEmKzcvJik0KSEiMEExNDk7Pj4+JS5ESUM8SDc9Pjv/wAALCABkAUABAREA/8QAFQABAQAAAAAA"
    "AAAAAAAAAAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAA/ALMAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAA/9k="
)


def _minimal_pdf() -> bytes:
    stream = b"BT /F1 36 Tf 72 600 Td (F0H SYNTHETIC 123) Tj ET\n"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/CropBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> "
            b"/Contents 4 0 R >>"
        ),
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    body = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, value in enumerate(objects, 1):
        offsets.append(len(body))
        body.extend(f"{number} 0 obj\n".encode("ascii"))
        body.extend(value)
        body.extend(b"\nendobj\n")
    xref_offset = len(body)
    body.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    body.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        body.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    body.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(body)


def _jpeg(with_text: bool) -> bytes:
    encoded = _TEXT_JPEG_B64 if with_text else _BLANK_JPEG_B64
    return base64.b64decode(encoded, validate=True)


def _envelope(kind: str, tamper_hash: bool) -> bytes:
    document_type = "PDF" if kind == "PDF" else "JPEG"
    source = _minimal_pdf() if kind == "PDF" else _jpeg(kind == "JPEG")
    source_sha256 = hashlib.sha256(source).hexdigest()
    if tamper_hash:
        source_sha256 = "0" * 64
    header = {
        "schema": "f0e-envelope-v1",
        "document_type": document_type,
        "source_sha256": source_sha256,
        "source_size": len(source),
        "expected_total_pages": 1,
        "page_no": 1,
        "source_unit_id": hashlib.sha256(
            f"f0h-synthetic-{kind.lower()}-page-1".encode("ascii")
        ).hexdigest(),
    }
    if document_type == "PDF":
        header.update(
            {
                "media_box": {"left": "0.000", "bottom": "0.000", "right": "612.000", "top": "792.000"},
                "crop_box": {"left": "0.000", "bottom": "0.000", "right": "612.000", "top": "792.000"},
                "rotation_degrees": 0,
            }
        )
    else:
        header.update({"image_width_px": 320, "image_height_px": 100})
    header_bytes = json.dumps(
        header, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    return len(header_bytes).to_bytes(4, "big") + header_bytes + source


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("document_type", choices=("PDF", "JPEG", "JPEG_BLANK"))
    parser.add_argument("--tamper-hash", action="store_true")
    args = parser.parse_args()
    sys.stdout.buffer.write(_envelope(args.document_type, args.tamper_hash))


if __name__ == "__main__":
    main()
