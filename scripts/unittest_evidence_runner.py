#!/usr/bin/env python3
"""Run unittest with a flushed event journal, including partial process evidence.

Only real TestCase instances are counted. Loader/fixture errors have their own
events; multiple failed subtests remain one failed parent case.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
import unittest
from pathlib import Path


def flatten(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


def run(suites: list[str], journal_path: Path) -> int:
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    with journal_path.open("w", encoding="utf-8", buffering=1) as journal:
        def emit(event, **fields):
            journal.write(json.dumps({"event": event, "time": time.time(), **fields}, ensure_ascii=False) + "\n")
            journal.flush()

        emit("runner_start", suites=suites, python=sys.version, executable=sys.executable)
        loader = unittest.TestLoader()
        assembled = unittest.TestSuite()
        discovery_errors = 0
        for name in suites:
            prior_errors = len(loader.errors)
            try:
                loaded = loader.loadTestsFromName(name)
            except Exception:
                discovery_errors += 1
                detail = traceback.format_exc()
                emit("discovery_error", suite=name, traceback=detail)
                print(detail, file=sys.stderr)
                continue
            for detail in loader.errors[prior_errors:]:
                discovery_errors += 1
                emit("discovery_error", suite=name, traceback=detail)
                print(detail, file=sys.stderr)
            # Keep the suite hierarchy, which controls setUp/tearDown lifetimes.
            def remove_loader_failures(group):
                kept = unittest.TestSuite()
                for test in group:
                    if isinstance(test, unittest.TestSuite):
                        kept.addTest(remove_loader_failures(test))
                    elif not isinstance(test, unittest.loader._FailedTest):
                        kept.addTest(test)
                return kept
            assembled.addTest(remove_loader_failures(loaded))
        cases = list(flatten(assembled))
        # Queue ordinals also handles intentionally repeated instances/IDs.
        ordinals = {}
        for ordinal, test in enumerate(cases):
            ordinals.setdefault(id(test), []).append(ordinal)
        emit("collected", cases=[{"ordinal": n, "id": t.id()} for n, t in enumerate(cases)])

        class EvidenceResult(unittest.TextTestResult):
            def startTest(self, test):
                super().startTest(test)
                self.active = {"ordinal": ordinals[id(test)].pop(0), "id": test.id()}
                self.outcomes = []
                emit("case_start", **self.active)

            def mark(self, test, outcome, detail=None):
                if isinstance(test, unittest.TestCase) and hasattr(self, "active"):
                    self.outcomes.append(outcome)
                    emit("case_outcome", **self.active, outcome=outcome, detail=detail)
                else:
                    emit("fixture_" + outcome, id=test.id(), detail=detail)

            def addSuccess(self, test):
                super().addSuccess(test)
                self.mark(test, "passed")

            def addFailure(self, test, err):
                super().addFailure(test, err)
                self.mark(test, "failed", self._exc_info_to_string(err, test))

            def addError(self, test, err):
                super().addError(test, err)
                self.mark(test, "error", self._exc_info_to_string(err, test))

            def addSkip(self, test, reason):
                super().addSkip(test, reason)
                self.mark(test, "skipped", reason)

            def addExpectedFailure(self, test, err):
                super().addExpectedFailure(test, err)
                self.mark(test, "expected_failure", self._exc_info_to_string(err, test))

            def addUnexpectedSuccess(self, test):
                super().addUnexpectedSuccess(test)
                self.mark(test, "unexpected_success")

            def addSubTest(self, test, subtest, err):
                super().addSubTest(test, subtest, err)
                outcome = "passed" if err is None else (
                    "failed" if issubclass(err[0], test.failureException) else "error")
                if err is not None:
                    self.outcomes.append(outcome)
                emit("subtest", **self.active, subtest_id=subtest.id(), outcome=outcome,
                     detail=None if err is None else self._exc_info_to_string(err, test))

            def stopTest(self, test):
                severity = ["error", "failed", "unexpected_success", "skipped", "expected_failure", "passed"]
                outcome = next((name for name in severity if name in self.outcomes), "incomplete")
                emit("case_finish", **self.active, outcome=outcome)
                del self.active
                super().stopTest(test)

        try:
            result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(assembled)
        except BaseException:
            emit("runner_error", traceback=traceback.format_exc())
            raise
        emit("runner_complete", tests_run=result.testsRun, successful=result.wasSuccessful(),
             discovery_errors=discovery_errors)
        return 0 if result.wasSuccessful() and not discovery_errors else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument("suites", nargs="+")
    args = parser.parse_args()
    # Script execution otherwise omits the checkout root from sys.path.
    sys.path.insert(0, str(Path.cwd()))
    return run(args.suites, args.journal)


if __name__ == "__main__":
    sys.exit(main())
