# F0-H local PP-OCRv6 runtime

This directory freezes a new provider beside, rather than over, the immutable
F0-E/F0-F PP-OCRv4 runtime.  It is a fixture-only Linux/arm64 executor using
RapidOCR 3.9.2, PP-OCRv6-small detection/recognition and ONNX Runtime 1.28.0.
The orientation classifier is the separately disclosed legacy mobile model.

Build from the repository root with no network and no pull:

```sh
DOCKER_BUILDKIT=0 docker build --pull=false --network=none \
  --file infra/f0h/Dockerfile --tag anhuan-f0h-build:0.1 .
```

Execution accepts only the binary `f0e-envelope-v1` on stdin.  The immutable
image content ID from `runtime-lock.json` must be used with `--pull never`,
`--network none`, no bind mounts, a read-only root filesystem, user 65532,
all capabilities dropped, no-new-privileges, the registered seccomp profile,
and the registered CPU/memory/PID/output/timeout limits.

`body` returns `f0f-body-result-v1` only through the caller-owned private pipe.
`evidence` returns the body-free `f0e-result-v1` projection.  Runtime model
download is forbidden; all three model paths are explicit and missing models
fail closed.  Neither mode is an accuracy, Gold, search, production, or
professional acceptance claim.

ANTLR 4.9.3 has no official wheel.  The committed runtime wheel was built from
the locked official PyPI sdist with the locked build inputs and
`SOURCE_DATE_EPOCH=1636156800`; two independent builds produced the same SHA.
