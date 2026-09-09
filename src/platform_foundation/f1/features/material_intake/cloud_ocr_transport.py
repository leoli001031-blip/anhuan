"""Killable HTTPS exchange for cloud OCR, with no persisted plaintext/secret.

urllib's socket timeout is an idle timeout, not a total request deadline. A
separate process makes DNS, TLS, response headers and slow-drip bodies subject
to the same parent-enforced wall clock. This worker is never used for injected
unit-test transports.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys

MAX_REQUEST_BYTES = 9 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def bounded_https_exchange(url: str, headers: dict[str, str], body: bytearray, timeout: float) -> bytes:
    if len(body) > MAX_REQUEST_BYTES or not 0 < timeout <= 600:
        raise OSError("OCR_UNAVAILABLE")
    header = json.dumps({"url": url, "headers": headers, "timeout": timeout}, separators=(",", ":")).encode()
    if len(header) > 8192:
        raise OSError("OCR_UNAVAILABLE")
    process = None
    try:
        process = subprocess.Popen([sys.executable, "-I", str(Path(__file__).resolve())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}, close_fds=True, start_new_session=True)
        response, _ = process.communicate(header + b"\n" + body, timeout=timeout)
        if process.returncode != 0 or not 1 <= len(response) <= MAX_RESPONSE_BYTES:
            raise OSError("OCR_UNAVAILABLE")
        return response
    except subprocess.TimeoutExpired:
        raise OSError("OCR_UNAVAILABLE") from None
    finally:
        if process is not None:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()


def _worker():
    import resource
    import ssl
    from urllib.error import HTTPError
    from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener
    import certifi
    resource.setrlimit(resource.RLIMIT_CPU, (15, 16))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
    header = sys.stdin.buffer.readline(8193)
    if len(header) > 8192 or not header.endswith(b"\n"):
        raise RuntimeError
    config = json.loads(header)
    body = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if not 1 <= len(body) <= MAX_REQUEST_BYTES or not config["url"].startswith("https://"):
        raise RuntimeError
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise HTTPError(newurl, code, "OCR_UNAVAILABLE", headers, fp)
    opener = build_opener(NoRedirect(), ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where())))
    request = Request(config["url"], data=body, headers=config["headers"], method="POST")
    with opener.open(request, timeout=config["timeout"]) as response:
        if response.status != 200:
            raise RuntimeError
        total = 0
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise RuntimeError
            sys.stdout.buffer.write(chunk)


if __name__ == "__main__":
    try:
        _worker()
    except BaseException:
        sys.exit(1)
