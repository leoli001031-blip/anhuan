#!/usr/bin/env python3
"""Produce deterministic synthetic F0-F PDF/JPEG envelopes without real data."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys


def _minimal_pdf() -> bytes:
    stream = b"BT /F1 36 Tf 72 600 Td (F0F SYNTHETIC 123) Tj ET\n"
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
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (640, 240), "white")
    if with_text:
        draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.load_default(size=52)
        except TypeError:
            font = ImageFont.load_default()
        draw.text((36, 78), "F0F SYNTHETIC 456", fill="black", font=font)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=90, optimize=False, progressive=False)
    return output.getvalue()


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
            f"f0f-synthetic-{kind.lower()}-page-1".encode("ascii")
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
        header.update({"image_width_px": 640, "image_height_px": 240})
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
