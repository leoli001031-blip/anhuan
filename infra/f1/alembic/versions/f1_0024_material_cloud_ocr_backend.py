"""Widen the OCR checkpoint parser-backend closed set for the cloud adapter.

The cloud vision chat OCR adapter persists checkpoints under
``cloud-vision-chat-1`` regardless of the configured vendor.  The local FIFO runtime identity
``f0h-ppocrv6-3.9.2`` stays the audited default; no other backend value is
accepted, and per-version checkpoints of different backends remain distinct
rows under ``material_ocr_checkpoint_identity_uq``.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f1_0024"
down_revision: str | None = "f1_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE f1.material_ocr_checkpoint "
        "DROP CONSTRAINT material_ocr_checkpoint_parser_backend_check"
    )
    op.execute(
        "ALTER TABLE f1.material_ocr_checkpoint "
        "ADD CONSTRAINT material_ocr_checkpoint_parser_backend_check CHECK ("
        "parser_backend IN ('f0h-ppocrv6-3.9.2','cloud-vision-chat-1')"
        ")"
    )


def downgrade() -> None:
    # Cloud-backend checkpoints cannot survive a narrowed closed set; the
    # successor analysis revisions that cite them are immutable, so the rows
    # are removed rather than rewritten.  FIFO checkpoints are untouched.
    op.execute(
        "DELETE FROM f1.material_ocr_checkpoint "
        "WHERE parser_backend NOT IN ('f0h-ppocrv6-3.9.2')"
    )
    op.execute(
        "ALTER TABLE f1.material_ocr_checkpoint "
        "DROP CONSTRAINT material_ocr_checkpoint_parser_backend_check"
    )
    op.execute(
        "ALTER TABLE f1.material_ocr_checkpoint "
        "ADD CONSTRAINT material_ocr_checkpoint_parser_backend_check CHECK ("
        "parser_backend = 'f0h-ppocrv6-3.9.2'"
        ")"
    )
