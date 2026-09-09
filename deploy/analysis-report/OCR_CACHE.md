# OCR 结果复用

0044 在 analysis-report 专属栈增加 `material_ocr_result_cache`（FORCE RLS）。默认 0014 和 material-RAG 0016 不启用。demo/UAT 的 `F1_OCR_RESULT_CACHE=1` 接通 PDF 分析、PDF 本地索引和 JPEG 原生提取；成功分析不清理此表。旧 backend-only checkpoint 保留原历史语义，新模式不读取或迁入它们，成功分析后的旧 checkpoint 清理仍保持。

缓存只跳过成功的 OCR 调用。PDF 云端仍先重新渲染整页，并核对源 SHA、页号、实际模型请求 SHA、模型/provider/dialect/endpoint、解析/渲染代码身份及请求预算；JPEG 还绑定规范图像/pixel SHA、orientation 渲染身份。FIFO 使用源/页几何、固定 F0-H bundle 的模型/配置/执行 SHA 及客户端代码身份；本地 FIFO 不转远端。失败、空结果、不完整结束和 PDF 不足40字的结果不入缓存。相同字节的不同材料版本不共享。

`F1_OCR_CACHE_EPOCH` 默认为 `1`，同一候选的摄取与索引 worker 必须使用相同值。显式模型 ID 不能检测供应商在同一 ID 下的静默升级；运营方获知此类变化、确认历史识别错误或需要强制重算时递增 epoch 并重启相关 worker。新 epoch 只影响今后的 OCR 调用，不改写已确认分析、人工修订、索引或冻结报告；已有结果仍通过原人工复核/显式重建流程更新。

客户端仅向固定 source-gateway 内部 HTTP 地址提交任务类型/ID/token、版本/SHA、页号/输入指纹与加密 envelope。DB 从当前任务派生租户和源，复用已有源/job/成员锁、版本和实时租约检查。源网关不持材料解密密钥；正文采用材料密钥 AES-GCM，AAD 含租户、版本、源、页和完整输入/正文 SHA。缓存读写权限在独立 NOLOGIN definer 内，任何运行登录都不能直接读写表。正常 worker 没有新增 DB/MinIO 密码。

同一输入只有首个完整结果可保存，后续并发/重放采用该结果。每版本每页最多8个不同处理指纹，单条 envelope 不超过1 MB；达到上限或有效结果过大时继续执行 OCR，但不缓存该结果。损坏密文、身份不符或缓存服务不可用会明确使本次 OCR 不可用并走现有重试，不回退到未验证结果。

验证入口：`scripts/acceptance_gate.py --mode integration` 包含 `tests.test_ocr_cache_postgres`。测试使用隔离 PG/MinIO、真实内部 HTTP、实际 PDF/JPEG 渲染和合成模型响应计数；不代表供应商识别准确率。FIFO 验证缓存接线、源与 bundle 身份，响应调用为替身，不代表当前 Linux FIFO 整栈验收。新表、材料密钥及角色需纳入当前 head 的备份恢复；旧 head 的恢复证明不能替代它。
