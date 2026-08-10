# This exact local content ID is the sole build input. The build command uses
# --pull=false and --network=none; no package manager or download is invoked.
FROM anhuan-f0e-runtime@sha256:afff23f8e469f76e8b94159ccd5a1a4345c12a9c72c95ad150acf51c8c86085a

LABEL org.opencontainers.image.title="anhuan-f0f-private-body" \
      org.opencontainers.image.version="0.1" \
      org.opencontainers.image.description="FIXTURE_ONLY private OCR body IPC executor" \
      io.anhuan.f0f.profile.sha256="6f396e7b3bde411a79aa7d40432e5df2c5ba5833955978facd76c6ddd9a41151" \
      io.anhuan.f0f.protocol="f0f-body-result-v1" \
      io.anhuan.f0f.external-processing="DENY" \
      io.anhuan.f0f.benchmark-tier="NONE" \
      io.anhuan.f0f.production-allowed="false"

USER 0:0
COPY runner.py component-lock.json THIRD_PARTY_NOTICES.md /opt/f0f/
RUN chmod 0555 /opt/f0f/runner.py \
    && chmod 0444 /opt/f0f/component-lock.json /opt/f0f/THIRD_PARTY_NOTICES.md

USER 65532:65532
WORKDIR /work
ENTRYPOINT ["python3", "-I", "-B", "/opt/f0f/runner.py"]
