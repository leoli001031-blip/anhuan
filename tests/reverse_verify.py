from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from fixture_gate import ValidationFailure, verify_fixture_set


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _exit_code(source: Path, core: Path, negative: Path) -> int:
    try:
        verify_fixture_set(
            source_root=source,
            core_manifest=core,
            negative_manifest=negative,
        )
    except ValidationFailure:
        return 2
    return 0


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory).resolve()
        source = base / "source"
        source.mkdir()
        sample = source / "sample.txt"
        negative_file = source / "negative.txt"
        original = b"fixture"
        sample.write_bytes(original)
        negative_file.write_bytes(b"negative")

        core = base / "core.sha256"
        negative = base / "negative.sha256"
        core.write_text(f"{_sha256(original)}  sample.txt\n", encoding="utf-8")
        negative.write_text(
            f"{_sha256(b'negative')}  negative.txt\n", encoding="utf-8"
        )

        valid_exit = _exit_code(source, core, negative)
        sample.write_bytes(b"Fixture")
        tampered_exit = _exit_code(source, core, negative)
        sample.write_bytes(original)
        restored_exit = _exit_code(source, core, negative)

    print(f"valid_exit={valid_exit}")
    print(f"tampered_exit={tampered_exit}")
    print(f"restored_exit={restored_exit}")
    return 0 if (valid_exit, tampered_exit, restored_exit) == (0, 2, 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
