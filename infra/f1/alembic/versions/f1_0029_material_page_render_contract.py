"""Accept the visible whole-page extraction contract.

Accept whole-page cloud OCR and analysis extraction contract 3 without
relabeling any existing checkpoint or analysis evidence.
"""
from __future__ import annotations

from collections.abc import Sequence
from alembic import op

revision: str = "f1_0029"
down_revision: str | None = "f1_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE f1.material_analysis DROP CONSTRAINT material_analysis_extraction_contract_check")
    op.execute(
        "ALTER TABLE f1.material_analysis ADD CONSTRAINT material_analysis_extraction_contract_check "
        "CHECK (extraction_contract IN (1,2,3))"
    )
    op.execute("ALTER TABLE f1.material_ocr_checkpoint DROP CONSTRAINT material_ocr_checkpoint_parser_backend_check")
    op.execute(
        "ALTER TABLE f1.material_ocr_checkpoint ADD CONSTRAINT material_ocr_checkpoint_parser_backend_check "
        "CHECK (parser_backend IN ('f0h-ppocrv6-3.9.2','cloud-vision-chat-1','cloud-vision-chat-2','cloud-vision-page-3'))"
    )


def downgrade() -> None:
    raise RuntimeError("MATERIAL_PAGE_RENDER_CONTRACT_RESTORE_REQUIRED")
