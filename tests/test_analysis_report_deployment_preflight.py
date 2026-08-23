"""Offline contract locks for analysis-report Netlify deployment preflight.

The renderer/preflight under test is imported from disk. Tests must not mock it.
"""
from __future__ import annotations

import importlib.util
import os
import re
import stat
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT_PATH = ROOT / "deploy" / "analysis-report" / "preflight.py"
TEMPLATE_PATH = ROOT / "deploy" / "analysis-report" / "netlify.toml.template"
ENV_EXAMPLE_PATH = ROOT / "deploy" / "analysis-report" / "env.example"
DEPLOYMENT_PATH = ROOT / "deploy" / "analysis-report" / "DEPLOYMENT.md"
ROLLBACK_PATH = ROOT / "deploy" / "analysis-report" / "ROLLBACK.md"
REMOTE_SMOKE_PATH = ROOT / "deploy" / "analysis-report" / "REMOTE_SMOKE.md"
HANDOFF_PATH = ROOT / "TEST_HANDOFF.md"

VALID_NETLIFY = "https://reports.example.net"
VALID_EDGE = "https://edge.example.net"

SECRET_NEEDLES = (
    "Bearer",
    "eyJ",
    "password",
    "api_key",
    "api-key",
    "ARK_API",
    "sk-",
)


def load_preflight() -> Any:
    spec = importlib.util.spec_from_file_location(
        "analysis_report_deploy_preflight", PREFLIGHT_PATH
    )
    if spec is None or spec.loader is None:
        raise AssertionError("preflight loader unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def redirect_from_order(text: str) -> list[str]:
    return re.findall(r'(?m)^\s*from\s*=\s*"([^"]+)"\s*$', text)


class AnalysisReportDeploymentPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not PREFLIGHT_PATH.is_file():
            raise AssertionError(f"missing preflight: {PREFLIGHT_PATH}")
        cls.preflight = load_preflight()

    def _render(self, config: dict[str, str], **kwargs: Any) -> str:
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        output = tmp / "netlify.toml"
        return self.preflight.render_netlify_toml(config, output, **kwargs)

    def _valid_config(self) -> dict[str, str]:
        return {
            "NETLIFY_ORIGIN": VALID_NETLIFY,
            "EDGE_ORIGIN": VALID_EDGE,
        }

    def test_valid_https_origins_render_exact_rewrite_order(self) -> None:
        text = self._render(self._valid_config())
        self.assertEqual(
            redirect_from_order(text),
            ["/api/*", "/realms/*", "/resources/*", "/*"],
        )
        self.assertIn(f'to = "{VALID_EDGE}/api/:splat"', text)
        self.assertIn(f'to = "{VALID_EDGE}/realms/:splat"', text)
        self.assertIn(f'to = "{VALID_EDGE}/resources/:splat"', text)
        self.assertIn('to = "/index.html"', text)
        self.assertNotIn("__EDGE_ORIGIN__", text)
        self.assertIn("X-Robots-Tag", text)
        self.assertIn("noindex, nofollow", text)
        self.assertIn("X-Content-Type-Options", text)
        self.assertIn("nosniff", text)
        self.assertIn("Referrer-Policy", text)
        self.assertIn('base = "src/web"', text)
        self.assertIn('command = "npm run build"', text)
        self.assertIn('publish = "dist"', text)
        self.assertNotIn("VITE_MATERIAL_RAG_REPORT_MOCK", text)

    def test_http_origin_rejected(self) -> None:
        cfg = self._valid_config()
        cfg["NETLIFY_ORIGIN"] = "http://reports.example.net"
        with self.assertRaisesRegex(
            self.preflight.PreflightError, "LOCAL_DEPLOY_ORIGIN_NOT_HTTPS"
        ):
            self._render(cfg)

    def test_loopback_origin_rejected(self) -> None:
        cfg = self._valid_config()
        cfg["EDGE_ORIGIN"] = "https://127.0.0.1"
        with self.assertRaises(self.preflight.PreflightError):
            self._render(cfg)
        cfg = self._valid_config()
        cfg["NETLIFY_ORIGIN"] = "https://localhost"
        with self.assertRaises(self.preflight.PreflightError):
            self._render(cfg)

    def test_bare_ip_origin_rejected(self) -> None:
        cfg = self._valid_config()
        cfg["EDGE_ORIGIN"] = "https://203.0.113.10"
        with self.assertRaises(self.preflight.PreflightError):
            self._render(cfg)

    def test_origin_with_path_query_or_fragment_rejected(self) -> None:
        cfg = self._valid_config()
        cfg["NETLIFY_ORIGIN"] = "https://reports.example.net/app"
        with self.assertRaises(self.preflight.PreflightError):
            self._render(cfg)

    def test_single_label_and_malformed_port_rejected(self) -> None:
        cfg = self._valid_config()
        cfg["EDGE_ORIGIN"] = "https://edge"
        with self.assertRaisesRegex(
            self.preflight.PreflightError, "LOCAL_DEPLOY_ORIGIN_INVALID"
        ):
            self._render(cfg)
        cfg = self._valid_config()
        cfg["EDGE_ORIGIN"] = "https://edge.example.net:not-a-port"
        with self.assertRaisesRegex(
            self.preflight.PreflightError, "LOCAL_DEPLOY_ORIGIN_INVALID"
        ):
            self._render(cfg)
        cfg = self._valid_config()
        cfg["EDGE_ORIGIN"] = "https://edge.example.net?x=1"
        with self.assertRaises(self.preflight.PreflightError):
            self._render(cfg)
        cfg = self._valid_config()
        cfg["NETLIFY_ORIGIN"] = "https://reports.example.net#frag"
        with self.assertRaises(self.preflight.PreflightError):
            self._render(cfg)

    def test_same_origin_loop_rejected(self) -> None:
        cfg = {
            "NETLIFY_ORIGIN": VALID_EDGE,
            "EDGE_ORIGIN": VALID_EDGE,
        }
        with self.assertRaises(self.preflight.PreflightError):
            self._render(cfg)

    def test_missing_required_key_rejected(self) -> None:
        with self.assertRaises(self.preflight.PreflightError):
            self._render({"NETLIFY_ORIGIN": VALID_NETLIFY})

    def test_extra_key_rejected(self) -> None:
        cfg = self._valid_config()
        cfg["UNEXPECTED_KEY"] = "1"
        with self.assertRaises(self.preflight.PreflightError):
            self._render(cfg)

    def test_residual_placeholder_rejected(self) -> None:
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        broken = tmp / "netlify.toml.template"
        broken.write_text(
            TEMPLATE_PATH.read_text(encoding="utf-8") + '\n# leftover __UNREPLACED__\n',
            encoding="utf-8",
        )
        with self.assertRaises(self.preflight.PreflightError):
            self._render(self._valid_config(), template_path=broken)

    def test_vite_secret_token_or_key_rejected(self) -> None:
        for extra in (
            "VITE_FOO_SECRET",
            "VITE_SESSION_TOKEN",
            "VITE_PUBLIC_KEY",
        ):
            cfg = self._valid_config()
            cfg[extra] = "should-not-matter"
            with self.assertRaises(self.preflight.PreflightError):
                self._render(cfg)

    def test_spa_fallback_moved_first_is_rejected_then_canonical_passes(self) -> None:
        original = TEMPLATE_PATH.read_text(encoding="utf-8")
        blocks = re.split(r"(?=\[\[redirects\]\])", original)
        head = blocks[0]
        redirects = blocks[1:]
        self.assertEqual(len(redirects), 4)
        spa = next(block for block in redirects if 'from = "/*"' in block)
        rest = [block for block in redirects if block is not spa]
        mutated_text = head + spa + "".join(rest)
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        mutated = tmp / "netlify.toml.template"
        mutated.write_text(mutated_text, encoding="utf-8")
        with self.assertRaises(self.preflight.PreflightError):
            self._render(self._valid_config(), template_path=mutated)
        restored = self._render(self._valid_config())
        self.assertEqual(
            redirect_from_order(restored),
            ["/api/*", "/realms/*", "/resources/*", "/*"],
        )

    def test_realms_rule_removed_is_rejected_then_canonical_passes(self) -> None:
        original = TEMPLATE_PATH.read_text(encoding="utf-8")
        blocks = re.split(r"(?=\[\[redirects\]\])", original)
        head = blocks[0]
        redirects = [
            block
            for block in blocks[1:]
            if 'from = "/realms/*"' not in block
        ]
        mutated = Path(self.enterContext(tempfile.TemporaryDirectory())) / "t.toml.template"
        mutated.write_text(head + "".join(redirects), encoding="utf-8")
        with self.assertRaises(self.preflight.PreflightError):
            self._render(self._valid_config(), template_path=mutated)
        restored = self._render(self._valid_config())
        self.assertIn(f'to = "{VALID_EDGE}/realms/:splat"', restored)

    def test_template_and_generated_files_have_no_secret_needles(self) -> None:
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
        env_example = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
        generated = self._render(self._valid_config())
        for blob in (template, env_example, generated):
            lowered = blob.lower()
            for needle in SECRET_NEEDLES:
                self.assertNotIn(needle.lower(), lowered)
            self.assertNotIn("bearer ", lowered)
        self.assertNotRegex(env_example, r"(?m)^[A-Z0-9_]+=.+$")

    def test_refuses_repo_root_netlify_toml_and_template_overwrite(self) -> None:
        cfg = self._valid_config()
        with self.assertRaises(self.preflight.PreflightError):
            self.preflight.render_netlify_toml(cfg, ROOT / "netlify.toml")
        with self.assertRaises(self.preflight.PreflightError):
            self.preflight.render_netlify_toml(cfg, TEMPLATE_PATH)

    def test_rendered_file_is_regular_0600_and_symlink_output_is_rejected(self) -> None:
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        output = tmp / "netlify.toml"
        self.preflight.render_netlify_toml(self._valid_config(), output)
        info = output.lstat()
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)

        symlink = tmp / "linked.toml"
        os.symlink(output, symlink)
        with self.assertRaisesRegex(
            self.preflight.PreflightError, "LOCAL_DEPLOY_OUTPUT_PATH_INVALID"
        ):
            self.preflight.render_netlify_toml(self._valid_config(), symlink)

    def test_remote_smoke_keeps_bearer_out_of_curl_argv(self) -> None:
        text = REMOTE_SMOKE_PATH.read_text(encoding="utf-8")
        self.assertNotIn('-H "Authorization: Bearer', text)
        self.assertNotIn("$(cat \"$SMOKE_DIR/provider.token\")", text)
        self.assertNotIn("$(cat \"$SMOKE_DIR/client.token\")", text)
        self.assertIn('--header "@$SMOKE_DIR/provider.headers"', text)
        self.assertIn('--header "@$SMOKE_DIR/client.headers"', text)
        self.assertIn("chmod 600", text)

    def test_runbooks_lock_migration_backup_and_recovery_boundaries(self) -> None:
        deployment = DEPLOYMENT_PATH.read_text(encoding="utf-8")
        rollback = ROLLBACK_PATH.read_text(encoding="utf-8")
        handoff = HANDOFF_PATH.read_text(encoding="utf-8")
        for needle in (
            "f1_0017",
            "f1_0018",
            "pg_dump",
            "F1_SECRETS_DIR",
            "F1_KEYCLOAK_ISSUER_URL",
            "VITE_MATERIAL_RAG_REPORT_MOCK",
        ):
            self.assertIn(needle, deployment)
        self.assertIn("恢复式回滚", rollback)
        self.assertIn("禁止执行 Alembic downgrade", rollback)
        self.assertIn("人工二次确认", rollback)
        self.assertIn("DEPLOYMENT_PACKAGE_INTEGRATED_LOCAL", handoff)

    def test_remote_smoke_locks_dual_identity_health_and_test_mode(self) -> None:
        text = REMOTE_SMOKE_PATH.read_text(encoding="utf-8")
        for needle in (
            "provider_admin",
            "client_user",
            "/api/v1/analysis-reports/health/latest",
            "deterministic_local",
            "ark_calls=0",
            "mock_data=0",
        ):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
