# syntax=docker/dockerfile:1.7

# The arm64 manifest, rather than the mutable tag, is the build input.
FROM docker.io/library/python@sha256:9def7f10e7ab48aa9773d286e957f0732253fc3c9658cc7affa7a5a5540fda78 AS wheel-install

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.lock /build/requirements.lock
RUN python3 -m pip install \
      --no-cache-dir \
      --no-compile \
      --no-deps \
      --only-binary=:all: \
      --require-hashes \
      --target /opt/python \
      --requirement /build/requirements.lock

FROM docker.io/library/python@sha256:9def7f10e7ab48aa9773d286e957f0732253fc3c9658cc7affa7a5a5540fda78

LABEL org.opencontainers.image.title="anhuan-f0e-offline-ocr" \
      org.opencontainers.image.version="0.1" \
      org.opencontainers.image.description="FIXTURE_ONLY body-free OCR evidence executor" \
      io.anhuan.f0e.profile.sha256="8b79ddd2e30708f15f72493bbba937a005ffe61d39c0da9baade89e264b674b6" \
      io.anhuan.f0e.external-processing="DENY" \
      io.anhuan.f0e.max-pdf-pages="128" \
      io.anhuan.f0e.max-pages-per-document-job="16" \
      io.anhuan.f0e.units-per-execution="1" \
      io.anhuan.f0e.render-dpi="250" \
      io.anhuan.f0e.max-pixels-per-page="16000000" \
      io.anhuan.f0e.manual-review-confidence-floor-ppm="0"

ENV HOME=/nonexistent \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=UTC \
    PYTHONHASHSEED=0 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONNOUSERSITE=1 \
    PIP_NO_INDEX=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    XDG_CACHE_HOME=/tmp/f0e-cache \
    TMPDIR=/tmp \
    OMP_NUM_THREADS=1 \
    OMP_THREAD_LIMIT=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    MALLOC_ARENA_MAX=2 \
    OPENCV_LOG_LEVEL=OFF

COPY --from=wheel-install /opt/python /opt/python
COPY runner.py component-lock.json requirements.lock THIRD_PARTY_NOTICES.md /opt/f0e/

RUN mkdir -p /work /tmp \
    && chown 65532:65532 /work /tmp \
    && chmod 0700 /work /tmp \
    && chmod 0444 /opt/f0e/component-lock.json /opt/f0e/requirements.lock /opt/f0e/THIRD_PARTY_NOTICES.md \
    && chmod 0555 /opt/f0e/runner.py

USER 65532:65532
WORKDIR /work
ENTRYPOINT ["python3", "-I", "-B", "/opt/f0e/runner.py"]
