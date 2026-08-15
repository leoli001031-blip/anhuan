"""Bounded FIFO wrapper around the immutable offline F0-H runner.

One verifier talks to one server in strict serial order.  Request framing is
four-byte big-endian envelope length followed by one ``f0e-envelope-v1``;
response framing is four-byte big-endian JSON length followed by canonical
JSON.  No envelope, OCR body, path, or subprocess stderr is logged or
persisted.
"""
from __future__ import annotations

import json
import os
import selectors
import signal
import stat
import subprocess
import time
from pathlib import Path


IPC_DIRECTORY = Path("/run/material-rag-ocr")
REQUEST_FIFO = IPC_DIRECTORY / "request.fifo"
RESPONSE_FIFO = IPC_DIRECTORY / "response.fifo"
READY_PATH = IPC_DIRECTORY / "ready"
RUNNER_ARGV = (
    "/usr/local/bin/python3",
    "-I",
    "-B",
    "/opt/f0h/runner.py",
    "body",
)
MAX_HEADER_BYTES = 4096
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_ENVELOPE_BYTES = 64 * 1024 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_STDERR_BYTES = 4096
RUNNER_TIMEOUT_SECONDS = 130.0


class RequestError(RuntimeError):
    pass


def _canonical_json(value: dict[str, object]) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


def _failure(code: str) -> bytes:
    return _canonical_json(
        {"schema": "anhuan-material-rag-ocr-error-v1", "reason": code}
    )


def _read_exact(descriptor: int, size: int) -> bytearray:
    if size < 0 or size > MAX_ENVELOPE_BYTES:
        raise RequestError("OCR_REQUEST_LIMIT")
    body = bytearray()
    while len(body) < size:
        chunk = os.read(descriptor, min(65536, size - len(body)))
        if not chunk:
            raise RequestError("OCR_REQUEST_TRUNCATED")
        body.extend(chunk)
    return body


def _validate_envelope(envelope: bytearray) -> None:
    if not 5 <= len(envelope) <= MAX_ENVELOPE_BYTES:
        raise RequestError("OCR_ENVELOPE_INVALID")
    header_size = int.from_bytes(envelope[:4], "big")
    if not 1 <= header_size <= MAX_HEADER_BYTES:
        raise RequestError("OCR_ENVELOPE_INVALID")
    boundary = 4 + header_size
    if boundary >= len(envelope):
        raise RequestError("OCR_ENVELOPE_INVALID")
    try:
        header = json.loads(bytes(envelope[4:boundary]).decode("ascii", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RequestError("OCR_ENVELOPE_INVALID") from None
    if (
        not isinstance(header, dict)
        or header.get("schema") != "f0e-envelope-v1"
        or header.get("document_type") not in {"PDF", "JPEG"}
        or type(header.get("source_size")) is not int
        or not 8 <= int(header["source_size"]) <= MAX_SOURCE_BYTES
        or int(header["source_size"]) != len(envelope) - boundary
    ):
        raise RequestError("OCR_ENVELOPE_INVALID")


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        process.send_signal(signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        raise RequestError("OCR_RUNNER_CLEANUP_FAILED") from None


def _execute(envelope: bytearray) -> bytes:
    try:
        process = subprocess.Popen(
            RUNNER_ARGV,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            start_new_session=True,
        )
    except OSError:
        raise RequestError("OCR_RUNNER_START_FAILED") from None
    if process.stdin is None or process.stdout is None or process.stderr is None:
        _terminate(process)
        raise RequestError("OCR_RUNNER_START_FAILED")

    selector = selectors.DefaultSelector()
    input_view = memoryview(envelope)
    input_offset = 0
    stdout = bytearray()
    stderr_size = 0
    deadline = time.monotonic() + RUNNER_TIMEOUT_SECONDS
    for stream in (process.stdin, process.stdout, process.stderr):
        os.set_blocking(stream.fileno(), False)
    selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    try:
        while selector.get_map():
            if time.monotonic() >= deadline:
                raise RequestError("OCR_RUNNER_TIMEOUT")
            events = selector.select(timeout=0.25)
            for key, _mask in events:
                if key.data == "stdin":
                    try:
                        written = os.write(
                            key.fd,
                            input_view[input_offset : input_offset + 65536],
                        )
                    except BlockingIOError:
                        continue
                    input_offset += written
                    if input_offset == len(input_view):
                        selector.unregister(key.fileobj)
                        process.stdin.close()
                elif key.data == "stdout":
                    try:
                        chunk = os.read(key.fd, 65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    stdout.extend(chunk)
                    if len(stdout) > MAX_OUTPUT_BYTES:
                        raise RequestError("OCR_RUNNER_OUTPUT_LIMIT")
                else:
                    try:
                        chunk = os.read(key.fd, 4096)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    stderr_size += len(chunk)
                    if stderr_size > MAX_STDERR_BYTES:
                        raise RequestError("OCR_RUNNER_STDERR_LIMIT")
        remaining = max(0.1, deadline - time.monotonic())
        try:
            returncode = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise RequestError("OCR_RUNNER_TIMEOUT") from None
    except RequestError:
        _terminate(process)
        raise
    finally:
        selector.close()
        input_view.release()
        for stream in (process.stdin, process.stdout, process.stderr):
            if not stream.closed:
                stream.close()
    if returncode != 0 or stderr_size != 0 or not stdout:
        raise RequestError("OCR_RUNNER_FAILED")
    try:
        payload = json.loads(stdout.decode("ascii", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RequestError("OCR_RUNNER_OUTPUT_INVALID") from None
    if not isinstance(payload, dict) or payload.get("schema") != "f0f-body-result-v1":
        raise RequestError("OCR_RUNNER_OUTPUT_INVALID")
    return _canonical_json(payload)


def _serve_request(request_descriptor: int, response_descriptor: int) -> None:
    envelope = bytearray()
    try:
        size_bytes = _read_exact(request_descriptor, 4)
        size = int.from_bytes(size_bytes, "big")
        size_bytes[:] = b"\0" * len(size_bytes)
        envelope = _read_exact(request_descriptor, size)
        _validate_envelope(envelope)
        response = _execute(envelope)
    except (OSError, RequestError):
        response = _failure("LOCAL_OCR_REQUEST_FAILED")
    finally:
        envelope[:] = b"\0" * len(envelope)
        envelope.clear()
    framed = len(response).to_bytes(4, "big") + response
    offset = 0
    while offset < len(framed):
        written = os.write(response_descriptor, framed[offset : offset + 65536])
        if written < 1:
            raise RequestError("OCR_RESPONSE_WRITE_FAILED")
        offset += written


def main() -> int:
    info = IPC_DIRECTORY.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        return 2
    try:
        for path in (REQUEST_FIFO, RESPONSE_FIFO, READY_PATH):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        os.mkfifo(REQUEST_FIFO, 0o600)
        os.mkfifo(RESPONSE_FIFO, 0o600)
        descriptor = os.open(
            READY_PATH,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.close(descriptor)
    except OSError:
        return 2
    try:
        while True:
            request_descriptor = os.open(REQUEST_FIFO, os.O_RDONLY)
            response_descriptor = -1
            try:
                response_descriptor = os.open(RESPONSE_FIFO, os.O_WRONLY)
                _serve_request(request_descriptor, response_descriptor)
            finally:
                os.close(request_descriptor)
                if response_descriptor >= 0:
                    os.close(response_descriptor)
    finally:
        for path in (READY_PATH, REQUEST_FIFO, RESPONSE_FIFO):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
