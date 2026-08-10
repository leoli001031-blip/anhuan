from __future__ import annotations

import json
from pathlib import Path
import unittest

from infra.f1 import local_log_verify as logs


ROOT = Path(__file__).resolve().parents[1]


def clean_logs() -> dict[str, bytes]:
    return {name: b"service ready\n" for name in logs.CORE_SERVICES}


def verify_logs(
    payload: dict[str, bytes], *, secret_values: tuple[bytes, ...]
) -> logs.LogVerificationCounts:
    return logs.verify_runtime_logs(
        payload,
        secret_values=secret_values,
        repository_scanned_file_count=3,
        repository_secret_leak_count=0,
        backup_scanned_file_count=2,
        backup_secret_leak_count=0,
    )


class EngineeringCloseoutLogVerificationTests(unittest.TestCase):
    def test_clean_exact_service_set_renders_aggregate_only_success(self) -> None:
        counts = verify_logs(
            clean_logs(), secret_values=(b"synthetic-secret-value",)
        )
        payload, tag = logs.render_success(counts)
        decoded = json.loads(payload)
        self.assertEqual(tag, "LOCAL_LOG_VERIFY_OK")
        self.assertEqual(decoded["core_service_log_count"], 9)
        self.assertEqual(decoded["repository_scanned_file_count"], 3)
        self.assertEqual(decoded["backup_scanned_file_count"], 2)
        self.assertTrue(
            all(
                value == 0
                for key, value in decoded.items()
                if key
                not in {
                    "core_service_log_count",
                    "repository_scanned_file_count",
                    "backup_scanned_file_count",
                }
            )
        )

    def test_missing_or_extra_service_is_rejected(self) -> None:
        payload = clean_logs()
        payload.pop("web")
        with self.assertRaisesRegex(
            logs.LogVerificationError, "LOCAL_LOG_SERVICE_SET_INVALID"
        ):
            verify_logs(payload, secret_values=())
        payload = clean_logs()
        payload["unknown"] = b"ready"
        with self.assertRaisesRegex(
            logs.LogVerificationError, "LOCAL_LOG_SERVICE_SET_INVALID"
        ):
            verify_logs(payload, secret_values=())

    def test_secret_dsn_token_and_pii_are_counted_without_echo(self) -> None:
        payload = clean_logs()
        payload["api"] = (
            b"postgresql://role:redacted@db/app "
            b"Authorization: Bearer hidden "
            b"eyJabcdefgh.abcdefgh.abcdefgh "
            b"GET /callback?code=hidden&state=opaque&session_state=secret "
            b"person@example.invalid 13800138000 local-ingestion.pdf "
            b"LOCAL_INGESTION_SYNTHETIC secret-value-123"
        )
        counts = verify_logs(
            payload, secret_values=(b"secret-value-123",)
        )
        with self.assertRaisesRegex(
            logs.LogVerificationError, "LOCAL_LOG_RUNTIME_SECRET_LEAK"
        ):
            logs.render_success(counts)
        rendered = repr(counts)
        self.assertNotIn("person@example.invalid", rendered)
        self.assertNotIn("secret-value-123", rendered)

    def test_nginx_access_log_never_records_query_strings(self) -> None:
        nginx = (ROOT / "infra/f1/nginx/default.conf").read_text(
            encoding="utf-8"
        )
        self.assertIn("log_format anhuan_body_free", nginx)
        self.assertIn(
            "server {\n    access_log /var/log/nginx/access.log anhuan_body_free;",
            nginx,
        )
        self.assertIn("error_log /var/log/nginx/error.log crit;", nginx)
        format_line = next(
            line for line in nginx.splitlines() if line.startswith("log_format ")
        )
        sanitized_format = format_line.replace("$request_method", "")
        for forbidden in (
            "$args",
            "$request",
            "$request_uri",
            "$http_referer",
        ):
            self.assertNotIn(forbidden, sanitized_format)

    def test_api_access_and_database_error_logs_hide_business_values(self) -> None:
        compose = (ROOT / "infra/f1/docker-compose.local.yml").read_text(
            encoding="utf-8"
        )
        database = (
            ROOT / "src/platform_foundation/f1/database.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"--no-access-log"', compose)
        self.assertIn("hide_parameters=True", database)

    def test_oversize_log_is_fail_closed(self) -> None:
        payload = clean_logs()
        payload["clamd"] = b"x" * (logs.MAX_SERVICE_LOG_BYTES + 1)
        counts = verify_logs(payload, secret_values=())
        self.assertEqual(counts.log_byte_limit_exceeded_count, 1)
        with self.assertRaisesRegex(
            logs.LogVerificationError, "LOCAL_LOG_SIZE_LIMIT"
        ):
            logs.render_success(counts)

    def test_negative_or_non_integer_evidence_count_is_rejected(self) -> None:
        for value in (-1, True, "1"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    logs.LogVerificationError,
                    "LOCAL_LOG_EVIDENCE_COUNT_INVALID",
                ):
                    logs.verify_runtime_logs(
                        clean_logs(),
                        secret_values=(),
                        repository_scanned_file_count=value,  # type: ignore[arg-type]
                        repository_secret_leak_count=0,
                        backup_scanned_file_count=0,
                        backup_secret_leak_count=0,
                    )

    def test_localctl_scans_exact_labelled_core_containers_and_all_secrets(self) -> None:
        source = (ROOT / "scripts/localctl").read_text(encoding="utf-8")
        self.assertIn("def _runtime_log_metrics", source)
        self.assertIn("local_log_verify.CORE_SERVICES", source)
        self.assertIn('labels.get("io.anhuan.scope") != SCOPE', source)
        self.assertIn('labels.get("io.anhuan.project-id")', source)
        self.assertIn("def _docker_logs_bounded", source)
        self.assertIn('"ps", "-a", "-q", "--no-trunc"', source)
        self.assertIn('[_docker(), "logs", container_id]', source)
        self.assertIn("selectors.DefaultSelector()", source)
        self.assertNotIn('"logs", "--tail"', source)
        self.assertIn("for name in ALL_SECRET_NAMES", source)
        self.assertIn('if name != "f0i_key"', source)
        self.assertIn("body != body.strip()", source)
        self.assertIn("_repository_files_for_log_check()", source)
        self.assertIn("_backup_files_for_log_check(state)", source)
        self.assertIn("_runtime_log_metrics(state)", source)
        self.assertIn('print(log_tag)', source)
        start_source = source.split("def _start(", 1)[1].split(
            "\ndef _resource_ids", 1
        )[0]
        self.assertGreaterEqual(start_source.count('"--force-recreate"'), 2)


if __name__ == "__main__":
    unittest.main()
