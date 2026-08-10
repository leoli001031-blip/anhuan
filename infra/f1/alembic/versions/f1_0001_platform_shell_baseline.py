"""f1 platform shell baseline

Independent Alembic root (moved out of the frozen F0 tree unchanged in DDL).
Revision ID: 6c9461830342
Revises: (none — this is the F1 branch root)
Create Date: 2026-08-08 02:51:19.774141
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f1_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS f1 AUTHORIZATION f0d_migration")
    op.execute("REVOKE ALL ON SCHEMA f1 FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA f1 TO f0d_runtime")

    op.execute(
        """
        CREATE TABLE f1.enterprise (
          id uuid PRIMARY KEY,
          name text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
          license_no text NOT NULL CHECK (length(license_no) BETWEEN 1 AND 64),
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.plant (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          name text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
          address text,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.user_profile (
          id uuid PRIMARY KEY,
          keycloak_sub text NOT NULL UNIQUE,
          email text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.enterprise_user (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          user_id uuid NOT NULL REFERENCES f1.user_profile(id),
          role text NOT NULL CHECK (role IN (
            'super_admin','enterprise_admin','plant_admin','partner','auditor'
          )),
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          UNIQUE (enterprise_id, user_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.document (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          plant_id uuid REFERENCES f1.plant(id),
          object_key text NOT NULL,
          filename text NOT NULL,
          size bigint NOT NULL CHECK (size >= 0),
          content_type text NOT NULL,
          status text NOT NULL DEFAULT 'pending' CHECK (status IN (
            'pending','scanning','indexing','done','failed'
          )),
          created_at timestamptz NOT NULL DEFAULT statement_timestamp()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.audit_log (
          id uuid PRIMARY KEY,
          user_sub text NOT NULL,
          action text NOT NULL,
          resource_type text NOT NULL,
          resource_id text,
          result text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp()
        )
        """
    )



def downgrade() -> None:
    op.execute("DROP SCHEMA f1 CASCADE")