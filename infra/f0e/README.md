# F0-E offline OCR runtime

Status: `FIXTURE_ONLY / BENCHMARK_TIER=NONE / NOT GOLD / NOT PRODUCTION`.
External OCR/LLM, runtime downloads, raw-text persistence, confidence-based
acceptance, and professional conclusions remain closed.

## Build boundary

Build context is this directory only. Build-stage package-network access is
required once to fetch the exact hash-pinned Linux/arm64 wheels. Runtime has no
network and cannot download. Build alias:

```sh
docker build --platform linux/arm64 --tag anhuan-f0e-runtime:0.1-arm64 infra/f0e
```

After the final build, use `docker image inspect .Id` and write that immutable
`sha256:<64>` into the external `runtime-lock.json` and SBOM. The mutable tag is
never accepted by the execution supervisor.

## Binary envelope

stdin is `uint32_be(header length) + canonical ASCII JSON header + source`.
Canonical JSON means sorted keys, no insignificant whitespace, ASCII escaping,
and separators `,` / `:`. The stream must end after exactly `source_size`
bytes. One invocation handles one page/unit.

Common header keys are `schema=f0e-envelope-v1`, `document_type`,
`source_sha256`, `source_size`, `expected_total_pages`, `page_no`, and a
64-hex `source_unit_id`. PDF adds the F0-C `media_box`/`crop_box` objects with
three-decimal string coordinates and `rotation_degrees`. JPEG adds
`image_width_px` and `image_height_px`; it must be one page.

stdout is one canonical JSON object. It never contains recognized text, source
bytes, host paths, filenames, exception messages, or library logs. Non-empty
OCR output is only `OCR_EVIDENCE_CAPTURED_NOT_VALIDATED`; empty output is
`MANUAL_REVIEW_REQUIRED/EMPTY_OCR_OUTPUT`. With no Gold,
`manual_review_confidence_floor_ppm=0`; confidence is evidence only.

## Fixed runtime argv

The service uses `subprocess` with `shell=False`, a bounded stdin/stdout, an
external 120-second kill deadline, and the following fixed argv. Replace only
the three validated placeholders: `IMAGE_ID` must match external lock,
`SECCOMP_ABSOLUTE_PATH` must resolve to this checked file and match its locked
hash, and `RUN_HEX` must be 32 lowercase hex generated locally by the
supervisor (never copied from a request).

```text
docker run --rm -i --pull never --name anhuan-f0e-RUN_HEX
--platform linux/arm64 --network none --ipc none --shm-size 64m
--read-only --user 65532:65532 --cap-drop ALL
--security-opt no-new-privileges
--security-opt seccomp=SECCOMP_ABSOLUTE_PATH
--pids-limit 64 --memory 1024m --memory-swap 1024m --cpus 1
--ulimit core=0:0 --ulimit nofile=64:64 --ulimit nproc=64:64
--tmpfs /tmp:rw,nosuid,nodev,noexec,size=16m,uid=65532,gid=65532,mode=0700
--tmpfs /work:rw,nosuid,nodev,noexec,size=256m,uid=65532,gid=65532,mode=0700
--log-driver none sha256:IMAGE_ID
```

There are no volumes, host paths, environment overrides, shell, output mount,
or entrypoint override. Both writable locations are private tmpfs; stopping or
force-killing the container destroys them, and no source/output temp exists on
the host.

On timeout or any uncertain CLI failure, the supervisor first kills its Docker
CLI process group, then calls `docker kill` and `docker rm -f` with that exact
validated name, and finally requires `docker container inspect` to report the
name absent. The name is never written to runner stdout, errors, or evidence.
