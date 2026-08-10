"""Tenant-scoped blind annotation service over F0-G SECURITY DEFINER gates."""

from __future__ import annotations

import uuid

from ..auth import SessionContext
from ..database import DatabaseConfig, tenant_transaction
from ..f0e.hashing import stable_uuid4
from ..f0f.keyfile import load_keyfile
from .config import validate_local_database_config
from .contracts import (
    AssignmentMetadata,
    CanonicalLabel,
    F0GError,
    LabelMetadata,
    SensitiveBytes,
)


_BODY_LIMIT = 4 * 1024 * 1024


class AnnotationService:
    """The only Python boundary allowed to move annotation plaintext."""

    def __init__(self, config: DatabaseConfig, key_path: str) -> None:
        if not isinstance(config, DatabaseConfig) or not isinstance(key_path, str):
            raise F0GError("ANNOTATION_CONTRACT_INVALID")
        try:
            self.config = validate_local_database_config(config)
        except Exception:
            raise F0GError("ANNOTATION_CONTRACT_INVALID") from None
        verify_function_catalog(self.config)
        self._key_path = key_path

    def list_assignments(
        self, context: SessionContext
    ) -> tuple[AssignmentMetadata, ...]:
        try:
            with tenant_transaction(self.config, "f0d_runtime", context) as connection:
                rows = connection.execute(
                    "SELECT * FROM f0g.list_assigned_work()"
                ).fetchall()
            return tuple(
                sorted(
                    (_assignment(row) for row in rows),
                    key=lambda item: item.selection_ordinal,
                )
            )
        except F0GError:
            raise
        except Exception:
            raise F0GError("ANNOTATION_DATABASE_FAILED") from None

    def read_assigned_body(
        self, context: SessionContext, assignment_id: uuid.UUID
    ) -> SensitiveBytes:
        _require_uuid(assignment_id)
        key_material = bytearray()
        try:
            with load_keyfile(self._key_path) as key:
                key_material.extend(key.view())
                with tenant_transaction(
                    self.config, "f0d_runtime", context
                ) as connection:
                    row = connection.execute(
                        "SELECT * FROM f0g.read_assigned_body(%s,%s,%s)",
                        (assignment_id, key_material, uuid.uuid4()),
                    ).fetchone()
            if row is None or not isinstance(row.get("body"), (bytes, bytearray, memoryview)):
                raise F0GError("ANNOTATION_ASSIGNMENT_DENIED")
            body = SensitiveBytes(row["body"], maximum=_BODY_LIMIT)
            if body.sha256 != str(row.get("plaintext_sha256")) or body.byte_count != int(
                row.get("plaintext_size_bytes", -1)
            ):
                body.wipe()
                raise F0GError("ANNOTATION_BODY_INVALID")
            return body
        except F0GError:
            raise
        except Exception:
            raise F0GError("ANNOTATION_ASSIGNMENT_DENIED") from None
        finally:
            _wipe(key_material)

    def submit_label(
        self,
        context: SessionContext,
        assignment_id: uuid.UUID,
        label: CanonicalLabel,
    ) -> uuid.UUID:
        _require_uuid(assignment_id)
        if not isinstance(label, CanonicalLabel):
            raise F0GError("ANNOTATION_LABEL_INVALID")
        assignments = {
            item.assignment_id: item for item in self.list_assignments(context)
        }
        assignment = assignments.get(assignment_id)
        if assignment is None or assignment.label_slot not in {1, 2}:
            raise F0GError("ANNOTATION_ASSIGNMENT_DENIED")
        label_id = stable_uuid4(
            "f0g-blind-label-v1",
            context.enterprise_id,
            assignment_id,
            context.actor_id,
            assignment.label_slot,
            label.sha256,
        )
        key_material = bytearray()
        try:
            with load_keyfile(self._key_path) as key:
                key_material.extend(key.view())
                with tenant_transaction(
                    self.config, "f0d_runtime", context
                ) as connection:
                    row = connection.execute(
                        "SELECT f0g.record_blind_label(%s,%s,%s,%s,%s,%s,%s) AS label_id",
                        (
                            label_id,
                            assignment_id,
                            key_material,
                            label.view(),
                            label.sha256,
                            label.byte_count,
                            uuid.uuid4(),
                        ),
                    ).fetchone()
            if row is None or row.get("label_id") != label_id:
                raise F0GError("ANNOTATION_STATE_INVALID")
            return label_id
        except F0GError:
            raise
        except Exception:
            raise F0GError("ANNOTATION_STATE_INVALID") from None
        finally:
            _wipe(key_material)

    def read_adjudication_labels(
        self, context: SessionContext, assignment_id: uuid.UUID
    ) -> tuple[tuple[LabelMetadata, SensitiveBytes], tuple[LabelMetadata, SensitiveBytes]]:
        _require_uuid(assignment_id)
        key_material = bytearray()
        pairs: list[tuple[LabelMetadata, SensitiveBytes]] = []
        try:
            with load_keyfile(self._key_path) as key:
                key_material.extend(key.view())
                with tenant_transaction(
                    self.config, "f0d_runtime", context
                ) as connection:
                    rows = connection.execute(
                        "SELECT * FROM f0g.read_adjudication_labels(%s,%s,%s)",
                        (assignment_id, key_material, uuid.uuid4()),
                    ).fetchall()
            for row in rows:
                metadata = _label(row)
                body = SensitiveBytes(
                    row["label_body"], maximum=_BODY_LIMIT  # type: ignore[arg-type]
                )
                pairs.append((metadata, body))
            if len(pairs) != 2 or tuple(item[0].label_ordinal for item in pairs) != (1, 2):
                raise F0GError("ANNOTATION_ADJUDICATION_DENIED")
            for metadata, body in pairs:
                if metadata.label_sha256 != body.sha256 or metadata.label_size_bytes != body.byte_count:
                    raise F0GError("ANNOTATION_BODY_INVALID")
            return (pairs[0], pairs[1])
        except F0GError:
            _wipe_pairs(pairs)
            raise
        except Exception:
            _wipe_pairs(pairs)
            raise F0GError("ANNOTATION_ADJUDICATION_DENIED") from None
        finally:
            _wipe(key_material)

    def read_adjudication_label(
        self,
        context: SessionContext,
        assignment_id: uuid.UUID,
        label_ordinal: int,
    ) -> SensitiveBytes:
        _require_uuid(assignment_id)
        if label_ordinal not in {1, 2}:
            raise F0GError("ANNOTATION_CONTRACT_INVALID")
        pairs: tuple[tuple[LabelMetadata, SensitiveBytes], ...] = ()
        try:
            pairs = self.read_adjudication_labels(context, assignment_id)
            selected: SensitiveBytes | None = None
            for metadata, body in pairs:
                if metadata.label_ordinal == label_ordinal:
                    selected = body
                else:
                    body.wipe()
            if selected is None:
                raise F0GError("ANNOTATION_ADJUDICATION_DENIED")
            return selected
        except F0GError:
            raise
        except Exception:
            raise F0GError("ANNOTATION_ADJUDICATION_DENIED") from None

    def adjudicate(
        self,
        context: SessionContext,
        assignment_id: uuid.UUID,
        decision_code: str,
        selected_label_ordinal: int | None,
    ) -> uuid.UUID:
        _require_uuid(assignment_id)
        if decision_code not in {
            "ACCEPT_LABEL_ONE",
            "ACCEPT_LABEL_TWO",
            "NO_CONSENSUS",
        }:
            raise F0GError("ANNOTATION_CONTRACT_INVALID")
        if (
            (decision_code == "ACCEPT_LABEL_ONE" and selected_label_ordinal != 1)
            or (decision_code == "ACCEPT_LABEL_TWO" and selected_label_ordinal != 2)
            or (decision_code == "NO_CONSENSUS" and selected_label_ordinal is not None)
        ):
            raise F0GError("ANNOTATION_CONTRACT_INVALID")
        adjudication_id = stable_uuid4(
            "f0g-adjudication-v1", context.enterprise_id, assignment_id
        )
        selected_label_selector = (
            None
            if selected_label_ordinal is None
            else uuid.UUID(int=selected_label_ordinal)
        )
        key_material = bytearray()
        try:
            with load_keyfile(self._key_path) as key:
                key_material.extend(key.view())
                with tenant_transaction(
                    self.config, "f0d_runtime", context
                ) as connection:
                    row = connection.execute(
                        "SELECT f0g.adjudicate_assignment(%s,%s,%s,%s,%s,%s) "
                        "AS adjudication_id",
                        (
                            adjudication_id,
                            assignment_id,
                            key_material,
                            decision_code,
                            selected_label_selector,
                            uuid.uuid4(),
                        ),
                    ).fetchone()
            if row is None or row.get("adjudication_id") != adjudication_id:
                raise F0GError("ANNOTATION_STATE_INVALID")
            return adjudication_id
        except F0GError:
            raise
        except Exception:
            raise F0GError("ANNOTATION_ADJUDICATION_DENIED") from None
        finally:
            _wipe(key_material)


def _assignment(row: dict[str, object]) -> AssignmentMetadata:
    try:
        role = str(row["assignment_role"])
        return AssignmentMetadata(
            assignment_id=row["assignment_id"],  # type: ignore[arg-type]
            queue_id=row["annotation_queue_id"],  # type: ignore[arg-type]
            assignment_role=role,
            label_slot={"ANNOTATOR_ONE": 1, "ANNOTATOR_TWO": 2}.get(role),
            selection_ordinal=int(row["selection_ordinal"]),
            guideline_version=str(row["guideline_version"]),
            guideline_sha256=str(row["guideline_sha256"]),
            assignment_status=str(row["assignment_status"]),
            own_label_submitted=bool(row["own_label_submitted"]),
            labels_submitted=(
                None
                if row.get("labels_submitted") is None
                else int(row["labels_submitted"])
            ),
            adjudication_recorded=bool(row["adjudication_recorded"]),
        )
    except (KeyError, TypeError, ValueError):
        raise F0GError("ANNOTATION_DATABASE_FAILED") from None


def _label(row: dict[str, object]) -> LabelMetadata:
    try:
        return LabelMetadata(
            label_id=row["label_id"],  # type: ignore[arg-type]
            label_ordinal=int(row["label_ordinal"]),
            label_sha256=str(row["label_plaintext_sha256"]),
            label_size_bytes=int(row["label_plaintext_size_bytes"]),
        )
    except (KeyError, TypeError, ValueError):
        raise F0GError("ANNOTATION_DATABASE_FAILED") from None


def _require_uuid(value: object) -> uuid.UUID:
    if not isinstance(value, uuid.UUID):
        raise F0GError("ANNOTATION_CONTRACT_INVALID")
    return value


def _wipe(buffer: bytearray) -> None:
    buffer[:] = b"\0" * len(buffer)
    buffer.clear()


def _wipe_pairs(pairs: list[tuple[LabelMetadata, SensitiveBytes]]) -> None:
    for _, body in pairs:
        body.wipe()
    pairs.clear()


def verify_function_catalog(config: DatabaseConfig) -> None:
    """Fail closed if the complete F0-G database security catalog drifts."""

    signatures = {
        "prepare": "f0g.prepare_annotation_workflow(uuid,text,text,uuid[],uuid[],uuid,uuid,uuid,uuid)",
        "list": "f0g.list_assigned_work()",
        "body": "f0g.read_assigned_body(uuid,bytea,uuid)",
        "label": "f0g.record_blind_label(uuid,uuid,bytea,bytea,text,bigint,uuid)",
        "pair": "f0g.read_adjudication_labels(uuid,bytea,uuid)",
        "adjudicate": "f0g.adjudicate_assignment(uuid,uuid,bytea,text,uuid,uuid)",
        "old_decrypt": "f0f.decrypt_verified_body(uuid,bytea)",
        "old_label": "f0f.record_gold_label(uuid,uuid,bytea,bytea,text,bigint)",
        "old_adjudicate": "f0f.adjudicate_gold_labels(uuid,uuid,uuid,uuid,text,uuid)",
    }
    expected = {
        "f0d_runtime": {
            "prepare": False,
            "list": True,
            "body": True,
            "label": True,
            "pair": True,
            "adjudicate": True,
            "old_decrypt": False,
            "old_label": False,
            "old_adjudicate": False,
        },
        "f0d_worker": {
            "prepare": False,
            "list": False,
            "body": False,
            "label": False,
            "pair": False,
            "adjudicate": False,
            "old_decrypt": False,
            "old_label": False,
            "old_adjudicate": False,
        },
    }
    try:
        validate_local_database_config(config)
        for role, grants in expected.items():
            # Evaluate ACLs as the migration catalog owner.  A role with no
            # f0g schema USAGE cannot even resolve a regprocedure signature;
            # asking PostgreSQL about that role avoids granting visibility
            # merely so the verifier can inspect the intended denial.
            with _catalog_transaction(config, "f0d_migration") as connection:
                for name, signature in signatures.items():
                    row = connection.execute(
                        "SELECT to_regprocedure(%s) IS NOT NULL AS exists, "
                        "has_function_privilege(%s,%s,'EXECUTE') "
                        "AS allowed, "
                        "has_function_privilege(%s,%s,"
                        "'EXECUTE WITH GRANT OPTION') AS grantable",
                        (signature, role, signature, role, signature),
                    ).fetchone()
                    if (
                        row is None
                        or row.get("exists") is not True
                        or row.get("allowed") is not grants[name]
                        or row.get("grantable") is not False
                    ):
                        raise F0GError("ANNOTATION_DATABASE_FAILED")
                schema = connection.execute(
                    "SELECT has_schema_privilege(%s,'f0g','USAGE') "
                    "AS usage_allowed, "
                    "has_schema_privilege(%s,'f0g',"
                    "'USAGE WITH GRANT OPTION') AS usage_grantable, "
                    "has_schema_privilege(%s,'f0g','CREATE') "
                    "AS create_allowed",
                    (role, role, role),
                ).fetchone()
                expected_schema = (role == "f0d_runtime", False, False)
                if schema is None or (
                    schema.get("usage_allowed"),
                    schema.get("usage_grantable"),
                    schema.get("create_allowed"),
                ) != expected_schema:
                    raise F0GError("ANNOTATION_DATABASE_FAILED")
                for table in (
                    "f0g.annotation_guideline",
                    "f0g.blind_assignment",
                    "f0f.page_body_evidence",
                    "f0f.gold_annotation_queue",
                    "f0f.gold_label_evidence",
                    "f0f.gold_adjudication",
                ):
                    row = connection.execute(
                        "SELECT (SELECT bool_or(has_table_privilege("
                        "%s,%s,privilege)) FROM unnest(ARRAY["
                        "'SELECT','INSERT','UPDATE','DELETE','TRUNCATE',"
                        "'REFERENCES','TRIGGER']) AS privilege) AS table_allowed, "
                        "(SELECT bool_or(has_any_column_privilege("
                        "%s,%s,privilege)) FROM unnest(ARRAY["
                        "'SELECT','INSERT','UPDATE','REFERENCES']) AS privilege) "
                        "AS column_allowed",
                        (role, table, role, table),
                    ).fetchone()
                    if (
                        row is None
                        or row.get("table_allowed") is not False
                        or row.get("column_allowed") is not False
                    ):
                        raise F0GError("ANNOTATION_DATABASE_FAILED")

        with _catalog_transaction(config, "f0d_migration") as connection:
            configured = connection.execute(
                "SELECT set_config('search_path','pg_catalog',true) AS value"
            ).fetchone()
            if configured is None or configured.get("value") != "pg_catalog":
                raise F0GError("ANNOTATION_DATABASE_FAILED")
            function_count = connection.execute(
                "SELECT count(*) AS value FROM pg_proc AS proc "
                "JOIN pg_namespace AS namespace "
                "ON namespace.oid=proc.pronamespace "
                "WHERE namespace.nspname='f0g'"
            ).fetchone()
            if function_count is None or function_count.get("value") != 6:
                raise F0GError("ANNOTATION_DATABASE_FAILED")
            for name in (
                "prepare",
                "list",
                "body",
                "label",
                "pair",
                "adjudicate",
            ):
                signature = signatures[name]
                row = connection.execute(
                    "SELECT owner.rolname AS owner, proc.prosecdef, "
                    "proc.proconfig, "
                    "has_function_privilege('public',proc.oid,'EXECUTE') "
                    "AS public_execute FROM pg_proc AS proc "
                    "JOIN pg_roles AS owner ON owner.oid=proc.proowner "
                    "WHERE proc.oid=to_regprocedure(%s)",
                    (signature,),
                ).fetchone()
                if (
                    row is None
                    or row.get("owner") != "f0d_migration"
                    or row.get("prosecdef") is not True
                    or tuple(row.get("proconfig") or ())
                    != ("search_path=pg_catalog",)
                    or row.get("public_execute") is not False
                ):
                    raise F0GError("ANNOTATION_DATABASE_FAILED")

            schema = connection.execute(
                "SELECT owner.rolname AS owner, "
                "has_schema_privilege('public',namespace.oid,'USAGE') "
                "AS public_usage, "
                "has_schema_privilege('public',namespace.oid,'CREATE') "
                "AS public_create FROM pg_namespace AS namespace "
                "JOIN pg_roles AS owner ON owner.oid=namespace.nspowner "
                "WHERE namespace.nspname='f0g'"
            ).fetchone()
            if (
                schema is None
                or schema.get("owner") != "f0d_migration"
                or schema.get("public_usage") is not False
                or schema.get("public_create") is not False
            ):
                raise F0GError("ANNOTATION_DATABASE_FAILED")

            tables = connection.execute(
                "SELECT relation.relname, owner.rolname AS owner, "
                "relation.relrowsecurity, "
                "relation.relforcerowsecurity, "
                "(SELECT bool_or(has_table_privilege('public',relation.oid,"
                "privilege)) FROM unnest(ARRAY['SELECT','INSERT','UPDATE',"
                "'DELETE','TRUNCATE','REFERENCES','TRIGGER']) AS privilege) "
                "AS public_privilege, "
                "(SELECT bool_or(has_any_column_privilege('public',relation.oid,"
                "privilege)) FROM unnest(ARRAY['SELECT','INSERT','UPDATE',"
                "'REFERENCES']) AS privilege) AS public_column_privilege "
                "FROM pg_class AS relation "
                "JOIN pg_namespace AS namespace "
                "ON namespace.oid=relation.relnamespace "
                "JOIN pg_roles AS owner ON owner.oid=relation.relowner "
                "WHERE namespace.nspname='f0g' AND relation.relkind='r' "
                "ORDER BY relation.relname"
            ).fetchall()
            if tuple(
                (
                    row.get("relname"),
                    row.get("owner"),
                    row.get("relrowsecurity"),
                    row.get("relforcerowsecurity"),
                    row.get("public_privilege"),
                    row.get("public_column_privilege"),
                )
                for row in tables
            ) != (
                (
                    "annotation_guideline",
                    "f0d_migration",
                    True,
                    True,
                    False,
                    False,
                ),
                (
                    "blind_assignment",
                    "f0d_migration",
                    True,
                    True,
                    False,
                    False,
                ),
            ):
                raise F0GError("ANNOTATION_DATABASE_FAILED")

            policies = connection.execute(
                "SELECT relation.relname, policy.polname, policy.polcmd, "
                "policy.polpermissive, ARRAY(SELECT role.rolname "
                "FROM unnest(policy.polroles) AS item(role_oid) "
                "JOIN pg_roles AS role ON role.oid=item.role_oid "
                "ORDER BY role.rolname) AS roles, "
                "pg_get_expr(policy.polqual,policy.polrelid) AS qual, "
                "pg_get_expr(policy.polwithcheck,policy.polrelid) AS with_check "
                "FROM pg_policy AS policy JOIN pg_class AS relation "
                "ON relation.oid=policy.polrelid JOIN pg_namespace AS namespace "
                "ON namespace.oid=relation.relnamespace "
                "WHERE namespace.nspname='f0g' "
                "ORDER BY relation.relname,policy.polname"
            ).fetchall()
            expected_policies = {
                (table, policy): (command, roles, qualifier, check)
                for table in ("annotation_guideline", "blind_assignment")
                for policy, command, roles, qualifier, check in (
                    (
                        "migration_f0g_delete_probe",
                        "d",
                        ("f0d_migration",),
                        "tenant",
                        None,
                    ),
                    (
                        "migration_f0g_insert",
                        "a",
                        ("f0d_migration",),
                        None,
                        "tenant",
                    ),
                    (
                        "migration_f0g_read",
                        "r",
                        ("f0d_migration",),
                        "tenant",
                        None,
                    ),
                    (
                        "migration_f0g_update_probe",
                        "w",
                        ("f0d_migration",),
                        "tenant",
                        "false",
                    ),
                    (
                        "tenant_boundary",
                        "*",
                        ("f0d_runtime", "f0d_worker"),
                        "tenant",
                        "tenant",
                    ),
                )
            }
            actual_policies: dict[
                tuple[object, object], tuple[object, tuple[object, ...], object, object]
            ] = {}
            for row in policies:
                if row.get("polpermissive") is not True:
                    raise F0GError("ANNOTATION_DATABASE_FAILED")
                actual_policies[(row.get("relname"), row.get("polname"))] = (
                    row.get("polcmd"),
                    tuple(row.get("roles") or ()),
                    _policy_expression(row.get("qual")),
                    _policy_expression(row.get("with_check")),
                )
            if actual_policies != expected_policies:
                raise F0GError("ANNOTATION_DATABASE_FAILED")

            triggers = connection.execute(
                "SELECT relation.relname, trigger.tgname, trigger.tgenabled, "
                "trigger.tgtype::integer AS tgtype, function_namespace.nspname "
                "AS function_schema, function.proname AS function_name "
                "FROM pg_trigger AS trigger JOIN pg_class AS relation "
                "ON relation.oid=trigger.tgrelid JOIN pg_namespace AS namespace "
                "ON namespace.oid=relation.relnamespace JOIN pg_proc AS function "
                "ON function.oid=trigger.tgfoid "
                "JOIN pg_namespace AS function_namespace "
                "ON function_namespace.oid=function.pronamespace "
                "WHERE namespace.nspname='f0g' AND NOT trigger.tgisinternal "
                "ORDER BY relation.relname,trigger.tgname"
            ).fetchall()
            expected_triggers = tuple(
                sorted(
                    (
                        table,
                        name,
                        "O",
                        trigger_type,
                        "f0d",
                        "reject_immutable_mutation",
                    )
                    for table in ("annotation_guideline", "blind_assignment")
                    for name, trigger_type in (
                        ("reject_immutable_row_mutation", 27),
                        ("reject_immutable_truncate", 34),
                    )
                )
            )
            actual_triggers = tuple(
                (
                    row.get("relname"),
                    row.get("tgname"),
                    row.get("tgenabled"),
                    row.get("tgtype"),
                    row.get("function_schema"),
                    row.get("function_name"),
                )
                for row in triggers
            )
            if actual_triggers != expected_triggers:
                raise F0GError("ANNOTATION_DATABASE_FAILED")
    except F0GError:
        raise
    except Exception:
        raise F0GError("ANNOTATION_DATABASE_FAILED") from None


def _policy_expression(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise F0GError("ANNOTATION_DATABASE_FAILED")
    compact = "".join(value.split())
    while compact.startswith("(") and compact.endswith(")"):
        depth = 0
        encloses_all = True
        for index, character in enumerate(compact):
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0 and index != len(compact) - 1:
                    encloses_all = False
                    break
        if not encloses_all or depth != 0:
            break
        compact = compact[1:-1]
    tenant = (
        "(enterprise_id=f0d.current_enterprise_id())"
        "ANDf0d.context_session_authorized(enterprise_id)"
    )
    if compact == tenant:
        return "tenant"
    if compact == "false":
        return "false"
    return "invalid"


def _catalog_transaction(config: DatabaseConfig, role: str):
    from ..database import role_transaction

    return role_transaction(config, role)  # type: ignore[arg-type]


__all__ = ("AnnotationService", "verify_function_catalog")
