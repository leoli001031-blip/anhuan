"""Dependency-free contracts for analysis-report concurrency and RQ failures."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT / "src/platform_foundation/f1/features/analysis_reports/repository.py"
SERVICE = ROOT / "src/platform_foundation/f1/features/analysis_reports/service.py"
WORKER = ROOT / "src/platform_foundation/f1/features/analysis_reports/worker.py"


def _function_source(path: Path, name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"missing function {name}")


class GenerationConcurrencyContractTests(unittest.TestCase):
    def test_generate_locks_report_before_allocating_next_version(self) -> None:
        generate = _function_source(SERVICE, "generate_report")
        lock = _function_source(REPOSITORY, "lock_report_for_generation")

        self.assertIn("FOR UPDATE OF report", lock)
        self.assertLess(
            generate.index("lock_report_for_generation"),
            generate.index("begin_generation"),
        )
        self.assertIn("report=locked_report", generate)
        self.assertIn("_GENERATION_START_STATUSES", generate)
        self.assertIn("raise ReportTransitionInvalid()", generate)


class GenerationWorkerFailureContractTests(unittest.TestCase):
    def test_evidence_integrity_errors_are_terminal(self) -> None:
        process = _function_source(WORKER, "_process_generation_job")

        self.assertIn("except (ValueError, InvalidTag):", process)
        self.assertIn("reason='REPORT_SOURCE_EVIDENCE_INVALID'", process)
        integrity_branch = process.split("except (ValueError, InvalidTag):", 1)[1].split(
            "except Exception:", 1
        )[0]
        self.assertIn("_fail_claim", integrity_branch)
        self.assertNotIn("_release_claim", integrity_branch)

    def test_rq_retry_state_controls_release_or_terminal_failure(self) -> None:
        retry = _function_source(WORKER, "_rq_retry_available")
        process = _function_source(WORKER, "_process_generation_job")
        transient_branch = process.split("except Exception:", 1)[1]

        self.assertIn("get_current_job()", retry)
        self.assertIn("current.should_retry", retry)
        self.assertIn("if _rq_retry_available():", transient_branch)
        self.assertIn("_release_claim", transient_branch)
        self.assertIn("reason='REPORT_GENERATION_RETRIES_EXHAUSTED'", transient_branch)
        self.assertIn("_fail_claim", transient_branch)
        self.assertTrue(transient_branch.rstrip().endswith("raise"))


if __name__ == "__main__":
    unittest.main()
