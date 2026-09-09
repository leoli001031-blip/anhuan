# 材料提取质量门

`material_quality_gate.py` 使用当前 PDF 有效文本提取器、DOCX 原生提取器、XLSX 原生提取器和 JPEG 原生解码/OCR 接线，逐块比较人工金标。默认不连接数据库、对象存储或收费模型，不修改冻结证据。显式 `--live-ocr` 可对已复核的真实输入调用现役云 OCR。

## 运行

```sh
python scripts/material_quality_gate.py --synthetic --output /absolute/evidence/synthetic.json
python scripts/material_quality_gate.py --manifest /absolute/materials/manifest.json --output /absolute/evidence/real.json
python scripts/material_quality_gate.py --manifest /absolute/materials/manifest.json --live-ocr --output /absolute/evidence/live.json
python scripts/material_quality_gate.py --output /absolute/evidence/missing-input.json
python -m unittest discover -s tests -p test_material_quality_gate.py -v
```

调用函数为 `run_quality(manifest=Path(...), live_ocr=False) -> dict`，不指定 manifest 时真实模式直接返回 `NOT_TESTED / REAL_MATERIAL_MANIFEST_REQUIRED`；`run_quality(synthetic=True)` 独立运行工程基准。CLI 退出码：0 通过；1 失败；2 未测。真实模式 `status` 为 `PASSED / FAILED / NOT_TESTED`；合成模式为 `TARGETED_TEST_PASSED / FAIL / NOT_TESTED`。自动验收入口必须保留 `scope` 与 `proof_state`，不得把合成结果升格为真实质量通过。

## 合成工程范围

可审查的输入和金标位于 `tests/material_quality_samples/`：两页英文 PDF、中文 DOCX 段落与表格、两个中文 sheet 的 XLSX、JPEG，以及各格式损坏输入。`manifest.json` 的金标从生成规范单独编写，没有调用提取器或模型生成金标。`generate_inputs.py` 可在锁定依赖环境中重建输入；生成操作不会发生在质量门运行时，门始终先校验保存的原件 SHA。

共 12 个逐例结果：4 个正常格式样本，以及 4 个损坏输入、数字/单位/遗漏/定位各 1 个错误金标。失败样本保留 `outcome=FAIL` 与对应失败维度，整体工程门仅在 4 个正常样本通过、8 个指定拒绝都实际出现时通过。`counts.failed=8` 是预期失败样本的原始计数；另有 `expected_rejections=8`，不能把失败样本抹成成功。

JPEG 实际解码和整个生产提取函数均运行，但模型响应是显式 `SYNTHETIC_STUB`。替身核验渲染后图片 SHA 后返回固定响应；这只能证明格式定位与提取接线，不能证明识别准确率。PDF 样本使用原生文本；需 OCR 的 PDF 不运行替身。

## 真实样本输入合同

金标文件和原件放在同一私有目录；原件路径必须是该目录内相对路径，不能越界或用绝对路径。manifest 的基本结构为：

```json
{
  "schema_version": 1,
  "scope": "real",
  "authorized_for_local_processing": true,
  "gold_review": {
    "reviewer": "实际复核人",
    "reviewed_at": "实际复核日期",
    "method": "逐项核对原件可见内容，独立记录全文与格式位置"
  },
  "samples": [
    {
      "id": "pdf-001",
      "format": "pdf",
      "path": "approved-material.pdf",
      "sha256": "原件完整字节的64位小写SHA256",
      "gold": [
        {
          "text": "人工核对的该页完整文本，如 COD 42.50 mg/L",
          "locator": {"schema_version": 2, "kind": "pdf_page", "page_number": 1}
        }
      ]
    }
  ]
}
```

以上只展示一个样本结构，不能直接通过：完整集必须至少有 PDF、DOCX、XLSX、JPEG 各一个正常样本，最多 100 个，每个原件不超过 25 MiB。每个样本金标必须非空且含数字及支持的单位；单位检测集为 `mg/L, mg/m3, m3/h, t/a, kg, dB, m², %`。全文精确比较仍涵盖单位表之外的所有内容。`authorized_for_local_processing` 和 `gold_review` 是输入方声明，工具不能代替授权或核实复核人身份。

`gold` 应覆盖全部预期非空页/段落/单元格/图片块，不只写几个关键词。格式定位必须满足 `features/evidence/contracts.py` 的 canonical v2 定位合同；完整 DOCX/XLSX/JPEG 示例见合成 manifest。DOCX 定位是 XML 正文位置，不声称 Word 排版页码；XLSX 定位是 sheet 和单元格；JPEG 是原图/渲染尺寸、方向及渲染 SHA。不得为非 PDF 材料伪造页码。

真实模式禁止 `synthetic_ocr` 和 `expected_failure`。默认真实 JPEG、需 OCR 的 PDF 返回 `NOT_TESTED / REAL_OCR_INPUT_UNAVAILABLE`，因此仅有原生格式通过不能把四格式质量记为完成。

显式传入 `--live-ocr`（函数参数 `live_ocr=True`）表示允许向当前配置的云 provider 发送本次经复核的真实材料。门先验证整套 manifest、复核声明、原件路径/SHA 与金标，再开始任何云请求；合成模式不能开启 live。它复用 `CloudOcrConfig.from_environment()`、`cloud_ocr_pdf_pages()`、`extract_pdf_text_pages()` 与 `extract_jpeg()`，沿用生产配置校验、私有 key 文件、页数/超时限制与响应验证。配置方式见现役部署 OCR 配置说明，runner 不接收命令行 key，不把 endpoint 原文或 key 写入证据。JPEG 的本地 FIFO 不受支持，冲突配置仍会未测。

live 运行关闭进程内 OCR 结果缓存，逐请求记录 provider、请求的 model、dialect、prompt SHA、endpoint SHA、请求/响应 SHA、渲染图片 SHA；JPEG 另保存完整处理身份，PDF 保存逐页 OCR 状态和渲染实现 SHA。`ocr_live=EXECUTED` 表示真实路由完成调用且提取响应有效；金标仍可能不通过。已尝试请求而网络或响应失败记录 `FAILED`；缺配置、未发送请求记录 `NOT_TESTED`。这些状态不等于人工接受，`human_acceptance` 始终为 `NOT_TESTED`。工具不能识别供应商在同一 model ID 后静默更换权重。

本次自动测试仅用传输替身验证 live 开关、实际渲染/解析与失败边界，未调用收费模型，不能把测试中的 `EXECUTED` 当真实模型验收证据。不得用人工文本、录制响应或合成替身填充正式 live 结果。

## 比较和证据

默认阈值是全部逐块精确通过，仅折叠空白，不修正符号、标点、大小写、小数或单位。检查数字、单位、全文、遗漏/新增 token、格式定位和顺序；即使相同数字在两页间互换，逐块数字比较仍失败。原生提取器报告覆盖缺口或块数不足同样失败。空集、空金标、重复样本 ID、缺格式、损坏文件、路径越界、原件 SHA 不符都不能通过。

JSON 包含 scope、状态、原件/金标/manifest/实现 SHA、逐样本提取身份与覆盖债务、逐块定位与正文 hash、检查维度和原始计数。它不输出客户全文；输出文件默认 0600。包含实际失败原因的证据需与输入目录一起保留，复核时从 manifest 查看人工预期。主验收入口另负责整个候选工作树指纹、运行日志与执行环境。

本门的结果仅适用于列出的材料提取范围；仅有真实 live 运行的金标结果才能证明本次所列材料的 OCR 提取质量；它不证明报告论述正确、QA 正确、其他材料的识别准确率、人工业务接受或部署成功。
