# F0-F private body runtime

Status: `FIXTURE_ONLY / BENCHMARK_TIER=NONE / ANNOTATION_PENDING / NOT PRODUCTION`.
The image extends the exact local F0-E image content ID and adds no package,
model, or network access.

## Offline build

Verify the frozen F0-E image ID first, then build without pulling or networking:

```sh
docker image inspect sha256:afff23f8e469f76e8b94159ccd5a1a4345c12a9c72c95ad150acf51c8c86085a
DOCKER_BUILDKIT=0 docker build --pull=false --network=none --platform linux/arm64 --tag anhuan-f0f-runtime:0.1-arm64 infra/f0f
```

The Dockerfile has one `FROM`: the immutable local F0-E repository digest
`anhuan-f0e-runtime@sha256:afff...6085a`. The mutable tag is never a build
input. The Dockerfile contains no package manager instruction and never copies
from the host outside this directory.

The local Docker 29 BuildKit resolver attempts a registry lookup even when the
repository digest already exists locally. This is why the checked build command
uses the local legacy builder: it resolves the frozen digest from the daemon,
while `--pull=false --network=none` keeps all build steps offline.

## Private binary protocol

stdin is exactly the frozen `f0e-envelope-v1`: uint32 big-endian canonical JSON
header length, canonical ASCII JSON header, then exactly the declared PDF/JPEG
bytes. One invocation accepts one page/unit. All F0-E size, geometry, render,
pixel, block, character, and timeout limits remain enforced.

stdout is exactly one canonical ASCII JSON line with schema
`f0f-body-result-v1`, bounded to 8 MiB. Success preserves the complete F0-E
result shape, changes `raw_text_emitted` to true for the private pipe, and adds
one top-level `blocks` array. Every block has exactly `index`, NFC/LF-normalized
`text`, the F0-E rounded rendered-pixel `bbox`, and `confidence_ppm`. Errors
contain only a stable error code and
never source bytes, text, paths, filenames, exception strings, or library logs.

stdout is confidential IPC, not a terminal interface. The supervisor must use
`shell=False`, `stdin/stdout/stderr=PIPE`, never inherit or tee stdout, parse it
in memory, validate every key/hash/count, encrypt the body immediately, then
discard the bytes. It must expose only aggregate hashes and reason codes.

## Fixed runtime boundary

The caller uses the F0-E hardened argv with only these substitutions:

- name pattern `anhuan-f0f-<32 lowercase hex>`;
- F0-F immutable image content ID from `runtime-lock.json`;
- checked absolute path to this `seccomp.json`.

Required flags remain: `--rm -i --pull never`, arm64, network/ipc none,
read-only, non-root 65532, all capabilities dropped, no-new-privileges, checked
seccomp, pids/memory/CPU/ulimit bounds, private `/tmp` and `/work` tmpfs, and
log-driver none. Mounts, host paths, environment overrides, shell, output files,
entrypoint overrides, and mutable image references are forbidden. Timeout
cleanup kills the Docker CLI process group, kills/removes the exact validated
container name, and requires inspect to report it absent.
