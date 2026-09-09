"""Bind audit and review evidence to actual database state transitions.

Only AFTER row triggers may write the private transition journal. Event
inserts consume an exact transition by tenant, actor, action and transaction;
an arbitrary UPDATE that only refreshes xmin creates no such transition.
Deferred constraints also prevent committing a transition without its audit
and (for review actions) review evidence. Existing event rows are preserved.

The narrow NOLOGIN definer is provisioned and assigned function ownership by
migrate_f1. It has no business-table UPDATE or event-table INSERT privilege.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f1_0027"
down_revision: str | None = "f1_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE = "f1_report_transition_definer"
_TABLE = "analysis_report_transition"
_EVENTS = (
    "analysis_report_management_event",
    "analysis_report_audit_event",
    "analysis_report_review_event",
    "analysis_report_health_snapshot",
)


def upgrade() -> None:
    op.execute(
        f"""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{_ROLE}') THEN
            RAISE EXCEPTION 'F1_REPORT_TRANSITION_DEFINER_REQUIRED';
          END IF;
          IF EXISTS (
            SELECT 1 FROM pg_roles WHERE rolname='{_ROLE}'
              AND (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole
                   OR rolinherit OR rolreplication OR rolbypassrls)
          ) OR EXISTS (
            SELECT 1 FROM pg_auth_members AS member
            JOIN pg_roles AS role
              ON role.oid=member.roleid OR role.oid=member.member
            WHERE role.rolname='{_ROLE}'
          ) THEN RAISE EXCEPTION 'F1_DEFINER_ROLE_UNSAFE'; END IF;
        END $$
        """
    )
    _journal()
    _version_archive_boundary()
    _capture()
    _bind_events()
    _require_events()
    _grants()


def _journal() -> None:
    op.execute(
        """
        CREATE TABLE f1.analysis_report_transition (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          report_id uuid NOT NULL,
          version_id uuid,
          actor_user_id uuid NOT NULL,
          action text NOT NULL CHECK (action IN (
            'report_archived','report_unarchived','generate','redispatch',
            'submit','return','approve','publish','withdraw','health_snapshot_created'
          )),
          from_status text NOT NULL,
          to_status text NOT NULL,
          reason text,
          captured_xid bigint NOT NULL DEFAULT pg_current_xact_id()::text::bigint,
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          UNIQUE (enterprise_id,id),
          FOREIGN KEY (enterprise_id,report_id)
            REFERENCES f1.analysis_report(enterprise_id,id),
          FOREIGN KEY (enterprise_id,report_id,version_id)
            REFERENCES f1.analysis_report_version(enterprise_id,report_id,id),
          FOREIGN KEY (enterprise_id,actor_user_id)
            REFERENCES f1.enterprise_user(enterprise_id,user_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX analysis_report_transition_lookup_idx ON "
        "f1.analysis_report_transition(enterprise_id,report_id,version_id,"
        "captured_xid,action,actor_user_id)"
    )
    op.execute("ALTER TABLE f1.analysis_report_transition ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE f1.analysis_report_transition FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON f1.analysis_report_transition FROM PUBLIC,f1_api,f1_worker")
    for table in _EVENTS:
        # NULL is reserved for pre-migration history and the existing narrow
        # actor revocation/rebind capabilities. New ordinary events are bound
        # by the insert trigger; the API cannot supply this new column.
        op.execute(f"ALTER TABLE f1.{table} ADD COLUMN transition_id uuid")
        op.execute(
            f"ALTER TABLE f1.{table} ADD CONSTRAINT {table}_transition_fk "
            "FOREIGN KEY (enterprise_id,transition_id) "
            "REFERENCES f1.analysis_report_transition(enterprise_id,id)"
        )
        op.execute(
            f"CREATE UNIQUE INDEX {table}_transition_uq "
            f"ON f1.{table}(transition_id) WHERE transition_id IS NOT NULL"
        )


def _version_archive_boundary() -> None:
    # Runtime services already hold report -> version locks. Direct SQL must
    # also recheck the report under a lock: an unlocked read could race with
    # archiving. A raw UPDATE may have locked its version tuple first, so a
    # competing raw statement can be aborted by PostgreSQL's deadlock detector;
    # it may never commit a transition through an archived parent.
    # SECURITY INVOKER uses the API's existing report lock privilege without
    # granting business-table UPDATE to the private journal definer.
    op.execute(
        """
        CREATE FUNCTION f1.guard_analysis_report_version_archive()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path=pg_catalog AS $$
        DECLARE v_archived timestamptz;
        BEGIN
          IF TG_OP='INSERT' THEN
            IF NEW.status<>'queued' THEN RETURN NEW; END IF;
          ELSIF NEW.status IS NOT DISTINCT FROM OLD.status
             OR NEW.status NOT IN (
               'queued','review_pending','changes_requested','approved','published'
             ) THEN
            -- Withdrawal keeps its existing independent state checks and
            -- is not subject to this generation/review fence. Worker
            -- fail/finalize paths keep their existing lease/state checks.
            RETURN NEW;
          END IF;
          SELECT report.archived_at INTO v_archived
          FROM f1.analysis_report AS report
          WHERE report.enterprise_id=NEW.enterprise_id AND report.id=NEW.report_id
          FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'ANALYSIS_REPORT_VERSION_REPORT_INVALID';
          END IF;
          IF v_archived IS NOT NULL THEN
            RAISE EXCEPTION 'ANALYSIS_REPORT_ARCHIVED_VERSION_INVALID';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER analysis_report_version_00_archive_guard BEFORE INSERT OR UPDATE "
        "ON f1.analysis_report_version FOR EACH ROW "
        "EXECUTE FUNCTION f1.guard_analysis_report_version_archive()"
    )


def _capture() -> None:
    op.execute(
        """
        CREATE FUNCTION f1.capture_analysis_report_transition()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog AS $$
        DECLARE
          v_actor uuid; v_report uuid; v_version uuid;
          v_action text; v_from text; v_to text; v_reason text;
        BEGIN
          IF TG_TABLE_NAME='analysis_report' THEN
            IF OLD.archived_at IS NULL AND NEW.archived_at IS NOT NULL THEN
              v_action:='report_archived'; v_from:='active'; v_to:='archived';
              v_reason:=NEW.archived_reason;
            ELSIF OLD.archived_at IS NOT NULL AND NEW.archived_at IS NULL THEN
              v_action:='report_unarchived'; v_from:='archived'; v_to:='active';
            ELSE RETURN NEW; END IF;
            v_report:=NEW.id;
          ELSIF TG_TABLE_NAME='analysis_report_version' THEN
            v_report:=NEW.report_id; v_version:=NEW.id;
            IF TG_OP='INSERT' AND NEW.status='queued' THEN
              v_action:='generate'; v_to:='queued';
              v_from:=CASE WHEN NEW.version_number=1 THEN 'empty' ELSE 'prior' END;
            ELSIF TG_OP='UPDATE' THEN
              v_from:=OLD.status; v_to:=NEW.status;
              v_action:=CASE
                WHEN (v_from,v_to)=('failed','queued') THEN 'redispatch'
                WHEN (v_from,v_to)=('draft','review_pending') THEN 'submit'
                WHEN (v_from,v_to)=('review_pending','changes_requested') THEN 'return'
                WHEN (v_from,v_to)=('review_pending','approved') THEN 'approve'
                WHEN (v_from,v_to)=('approved','published') THEN 'publish'
                WHEN (v_from,v_to)=('published','withdrawn') THEN 'withdraw'
                ELSE NULL END;
            END IF;
            -- Claim/finalize/lease recovery and the existing definer's
            -- revoked-actor transition keep their separate job contracts.
            IF v_action IS NULL THEN RETURN NEW; END IF;
          ELSIF TG_TABLE_NAME='analysis_report_health_snapshot' THEN
            v_report:=NEW.report_id; v_version:=NEW.version_id;
            v_action:='health_snapshot_created'; v_from:='approved'; v_to:='published';
          ELSE RAISE EXCEPTION 'REPORT_TRANSITION_SOURCE_INVALID'; END IF;

          SELECT member.user_id INTO v_actor
          FROM f1.enterprise_user AS member
          JOIN f1.user_profile AS profile ON profile.id=member.user_id
          WHERE member.enterprise_id=NEW.enterprise_id
            AND profile.keycloak_sub=f1.current_sub()
            AND member.role IN ('super_admin','enterprise_admin');
          IF v_actor IS NULL OR NEW.enterprise_id IS DISTINCT FROM f1.current_enterprise_id()
             OR NOT f1.session_authorized(NEW.enterprise_id) THEN
            RAISE EXCEPTION 'REPORT_TRANSITION_ACTOR_INVALID';
          END IF;
          INSERT INTO f1.analysis_report_transition (
            enterprise_id,report_id,version_id,actor_user_id,action,from_status,to_status,reason
          ) VALUES (
            NEW.enterprise_id,v_report,v_version,v_actor,v_action,v_from,v_to,v_reason
          );
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER analysis_report_transition_capture AFTER UPDATE "
        "ON f1.analysis_report FOR EACH ROW "
        "EXECUTE FUNCTION f1.capture_analysis_report_transition()"
    )
    op.execute(
        "CREATE TRIGGER analysis_report_version_transition_capture AFTER INSERT OR UPDATE "
        "ON f1.analysis_report_version FOR EACH ROW "
        "EXECUTE FUNCTION f1.capture_analysis_report_transition()"
    )
    op.execute(
        "CREATE TRIGGER analysis_report_health_transition_capture AFTER INSERT "
        "ON f1.analysis_report_health_snapshot FOR EACH ROW "
        "EXECUTE FUNCTION f1.capture_analysis_report_transition()"
    )
    # A report's management tuple is a state, not editable historical proof.
    # Updated-at-only writes create no transition and cannot mint an event.
    op.execute(
        """
        CREATE FUNCTION f1.guard_analysis_report_management_tuple()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path=pg_catalog AS $$
        BEGIN
          IF NEW.archived_at IS NULL AND
             (NEW.archived_by_user_id IS NOT NULL OR NEW.archived_reason IS NOT NULL) THEN
            RAISE EXCEPTION 'ANALYSIS_REPORT_ARCHIVE_SHAPE_INVALID';
          END IF;
          IF NEW.archived_at IS NOT NULL AND NEW.archived_by_user_id IS NULL THEN
            RAISE EXCEPTION 'ANALYSIS_REPORT_ARCHIVE_ACTOR_INVALID';
          END IF;
          IF TG_OP='INSERT' AND NEW.archived_at IS NOT NULL THEN
            RAISE EXCEPTION 'ANALYSIS_REPORT_INSERT_INVALID';
          END IF;
          IF TG_OP='UPDATE' AND OLD.archived_at IS NOT NULL AND NEW.archived_at IS NOT NULL
             AND (NEW.archived_at IS DISTINCT FROM OLD.archived_at
                  OR NEW.archived_by_user_id IS DISTINCT FROM OLD.archived_by_user_id
                  OR NEW.archived_reason IS DISTINCT FROM OLD.archived_reason) THEN
            RAISE EXCEPTION 'ANALYSIS_REPORT_ARCHIVE_METADATA_IMMUTABLE';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER analysis_report_management_tuple_guard BEFORE INSERT OR UPDATE "
        "ON f1.analysis_report FOR EACH ROW "
        "EXECUTE FUNCTION f1.guard_analysis_report_management_tuple()"
    )


def _bind_events() -> None:
    op.execute(
        """
        CREATE FUNCTION f1.bind_analysis_report_transition_event()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog AS $$
        DECLARE
          v_actor uuid; v_transition uuid; v_action text; v_version uuid;
          v_from text; v_to text; v_reason text;
        BEGIN
          IF NEW.transition_id IS NOT NULL THEN
            RAISE EXCEPTION 'REPORT_TRANSITION_ID_SERVER_OWNED';
          END IF;
          IF TG_TABLE_NAME='analysis_report_audit_event' THEN
            IF NEW.action IN ('actor_revoked','actor_rebound') THEN
              -- The original SECURITY INVOKER audit guard still checks the
              -- f1_analysis_report_definer caller and its exact capability.
              -- f1_api cannot use these actions to bypass ordinary auditing.
              RETURN NEW;
            END IF;
          END IF;
          SELECT member.user_id INTO v_actor
          FROM f1.enterprise_user AS member
          JOIN f1.user_profile AS profile ON profile.id=member.user_id
          WHERE member.enterprise_id=NEW.enterprise_id
            AND profile.keycloak_sub=f1.current_sub()
            AND member.role IN ('super_admin','enterprise_admin');
          IF v_actor IS NULL OR NEW.enterprise_id IS DISTINCT FROM f1.current_enterprise_id()
             OR NOT f1.session_authorized(NEW.enterprise_id) THEN
            RAISE EXCEPTION 'REPORT_TRANSITION_ACTOR_INVALID';
          END IF;
          IF TG_TABLE_NAME='analysis_report_health_snapshot' THEN
            v_action:='publish'; v_version:=NEW.version_id;
            v_from:='approved'; v_to:='published';
          ELSE
            IF NEW.actor_user_id IS DISTINCT FROM v_actor THEN
              RAISE EXCEPTION 'REPORT_TRANSITION_ACTOR_INVALID';
            END IF;
            v_action:=NEW.action;
            IF TG_TABLE_NAME='analysis_report_management_event' THEN
              v_from:=CASE v_action WHEN 'report_archived' THEN 'active' ELSE 'archived' END;
              v_to:=CASE v_action WHEN 'report_archived' THEN 'archived' ELSE 'active' END;
              v_reason:=NEW.reason;
            ELSE
              v_version:=NEW.version_id;
              IF TG_TABLE_NAME='analysis_report_audit_event' THEN
                v_from:=NEW.from_status; v_to:=NEW.to_status;
              ELSE
                v_from:=CASE v_action WHEN 'submit' THEN 'draft' ELSE 'review_pending' END;
                v_to:=CASE v_action WHEN 'submit' THEN 'review_pending'
                  WHEN 'return' THEN 'changes_requested' WHEN 'approve' THEN 'approved' END;
              END IF;
            END IF;
          END IF;
          SELECT change.id INTO v_transition
          FROM f1.analysis_report_transition AS change
          WHERE change.enterprise_id=NEW.enterprise_id AND change.report_id=NEW.report_id
            AND change.version_id IS NOT DISTINCT FROM v_version
            AND change.actor_user_id=v_actor AND change.action=v_action
            AND change.from_status=v_from AND change.to_status=v_to
            AND change.reason IS NOT DISTINCT FROM v_reason
            AND change.captured_xid=pg_current_xact_id()::text::bigint
          ORDER BY change.created_at DESC,change.id DESC LIMIT 1;
          IF v_transition IS NULL THEN
            RAISE EXCEPTION 'REPORT_TRANSITION_EVIDENCE_REQUIRED';
          END IF;
          NEW.transition_id:=v_transition;
          -- Each event table has a unique transition_id. The same real
          -- transition supplies one audit and one review, never two of either.
          RETURN NEW;
        END $$
        """
    )
    # Replace the management xmin guard: it cannot distinguish a transition
    # from a no-op write. Keep its function for historical downgrade tooling.
    op.execute(
        "DROP TRIGGER analysis_report_management_insert_guard "
        "ON f1.analysis_report_management_event"
    )
    for table in _EVENTS:
        op.execute(
            f"CREATE TRIGGER {table}_00_transition_bind BEFORE INSERT "
            f"ON f1.{table} FOR EACH ROW "
            "EXECUTE FUNCTION f1.bind_analysis_report_transition_event()"
        )


def _require_events() -> None:
    op.execute(
        """
        CREATE FUNCTION f1.require_analysis_report_transition_event()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog AS $$
        BEGIN
          IF NEW.action IN ('report_archived','report_unarchived') THEN
            IF NOT EXISTS (
              SELECT 1 FROM f1.analysis_report_management_event AS event
              WHERE event.enterprise_id=NEW.enterprise_id AND event.transition_id=NEW.id
            ) THEN RAISE EXCEPTION 'REPORT_TRANSITION_EVENT_REQUIRED'; END IF;
          ELSE
            IF NOT EXISTS (
              SELECT 1 FROM f1.analysis_report_audit_event AS event
              WHERE event.enterprise_id=NEW.enterprise_id AND event.transition_id=NEW.id
            ) THEN RAISE EXCEPTION 'REPORT_TRANSITION_EVENT_REQUIRED'; END IF;
            IF NEW.action IN ('submit','return','approve') AND NOT EXISTS (
              SELECT 1 FROM f1.analysis_report_review_event AS event
              WHERE event.enterprise_id=NEW.enterprise_id AND event.transition_id=NEW.id
            ) THEN RAISE EXCEPTION 'REPORT_TRANSITION_REVIEW_REQUIRED'; END IF;
          END IF;
          RETURN NULL;
        END $$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER analysis_report_transition_requires_event "
        "AFTER INSERT ON f1.analysis_report_transition DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION f1.require_analysis_report_transition_event()"
    )


def _grants() -> None:
    scoped = (
        "enterprise_id=f1.current_enterprise_id() "
        "AND f1.session_authorized(enterprise_id)"
    )
    op.execute(f"GRANT USAGE ON SCHEMA f1 TO {_ROLE}")
    op.execute(
        f"GRANT SELECT,INSERT ON f1.analysis_report_transition TO {_ROLE}"
    )
    op.execute(
        f"CREATE POLICY analysis_report_transition_definer_select ON f1.{_TABLE} "
        f"FOR SELECT TO {_ROLE} USING ({scoped})"
    )
    op.execute(
        f"CREATE POLICY analysis_report_transition_definer_insert ON f1.{_TABLE} "
        f"FOR INSERT TO {_ROLE} WITH CHECK ({scoped})"
    )
    for table in _EVENTS:
        op.execute(f"GRANT SELECT (enterprise_id,transition_id) ON f1.{table} TO {_ROLE}")
        op.execute(
            f"CREATE POLICY {table}_transition_read ON f1.{table} "
            f"FOR SELECT TO {_ROLE} USING ({scoped})"
        )
    op.execute(f"GRANT SELECT (id,keycloak_sub) ON f1.user_profile TO {_ROLE}")
    op.execute(f"GRANT SELECT (enterprise_id,user_id,role) ON f1.enterprise_user TO {_ROLE}")
    op.execute(
        "CREATE POLICY analysis_report_transition_actor_profile ON f1.user_profile "
        f"FOR SELECT TO {_ROLE} USING (keycloak_sub=f1.current_sub())"
    )
    op.execute(
        "CREATE POLICY analysis_report_transition_actor_membership ON f1.enterprise_user "
        f"FOR SELECT TO {_ROLE} USING (enterprise_id=f1.current_enterprise_id())"
    )
    for name in (
        "capture_analysis_report_transition",
        "bind_analysis_report_transition_event",
        "require_analysis_report_transition_event",
        "guard_analysis_report_management_tuple",
        "guard_analysis_report_version_archive",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION f1.{name}() FROM PUBLIC")


def downgrade() -> None:
    # Production rollback restores a verified backup to a new database. Do
    # not erase newly recorded evidence or reopen the old forgery boundary.
    raise RuntimeError("REPORT_TRANSITION_AUDIT_RESTORE_REQUIRED")
