from __future__ import annotations

import hashlib
import io
import json
import logging
import sys
import tempfile
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from fixture_page_planner import (
    PlannerFailure,
    build_page_plan,
    render_status_html,
)
from fixture_router import RouteFailure, build_route_plan


def _pdf_bytes(text: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 72 700 Td ({text}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _json_bytes(value: dict[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _run(
    source: Path,
    core_manifest: Path,
    negative_manifest: Path,
    route_plan: Path,
) -> tuple[int, dict[str, object] | None]:
    try:
        plan = build_page_plan(
            source_root=source,
            core_manifest=core_manifest,
            negative_manifest=negative_manifest,
            route_plan_path=route_plan,
            profile="full",
        )
    except (PlannerFailure, RouteFailure):
        return 2, None
    return 0, plan


def main() -> int:
    body_canary = "PRIVATE_REVERSE_BODY_13800138000"
    core_bytes = _pdf_bytes(body_canary + "A" * 20)
    negative_bytes = _pdf_bytes("N" * 20)
    audit = {"external": 0, "ocr": 0}
    external_events = (
        "socket.",
        "subprocess.",
        "os.system",
        "os.posix_spawn",
        "os.spawn",
        "os.exec",
    )
    ocr_markers = ("paddleocr", "tesseract", "easyocr", "rapidocr")

    def audit_hook(event: str, args: tuple[object, ...]) -> None:
        if event.startswith(external_events):
            audit["external"] += 1
            lowered = " ".join(str(argument).casefold() for argument in args)
            if any(marker in lowered for marker in ocr_markers):
                audit["ocr"] += 1
        elif event == "import" and args:
            module_name = str(args[0]).casefold()
            if any(marker in module_name for marker in ocr_markers):
                audit["ocr"] += 1

    sys.addaudithook(audit_hook)
    log_stream = io.StringIO()
    log_handler = logging.StreamHandler(log_stream)
    root_logger = logging.getLogger()
    root_logger.addHandler(log_handler)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        source = root / "source"
        source.mkdir()
        core_path = source / "core.pdf"
        negative_path = source / "negative.pdf"
        core_path.write_bytes(core_bytes)
        negative_path.write_bytes(negative_bytes)
        core_manifest = root / "core.sha256"
        negative_manifest = root / "negative.sha256"
        core_manifest.write_text(
            f"{hashlib.sha256(core_bytes).hexdigest()}  core.pdf\n",
            encoding="utf-8",
        )
        negative_manifest.write_text(
            f"{hashlib.sha256(negative_bytes).hexdigest()}  negative.pdf\n",
            encoding="utf-8",
        )
        route_plan = root / "route-plan.json"
        route_plan.write_bytes(
            _json_bytes(
                build_route_plan(
                    source_root=source,
                    core_manifest=core_manifest,
                    negative_manifest=negative_manifest,
                    profile="full",
                )
            )
        )

        valid_exit, valid_plan = _run(
            source, core_manifest, negative_manifest, route_plan
        )
        core_path.write_bytes(core_bytes + b"X")
        tampered_exit, _ = _run(
            source, core_manifest, negative_manifest, route_plan
        )
        core_path.write_bytes(core_bytes)
        restored_exit, restored_plan = _run(
            source, core_manifest, negative_manifest, route_plan
        )

        serialized = b"" if valid_plan is None else _json_bytes(valid_plan)
        status = b"" if valid_plan is None else render_status_html(valid_plan).encode("utf-8")
        observed = status + serialized + log_stream.getvalue().encode("utf-8")
        body_leaks = int(body_canary.encode("ascii") in observed)
        external_calls = audit["external"] + int(
            restored_plan is None
            or restored_plan.get("external_processing") != "DENY"
        )
        ocr_calls = audit["ocr"] + int(
            restored_plan is None or restored_plan.get("ocr_executed") is not False
        )

    root_logger.removeHandler(log_handler)

    print(f"valid_exit={valid_exit}")
    print(f"tampered_exit={tampered_exit}")
    print(f"restored_exit={restored_exit}")
    print(f"body_leaks={body_leaks}")
    print(f"external_calls={external_calls}")
    print(f"ocr_calls={ocr_calls}")
    return 0 if (
        valid_exit,
        tampered_exit,
        restored_exit,
        body_leaks,
        external_calls,
        ocr_calls,
    ) == (0, 2, 0, 0, 0, 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
