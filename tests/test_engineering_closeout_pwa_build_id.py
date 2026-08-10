"""Targeted static and injection checks for build-isolated PWA caches."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src/web"
WORKER = WEB / "public/pwa-sw.js"
INJECTOR = WEB / "scripts/inject-pwa-build-id.mjs"
DOCKERFILE = ROOT / "infra/f1/web.Dockerfile"
NGINX = ROOT / "infra/f1/nginx/default.conf"
PLACEHOLDER = "__ANHUAN_PWA_BUILD_ID__"


class EngineeringCloseoutPwaBuildIdTests(unittest.TestCase):
    def _dist(self, parent: Path) -> Path:
        dist = parent / "dist"
        (dist / "assets").mkdir(parents=True)
        shutil.copyfile(WORKER, dist / "pwa-sw.js")
        (dist / "index.html").write_bytes(b"<html><script src='/assets/app.js'></script></html>")
        (dist / "assets/app.js").write_bytes(b"console.log('local');\n")
        return dist

    def test_worker_has_one_placeholder_and_build_scoped_cache_first_shell(self) -> None:
        source = WORKER.read_text(encoding="utf-8")
        self.assertEqual(source.count(PLACEHOLDER), 1)
        self.assertIn("`${CACHE_PREFIX}${PWA_BUILD_ID}-`", source)
        self.assertIn("key.startsWith(CACHE_PREFIX) && !CURRENT_CACHES.has(key)", source)
        navigation = source.split("async function navigationResponse", 1)[1].split(
            "function isStaticAsset", 1
        )[0]
        self.assertLess(navigation.index("const cachedShell = await shellCache.match"), navigation.index("await fetch(request)"))
        self.assertNotIn("skipWaiting()", source.split('addEventListener("message"', 1)[0])
        for boundary in ('"/api"', '"/realms"', '"/callback"', 'request.headers.has("Authorization")'):
            self.assertIn(boundary, source)

    def test_manifest_is_served_with_the_install_gate_content_type(self) -> None:
        nginx = NGINX.read_text(encoding="utf-8")
        manifest = nginx.split("location = /manifest.webmanifest", 1)[1].split(
            "location /", 1
        )[0]
        self.assertIn("default_type application/manifest+json;", manifest)
        self.assertIn("try_files $uri =404;", manifest)
        self.assertIn(
            'if (pathname === "/manifest.webmanifest") return "application/manifest";',
            WORKER.read_text(encoding="utf-8"),
        )

    def test_injector_is_deterministic_and_docker_build_runs_it(self) -> None:
        injector_source = INJECTOR.read_text(encoding="utf-8")
        for token in (
            "constants.O_NOFOLLOW",
            "constants.O_DIRECTORY",
            "await handle.stat()",
            "await handle.readFile()",
            "verifyTreeUnchanged(files, directories)",
        ):
            self.assertIn(token, injector_source)
        self.assertIn(
            "node ./scripts/inject-pwa-build-id.mjs ./dist",
            DOCKERFILE.read_text(encoding="utf-8"),
        )
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn('ARG ANHUAN_PWA_UPDATE_PROBE=""', dockerfile)
        self.assertIn("public/pwa-update-probe.txt", dockerfile)
        self.assertIn('io.anhuan.pwa-update-probe="${ANHUAN_PWA_UPDATE_PROBE}"', dockerfile)
        with tempfile.TemporaryDirectory(prefix="anhuan-pwa-build-") as temporary:
            dist = self._dist(Path(temporary))
            result = subprocess.run(
                ["node", str(INJECTOR), str(dist)],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            match = re.fullmatch(r"PWA_BUILD_ID_INJECTED ([0-9a-f]{64})\n", result.stdout)
            self.assertIsNotNone(match)
            rendered = (dist / "pwa-sw.js").read_text(encoding="utf-8")
            self.assertNotIn(PLACEHOLDER, rendered)
            self.assertIn(match.group(1), rendered)
            self.assertEqual(list(dist.glob(".pwa-sw.js.*.tmp")), [])

            digest = hashlib.sha256()
            digest.update(b"ANHUAN_INTERNAL_PWA_BUILD_V1\0")
            for relative in ("assets/app.js", "index.html", "pwa-sw.js"):
                relative_bytes = relative.encode()
                contents = WORKER.read_bytes() if relative == "pwa-sw.js" else (dist / relative).read_bytes()
                digest.update(len(relative_bytes).to_bytes(8, "big"))
                digest.update(relative_bytes)
                digest.update(len(contents).to_bytes(8, "big"))
                digest.update(contents)
            self.assertEqual(match.group(1), digest.hexdigest())

    def test_injector_rejects_symlinks_without_replacing_worker(self) -> None:
        with tempfile.TemporaryDirectory(prefix="anhuan-pwa-symlink-") as temporary:
            dist = self._dist(Path(temporary))
            (dist / "escape").symlink_to(Path(temporary) / "outside")
            result = subprocess.run(
                ["node", str(INJECTOR), str(dist)],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("PWA_BUILD_SYMLINK_REJECTED", result.stderr)
            self.assertEqual((dist / "pwa-sw.js").read_text(encoding="utf-8").count(PLACEHOLDER), 1)

    def test_injector_rejects_nonregular_entries_and_traversal(self) -> None:
        with tempfile.TemporaryDirectory(prefix="anhuan-pwa-special-") as temporary:
            dist = self._dist(Path(temporary))
            fifo = dist / "special.fifo"
            os.mkfifo(fifo)
            nonregular = subprocess.run(
                ["node", str(INJECTOR), str(dist)],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertNotEqual(nonregular.returncode, 0)
            self.assertIn("PWA_BUILD_NON_REGULAR_REJECTED", nonregular.stderr)
            fifo.unlink()

            traversal = subprocess.run(
                ["node", str(INJECTOR), f"{dist}/../dist"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertNotEqual(traversal.returncode, 0)
            self.assertIn("PWA_BUILD_PATH_TRAVERSAL", traversal.stderr)


if __name__ == "__main__":
    unittest.main()
