# F0-E third-party notices

This is an engineering inventory for the local `FIXTURE_ONLY` baseline. It is
not legal approval for customer, production, redistribution, or professional
use. Exact archives and hashes are in `requirements.lock`,
`component-lock.json`, and the external SPDX SBOM.

- `pypdfium2` 5.12.1: BSD-3-Clause wrapper; bundled PDFium 152.0.7947.0 and
  its build-time third parties retain the license files shipped inside the
  `pypdfium2` wheel under its `.dist-info/licenses` directory.
- `RapidOCR` / `rapidocr-onnxruntime` 1.4.4 and the three ONNX files injected
  by the tagged upstream build workflow: declared Apache-2.0 by upstream and
  wheel metadata. The exact upstream commit, workflow hash, model archive hash,
  model hashes, and model sizes are pinned in `component-lock.json`.
- ONNX Runtime 1.28.0: MIT.
- OpenCV Python Headless 5.0.0.93: Apache-2.0, with bundled third-party notices
  retained in its wheel distribution. RapidOCR metadata names the GUI
  `opencv-python` distribution, but this baseline deliberately substitutes the
  same-version headless distribution because RapidOCR uses only the `cv2`
  compute API. This avoids the GUI wheel's unresolved `libxcb.so.1` dependency.
- NumPy 2.4.6: BSD-3-Clause; Shapely 2.1.2: BSD-3-Clause; Pillow 12.3.0:
  HPND; PyYAML 6.0.3: MIT; pyclipper 1.4.0: MIT; six 1.17.0: MIT;
  packaging 26.3: Apache-2.0 OR BSD-2-Clause; protobuf 7.35.1: BSD-3-Clause;
  flatbuffers 25.12.19: Apache-2.0; tqdm 4.70.0: MPL-2.0 AND MIT.

The installed wheels retain their own license files where supplied. A package
metadata declaration does not settle trademark, patent, training-data, model,
or downstream redistribution questions; those remain closed P0 decisions.
