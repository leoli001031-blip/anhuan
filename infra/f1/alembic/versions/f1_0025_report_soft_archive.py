"""Report soft archive: management attribute columns and guard.

Soft archive is an internal management attribute, not a publication state.
Archiving a published report requires explicit withdrawal first.  Recovery
does not auto-publish.  Versions, citations, and audit trails are preserved.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f1_0025"
down_revision: str | None = "f1_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE f1.analysis_report "
        "ADD COLUMN IF NOT EXISTS archived_at timestamptz"
    )
    op.execute(
        "ALTER TABLE f1.analysis_report "
        "ADD COLUMN IF NOT EXISTS archived_by_user_id uuid"
    )
    op.execute(
        "ALTER TABLE f1.analysis_report "
        "ADD COLUMN IF NOT EXISTS archived_reason text"
    )
    op.execute(
        "ALTER TABLE f1.analysis_report "
        "ADD CONSTRAINT analysis_report_archived_actor_fk "
        "FOREIGN KEY (enterprise_id, archived_by_user_id) "
        "REFERENCES f1.enterprise_user (enterprise_id, user_id)"
    )
    op.execute(
        "ALTER TABLE f1.analysis_report "
        "ADD CONSTRAINT analysis_report_archived_reason_ck "
        "CHECK (archived_reason IS NULL OR char_length(archived_reason) BETWEEN 1 AND 500)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE f1.analysis_report "
        "DROP CONSTRAINT IF EXISTS analysis_report_archived_reason_ck"
    )
    op.execute(
        "ALTER TABLE f1.analysis_report "
        "DROP CONSTRAINT IF EXISTS analysis_report_archived_actor_fk"
    )
    op.execute(
        "ALTER TABLE f1.analysis_report "
        "DROP COLUMN IF EXISTS archived_reason"
    )
    op.execute(
        "ALTER TABLE f1.analysis_report "
        "DROP COLUMN IF EXISTS archived_by_user_id"
    )
    op.execute(
        "ALTER TABLE f1.analysis_report "
        "DROP COLUMN IF EXISTS archived_at"
    )
