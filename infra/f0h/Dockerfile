# The immutable F0-E content ID supplies the already-frozen renderer, NumPy,
# ONNX Runtime, OpenCV and resource-compatible Linux/arm64 Python runtime.
FROM anhuan-f0e-runtime@sha256:afff23f8e469f76e8b94159ccd5a1a4345c12a9c72c95ad150acf51c8c86085a

LABEL org.opencontainers.image.title="anhuan-f0h-ppocrv6-small" \
      org.opencontainers.image.version="0.1" \
      org.opencontainers.image.description="FIXTURE_ONLY offline PP-OCRv6-small adapter" \
      io.anhuan.f0h.profile.sha256="9c320d4d978fe2d0d5a69f6411b58744a203600909d1171a49aed700de8a4440" \
      io.anhuan.f0h.provider="ppocrv6-small" \
      io.anhuan.f0h.external-processing="DENY" \
      io.anhuan.f0h.runtime-downloads="false" \
      io.anhuan.f0h.accuracy-status="NOT_EVALUATED" \
      io.anhuan.f0h.production-allowed="false"

ENV HOME=/nonexistent \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=UTC \
    PYTHONHASHSEED=0 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONNOUSERSITE=1 \
    PIP_NO_INDEX=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    XDG_CACHE_HOME=/tmp/f0h-cache \
    TMPDIR=/tmp \
    OMP_NUM_THREADS=1 \
    OMP_THREAD_LIMIT=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    MALLOC_ARENA_MAX=2 \
    OPENCV_LOG_LEVEL=OFF

USER 0:0
COPY infra/f0h/wheels /opt/f0h/wheels
COPY infra/f0h/build-sources /opt/f0h/build-sources
COPY requirements/f0h.lock /opt/f0h/requirements.lock
RUN python3 -m pip install \
      --no-index \
      --no-cache-dir \
      --no-compile \
      --no-deps \
      --only-binary=:all: \
      --require-hashes \
      --target /opt/f0h/python \
      --find-links /opt/f0h/wheels \
      --requirement /opt/f0h/requirements.lock

# These copies make the model identity independently auditable from the wheel
# archive and guarantee the exact explicit paths used by the runner.
COPY infra/f0h/models /opt/f0h/python/rapidocr/models
COPY infra/f0h/model-metadata/config.yaml /opt/f0h/python/rapidocr/config.yaml
COPY infra/f0h/model-metadata/default_models.yaml /opt/f0h/python/rapidocr/default_models.yaml
COPY infra/f0h/runner.py infra/f0h/component-lock.json infra/f0h/THIRD_PARTY_NOTICES.md /opt/f0h/

RUN chmod -R a-w /opt/f0h \
    && find /opt/f0h -type d -exec chmod 0555 {} + \
    && find /opt/f0h -type f -exec chmod 0444 {} + \
    && chmod 0555 /opt/f0h/runner.py

USER 65532:65532
WORKDIR /work
ENTRYPOINT ["python3", "-I", "-B", "/opt/f0h/runner.py"]
