# F0-H third-party notices

F0-H retains every F0-E base-image notice and adds the following locked
components.  Source and archive hashes are recorded in `component-lock.json`
and `sbom.spdx.json`.

- RapidOCR 3.9.2 — Apache-2.0.  The wheel contains the registered PP-OCRv6
  detector/recognizer and orientation classifier ONNX files.
- OmegaConf 2.3.1 — BSD-3-Clause.
- antlr4-python3-runtime 4.9.3 — BSD-3-Clause.  The runtime wheel is a
  reproducible local build of the registered official PyPI sdist.
- colorlog 6.12.0 — MIT.
- Requests 2.34.2 — Apache-2.0.
- certifi 2026.7.22 — MPL-2.0.
- charset-normalizer 3.4.9 — MIT.
- idna 3.18 — BSD-3-Clause.
- urllib3 2.7.0 — MIT.

Build-only provenance inputs: wheel 0.47.0 and packaging 26.3.  They are kept
for reproduction and are not imported by the F0-H runtime.
