from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from fixture_router import RouteFailure, build_route_plan


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _valid_pdf(marker: bytes) -> bytes:
    prefix = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n" + marker + b"\n"
    xref_offset = len(prefix)
    return prefix + (
        b"xref\n0 1\n0000000000 65535 f \n"
        b"trailer\n<< /Size 1 >>\nstartxref\n"
        + str(xref_offset).encode("ascii")
        + b"\n%%EOF\n"
    )


def _write_manifest(path: Path, relative_path: str, data: bytes) -> None:
    path.write_text(f"{_digest(data)}  {relative_path}\n", encoding="utf-8")


def _route_exit(
    source: Path, core_manifest: Path, negative_manifest: Path
) -> int:
    try:
        build_route_plan(
            source_root=source,
            core_manifest=core_manifest,
            negative_manifest=negative_manifest,
            profile="full",
        )
    except RouteFailure:
        return 2
    return 0


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve()
        source = base / "source"
        source.mkdir()
        core_manifest = base / "core.sha256"
        negative_manifest = base / "negative.sha256"
        core_data = _valid_pdf(b"core")
        negative_data = _valid_pdf(b"negative")
        core_path = source / "core.pdf"
        core_path.write_bytes(core_data)
        (source / "negative.pdf").write_bytes(negative_data)
        _write_manifest(core_manifest, "core.pdf", core_data)
        _write_manifest(negative_manifest, "negative.pdf", negative_data)

        valid_exit = _route_exit(source, core_manifest, negative_manifest)
        core_path.write_bytes(core_data + b"tampered")
        tampered_exit = _route_exit(source, core_manifest, negative_manifest)
        core_path.write_bytes(core_data)
        restored_exit = _route_exit(source, core_manifest, negative_manifest)

    print(f"valid_exit={valid_exit}")
    print(f"tampered_exit={tampered_exit}")
    print(f"restored_exit={restored_exit}")
    return 0 if (valid_exit, tampered_exit, restored_exit) == (0, 2, 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
