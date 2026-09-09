# 材料全链与稳定性执行清单

更新：2026-09-09。用户本轮明确优先B0、B2、B4；原一期R01–R22范围保留，B1/B3其余业务扩展暂后排。范围以PHASE1_GO_LIVE_PLAN.md为准，结果统一记入PHASE1_GO_LIVE_PROGRESS.md。本清单负责工作项、依赖和验收，不替代业务决策台账。

## B0 当前起点

工作树：`codex/phase1-go-live-20260908`，HEAD=`60f321745a4c3b3297ba96bb8077264bcbdbed07`，实现未提交；当前单一迁移head=`f1_0044 / 55`（55为受保护表子集）。起点0034及270项离线/157项前端/119项实库保留在仓外baseline.json。9月9日最终冻结验收：368项离线、198项前端、197项实库、1个多阶段新环境恢复场景、29项浏览器全链通过；四个工程门及真实quality未测门的源码指纹一致，运行期间不变。总回执：[current_priority_result.json](../../../out/phase1_material_stability_2026-09-09/current_priority_result.json)。以下表格为当前状态；后文逐阶段记录保留当时的限制，不覆盖最终结果。

| 原问题 | 当前实现/证据入口 | 本轮处理 |
|---|---|---|
| A01/A02/A05/A06/A07 客户上下文、分页、交错动作 | src/web/scripts/frontend-context.test.cjs的对应A编号实际TSX回归；157项前端总回执 | 保留定向通过结论，变更相关组件时回归；当前材料全链浏览器验收另补 |
| A03/A04 混合页与跨页图片 | material_intake/pdf_renderer.py、ocr.py、cloud_ocr.py；test_material_pdf_renderer、test_material_layout_regressions、test_material_native_visibility | 已有整页渲染与可见性回归；真实OCR质量交给P03/P08，旧原生页眉样本继续保留 |
| A08 OCR截断 | test_material_cloud_ocr及cloud_ocr_transport.py | 保留截断拒绝回归；JPEG接入时共用完整性判定 |
| A09/A14 引用错配/非法编号 | analysis_reports/llm_generator.py及test_analysis_report_llm_generator | 现有PDF回归不替代新格式定位；P06必须覆盖HTML/PDF和正文引用 |
| A10–A13 并发/锁/转换审计 | f1_0027、test_analysis_report_transition_postgres及119项实库回执 | 保留当前事务边界；新证据/人工修订不得改变冻结版本或审计来源 |
| E01 测试栈误清理 | analysis_report_postgres_integration.py、test_analysis_report_harness_isolation/parallel | 已有精确项目清理与并行实测；新测试资源复用这套边界 |
| E02 部署迁移目标失配 | analysis-reports/migrate.py、DEPLOYMENT.md及deployment_preflight测试 | 当前0044合同及新环境恢复已通过；每次新迁移同步专属入口，默认0014/material0016不变 |
| E03 测试门失配 | acceptance_gate.py、unittest_evidence_runner.py及上述两份回执 | 已有实际逐例统计；扩展套件必须纳入统一门 |
| E04 未接线验收与CI | acceptance_gate.py五个mode均有runner；.github/workflows/acceptance.yml接offline/integration/restore/browser | 四个工程mode本地通过，quality缺真实输入NOT_TESTED；S07远端CI尚未运行 |
| E05 队列失败不可见 | analysis_reports/service.py:job_status已投影delivery state/attempt/reason；ReportWorkbenchPage显示自动重试/暂停 | S01定向通过，当前浏览器门含真实Redis故障/后台重启；S08只读监控在故障时实际ALERT |

以上是原问题到实现/回归入口的映射，不能据此宣称最终发布已验收。新增故障按同样规则记录。

| 需求 | 当前边界 | 后续工作 |
|---|---|---|
| R04/R05/R07 | 四格式上传、版本、解析、修订、检索、原件与报告引用已接通并通过合成浏览器全链 | P01–P07工程验收通过；P08真实材料质量仍待输入 |
| R13/R14 | 四格式定位/revision/片段/正文SHA已冻结；人工修订或新版本不改旧报告 | P06工程通过；正式模板与专业业务验收另列B3/B5 |
| R20 | 数据库投递、受限历史补投/恢复、对象对账修复、独立后台权限、OCR缓存已接通 | S01–S05本地定向/整栈通过；目标环境需重验 |
| R21/R22 | 统一门五个mode已接；当前0044新环境恢复与浏览器门本地通过 | S07远端CI；P08真实质量；S08目标部署/告警/维护交接待完成 |
| R01–R03/R06/R08–R12/R15–R19 | 有不同程度代码基础，完整业务验收未完成 | 保留B1/B3；仅处理本次材料/权限/报告接入所必需部分，不顺带宣称其完成 |

## B2 材料全链

| ID/状态 | 入口与动作 | 依赖 | 完成条件 |
|---|---|---|---|
| P01 定向通过 | evidence/docx_native.py按已研究方案支持常规附属包；校验内容类型/关系、有效样式，保留原包 | 0034基础、DOCX常规包兼容方案 | ordinary_generated.docx原件完整保留，5块文字/表格位置正确；隐藏、修订、字段、未知语义仍阻断可用片段；独立正反包通过 |
| P02 解析/持久化定向通过 | 新增XLSX提取与证据：sheet关系/名称、单元格类型、合并、日期/数字、公式与缓存完整性 | v2定位、原件预算；格式语义先核对一手文档 | 不把公式缓存当已重新计算结果；无法判定标partial；多sheet/隐藏内容/外部链接/预算等独立样例通过 |
| P03 解析/预览/持久化定向通过 | JPEG受控解码、方向修正、整图OCR、像素hash和ImageLocator | 既有OCR契约和可用后端 | 显示图与OCR输入同源；截断/超预算/方向异常不假成功；保留原件和处理身份 |
| P04 定向通过 | 扩展native任务/worker/envelopes/存储/API，接入多格式状态与受限证据读取 | P01–P03合同稳定；新迁移遵守线性head | 源变化/过期租约/停用成员不能提交；partial无可用片段；API不泄露密钥或跨客户正文 |
| P05 修订与检索应用TARGETED_TEST_PASSED | 人工字段与文本修订：独立revision、来源绑定、确认/撤销、索引失效处理 | P04和现有分析revision | 原始提取保留；并发修改冲突可见；新检索读新revision，冻结报告仍读旧证据 |
| P06 TARGETED_TEST_PASSED / 合成浏览器全链通过 | material_rag、material_pipeline及analysis_reports接v2检索、冻结和引用；前端显示各格式定位 | P04/P05；旧PDF兼容 | 精确编号/自然语言能命中；引用回到正确版本/段落/表格/单元格/图像；正文与引用列表及导出一致；无依据可拒答 |
| P07 TARGETED_TEST_PASSED / 合成浏览器全链通过 | MaterialPanel与DocumentUploadModal增加四格式/批量、处理详情、人工复核和引用查看 | P04–P06接口逐步稳定 | 单份、批量部分失败、新版本、未知回包重试、刷新及客户切换都可接续；按实际后端能力开放格式 |
| P08 合成浏览器29项通过；真实质量NOT_TESTED | 四格式HTTP→扫描→worker→存储→修订→检索→报告浏览器验证及独立质量样本 | P01–P07；真实质量依赖客户材料和复核人 | 合成端到端门先完成；真实数字/单位/遗漏/引用与修改耗时单独统计，不以合成结果充当真实验收 |

## B4 稳定性与发布工程

| ID/状态 | 入口与动作 | 依赖 | 完成条件 |
|---|---|---|---|
| S01 定向通过 | job_status/repository、wire及ReportWorkbenchPage展示投递状态、尝试次数、固定失败原因 | 已有受限列权限；不返回actor/token | 实际retry_wait/blocked在UI可区分，终态优先，旧客户回包无影响；保持原任务重试身份 |
| S02 TARGETED_TEST_PASSED / Linux实际整栈通过 | 审计worker/dispatcher实际DB和存储权限，拆分最小能力并验证部署角色 | P04任务与源身份合同 | 后台不得跨租户读源/写结果或越权发布；真实DB和对象权限负向操作被拒绝；所需正常任务可完成 |
| S03 运维入口/实库/对象/SIGKILL重放TARGETED_TEST_PASSED | 对象与DB对账、失败补偿、迟到成功保护、可重放恢复任务 | S02，现有p3释放/删除/投递合同 | 缺对象/孤立对象/半写可检测并按策略修复；并发完成时不误删；先只读计划，再受限修复；不碰共享测试资源 |
| S04 TARGETED_TEST_PASSED / 整栈OCR复用通过；真实质量待输入 | PDF分析/索引及JPEG复用可信OCR checkpoint，校验source/parser/render身份与覆盖 | P03/P04、现有0028/0029契约 | 相同已确认输入复用，源/渲染/解析版本变化失效；不会用旧错误结果生成新报告；真实调用计数佐证 |
| S05 TARGETED_TEST_PASSED / 实际Redis中断与worker重启恢复通过 | native历史缺任务补投、blocked受限重绑、租约/重启恢复 | P04/S02；保持完成revision不可变 | 缺任务只补一次；旧actor/token不能提交；未知提交结果不覆盖已完成数据；重复投递无重复片段 |
| S06 新环境恢复/独立读取TARGETED_TEST_PASSED | 当前analysis-report候选备份/恢复：DB、MinIO、密钥、迁移和重建；独立新环境演练 | P04/P06/S02/S05稳定 | 恢复后原件hash、密文解密、revision、检索与冻结报告一致；缺密钥/损坏备份明确拒绝；旧0015/0016恢复工具不冒充0034+证明 |
| S07 本地统一门通过；远端CI NOT_RUN | acceptance_gate接browser/restore/quality真实runner，扩CI并上传逐例证据 | 相应runner成熟即接线 | 缺依赖/输入不返回PASS；同候选完整计数与日志；CI实际执行并取回结果，配置存在不算通过 |
| S08 本地只读监控/故障告警通过；目标环境与通知NOT_TESTED | 队列/失败/对象异常监控，目标环境预检、安装升级、恢复告警说明与维护交接 | S01–S07；实际环境、预算、维护人输入 | 本地隔离工程先验；目标环境版本与配置核对、告警真实触达及恢复耗时另验；正式部署状态单独记录 |

执行顺序：B0核对与S01定向验证已完成→P01→P02/P03→P04→P05/P06→P07/P08；S02–S05随相关任务合同稳定穿插，S06/S07完成后进行最终一致候选验收，S08实际部署依赖环境输入。遇到影响数据正确性的阻断先修，并及时更新顺序。通用工程无需重新等待客户授权；真实数据/费用/生产配置按已经明确的授权范围处理。

S01当前结果：真实报告实库28项、前端161项、build/lint通过。接口新增可空delivery投影，仅返回state/attempt/reason_code，保留job自身状态；页面区分排队、等待自动重试、暂停与生成中，终态按原任务逻辑处理。未新增数据库授权，暂不暴露下次重试时间。首轮阻断测试错误地从queued直接结束为blocked，数据库正确拒绝；改用真实worker生成失败再阻断的路径后通过，原失败日志保留。回执：仓外out/phase1_material_stability_2026-09-09/report_delivery_result.json。真实浏览器队列故障留待统一入口验证。

P01当前结果：保留原始17条目DOCX（SHA256=9cfcbfedfbc4eb70ba4160064a437e5cd11ed14456509b1f16253d754ee29df2），普通正文和2×2表格5/5块完整提取。新增关系来源/入出边、内容类型/根身份、双样式有效闭包比较；有限web输出选项、无有效入口的编号、空bibliography与独立JPEG缩略图兼容。有效颜色/隐藏/编号/未知语义仍partial且零可用片段；manifest绑定附属判断和原件hash。19项独立常规包兼容回归纳入统一门，当前289项离线、161项前端、build/lint/material-automation通过且源码稳定；23项原生实库含普通原件worker→加密保存→解密定位及冲突包零片段通过，隔离清理通过。回执：out/phase1_material_stability_2026-09-09/docx_compatibility_result.json。对象存储端口在实库测试中替换，尚不等于四格式真实浏览器全链；不证明Word渲染与分页。

P02解析结果：新增XLSX原件有界OPC读取、sheet ID/关系/名称绑定、单元格/合并定位、共享/内联富文本、固定数值/百分比/单位与ISO日期规范化。19项独立回归及DOCX/XLSX相关84项通过；原始双sheet合成文件15/15单元格完整。任一公式缓存、隐藏内容、未知显示/部件语义使整份partial、零可用片段。没有执行公式；XLSX尚未接后台释放/任务/存储/检索/报告，后续P04–P08仍需完成。回执：out/phase1_material_stability_2026-09-09/xlsx_parser_result.json。

P03解析与预览结果：JPEG独立进程完整解码、8种EXIF方向校正、有效ICC到sRGB转换、尺寸/像素/耗时上限及处理身份；新预览与OCR发送相同JPEG，修复原预览先删方向元数据导致像素方向失配。12项图片及原P3共40项通过；四格式解析纳入统一离线门320项、前端161项及build/lint/material-automation通过，源码稳定。OCR HTTP使用替身，未验证真实识别准确率；既有local FIFO只支持PDF，其启用时JPEG不会静默改走云端。原生任务与后续检索报告等待P04–P08。回执：out/phase1_material_stability_2026-09-09/jpeg_parser_result.json。

P04持久化结果：新增线性0035/52；保留0034旧DOCX事实与AAD，XLSX/JPEG按MIME/原件key/大小与parser-profile闭集登记。Unicode canonical编码与Python一致（含中文、emoji、转义和嵌套）；严格单元格/图像定位和图片处理身份验证。不可变修订/加密片段、原件锁/成员锁/实时租约继续生效，JPEG临时OCR失败不保存终态空修订，重试同revision换token。API新增分页原生片段读取，逐片解密校验并保持旧版本可查看；API/worker无原生密文表通读权。32项原生实库、320项离线、163项前端/build/lint/material-automation通过；具体回执native_formats_persistence_result.json。原件存储端口与OCR HTTP为替身，P05/P06和真实质量/浏览器全链仍待后续。

P05修订结果：新增0036/54，保留PDF v1与native v2原提取，以独立review AAD保存全部文本位置和15类可选字段；字段值不直接改正式政策记录。确认/撤销追加不可变revision；预期head校验、source/actor锁及请求semantic SHA保证冲突可见与未知回包重试。PDF新索引进入处理中后旧成功请求仍可找回确认回执。界面分页并排原始/修订，字段选择来源、勾选完整核对、未知回包锁定内容与同请求重试、跨版本回包隔离。39项原生实库、325项离线、169项前端及build/lint/material-automation通过；完整统一门读取package声明，修复双测试清单漏纳入新用例。PDF为历史v1 fixture，storage/OCR为替身；实际HTTP/浏览器全链未验证。partial材料仍不开放全文手工转录；确认和撤销应用到新检索/报告及旧冻结引用由P06完成。回执：material_reviews_result.json。

P06当前进展：0037/54受限有效证据读取已接统一QA和报告。人工确认覆盖原提取，撤销不回退；新QA和旧请求重放重新核对当前片段，旧报告保留冻结引用。DOCX/Excel/图片不生成假页码，报告保存locator/revision/fragment/body SHA；旧PDF v1身份和旧报告兼容。完整语料上限20000片段/24MB，不先截256片段；第301片段可命中。328项离线、173项前端/build/lint、43项原生实库和28项旧报告实库通过。实库包含四格式生成引用、人工修订撤销后旧报告不变、QA重放失效；PDF基底为历史fixture，原件storage和OCR为替身，不等于上传HTTP/浏览器或真实OCR验收。HTML/PDF四格式位置文字提取已比对，2页PDF实际渲染已查看；修复中文两端对齐拉开空格。声明式citation schema另验四格式与旧PDF通过、三类伪页码拒绝。处理状态、完整引用查看、P07/P08及S02–S08仍待推进。回执：effective_evidence_result.json。

P06补充：有效状态读数已接原生状态与material_pipeline，区分原提取/人工修订/撤销/不完整；native释放、人工修订提交同事务登记报告续接投递，demo/UAT的ingestion-worker和report-worker补齐native开关。原件查看按存储SHA/大小读取，PDF渲染真实页、DOCX/XLSX重新定位原始块/单元格、JPEG核验原渲染身份；支持原件下载。SQL受限入口支持冻结报告和当前QA片段，拒绝错误版本/片段/修订/租户；客户发布前及撤回后读不到原件，撤销人工修订不破坏旧报告原件引用。329项离线、179项前端、74项原生与旧报告实库通过；源稳定。新原件路由和实际Compose尚待P08浏览器/HTTP全链，S02–S08继续。回执：p06_status_original_result.json。

S05定向结果：0038/54新增显式管理员恢复和audit不可变请求回执。历史无任务可按共享/客户域分页发现并单次登记；仅blocked可重绑当前管理员，保留源/修订身份，running/done不重置。未知响应重放同回执，过期token不提交；并发恢复只有一次rearm；后续worker阻断不能被旧请求重放再次清零重试预算。恢复与报告续接同事务，注入续接失败会回滚任务和回执。前端开始/恢复按钮保留请求并隔离迟到响应。50项原生实库、28项旧报告实库、329项离线/182项前端及build/lint通过。真实重启/Redis丢失将在统一全链验证，S02凭据权限审计继续。回执native_recovery_result.json。

P07定向结果（2026-09-09）：新增每批20份的四格式上传，按文件顺序处理并分别显示已接收/未确认/格式或大小不符；重试跳过成功项，同文件/名称/来源与用户保存请求摘要，刷新后重选可接续未知上传。单文件和新版本共用请求保存与有效回执校验，隔离客户/身份切换后的旧响应。0039/54受限原件入口用于复核窗口，当前管理员可在确认/撤销后核对原始base片段；错误片段/revision/来源版本和停用成员拒绝，客户QA权限不扩大。51项原生实库、329项离线/193项前端及build/lint通过。首轮实库测试误尝试回退文档版本，数据库正确拒绝，已改用另一有效管理员继续验证；首轮新增前端夹具的引用比较与取消请求索引错误已修正，失败日志保留。真实浏览器上传/刷新/扫描/处理全链交P08，存储和OCR仍有替身；回执p07_batch_review_result.json。

S02存储凭据阶段（2026-09-09）：当前专属demo/UAT在初始化时写入独立API/摄取/worker存储凭据并移除运行卷中的MinIO root文件；一次性provisioner创建三个业务桶及服务账号，默认0014配置不变。API/摄取仅业务桶读写，worker仅已释放/隔离源桶读取，无列对象/写入/删除/管理权限。独立真实MinIO五项通过，包含实际storage释放复制与读取、拒绝写/删/用户管理/桶策略/无关桶读取、服务凭据缺失不退回root、真实初始化容器和Compose合并；专属资源已清理，共享指纹不变。329项离线/193项前端/build/lint通过。此阶段仍不能证明按任务隔离源读取，报告/摄取仍共用API数据库身份，完整候选服务尚未重建运行；S02保持进行中，下一步增加租约限定的源读取及拆分数据库身份。回执storage_service_credentials_result.json。

S02任务源读取阶段（2026-09-09）：0040/54新增独立f1_source_reader登录和只读definer，只有任务类型/ID/租约可换取源；无表读写、claim、API/worker角色切换权。网关持来源/job/原生actor锁，核验当前版本、租约、SHA/大小，读取结束再次检查租约时钟。专属worker卷移除全部MinIO凭据，API/摄取配置保持独立桶权限；PDF索引和原生提取走内部网关。真实PG+MinIO59项通过，另一次真实socket HTTP→MinIO→native worker→加密SQL完成通过，错误身份/旧token/被篡改源拒绝；清理成功且共享指纹不变。统一离线329项/前端193项/build/lint通过。首轮51项仅旧head断言失败已修，保留日志。实际整栈尚未重建，PDF网关只验SQL身份；报告/摄取仍共用API数据库角色，摄取仍持业务桶读写权，S02继续。回执task_source_gateway_result.json。

S02报告生成角色阶段（2026-09-09）：0041/54新增f1_report_worker，仅允许五个投递/租约函数，无表读写、API/worker角色切换、发布或成员管理权；DB派生租户/客户/actor并在收尾重算完整有效证据指纹、持来源和成员锁。真实Redis/RQ下只持报告密码与材料密钥的独立Python进程完成draft/done，四类旧续接消息成功转投摄取队列（独立RQ ID避免跨队列误去重）。四格式冻结引用、跨客户/伪造页码拒绝、租约过期/成员停用和模型生成期间人工修订的零半写验证通过。统一164项实库通过在最终指纹补充之前；最终61项原生实库（含真实MinIO/Redis）及扩展模型期间修订用例通过，329项离线/193项前端/build/lint通过且源码稳定。全门暴露0037既有definer授权与QA密钥夹具重复创建，已修复；历史0026→0041升级保留全部旧业务行且新增引用身份列为NULL，迁移重放通过。保留失败回执。实际Compose整栈未重建，Linux容器worker未验；材料协调仍在持API数据库身份和业务桶凭据的摄取worker，S02继续。回执report_worker_role_result.json。

S02摄取后续交接补充（2026-09-09）：DOCX/XLSX/JPEG预览ready与稳定pipeline投递同事务登记；失败注入保留previewing/原处理token、零ready审计、零投递，成功后仅DB dispatcher即可找到任务。PDF继续等分析成功登记，避免OCR临时失败使过早投递终态阻断。新增仅内部网络使用的API协调入口，严格有界JSON只收delivery ID/token、DB派生租户/actor/version；错误/过期身份或额外业务参数404空body/no-store，不进入OpenAPI。摄取侧投递通过固定API服务转发，三类无租约旧nudge由持久投递替代；API本身执行原有幂等释放/索引/报告调度，OCR/提取/生成仍在worker。真实socket与无API密码的独立Python进程验证通过，63项原生实库、3项PDF布局实库、329项离线/193项前端及lint/build通过；Compose/真实MinIO五项通过。失败测试夹具的状态闭集、目录属性及reason断言已修，日志保留。此处只证明协调请求入口的capability，不是新的数据库逐事务协调租约fence；摄取解析仍持API和业务桶凭据，实际Compose整栈/浏览器全链未跑，S02继续。回执pipeline_control_handoff_result.json。

S02摄取数据库角色（2026-09-09）：0042/54新增f1_ingestion_worker与受限上下文/有限收尾definer；运行卷移除API/通用worker密码。SQL直接读写只覆盖当前有效投递的源、分析、页/候选、checkpoint及相关审计；禁止修改源身份、释放、确认、发布或成员管理。来源→投递→当前成员锁及实时派发/处理租约限制每条写入，运行进程提交前再次校验；旧分析不得追加页/字段或伪造新建审计。DB独立核对upload ready及PDF当前完整分析后才接受done/后续交接；原生ready与交接同事务，失败零半写，原件变化拒绝旧预览，过期处理换token后可恢复。真实Redis/RQ独立进程仅持摄取密码/材料密钥完成done与pending交接；存储读取、扫描器和预览存储为替身，预览/解析与PG真实。统一172项实库（含历史0026→0042保留与幂等重放）、329项离线/193项前端及lint/build/material-automation通过，源码稳定；独占资源清理且共享指纹不变。新增guard SQL闭括号错误曾导致迁移失败，已修复并保留失败日志。摄取仍持业务桶服务凭据，任务级源读取/不可变预览网关继续；API协调仍为入口cap校验+原有幂等事务，未新增逐事务租约fence；实际Compose/Linux worker、浏览器全链、OCR质量及当前head恢复仍待。回执ingestion_worker_role_result.json。

S02摄取对象权限（2026-09-09）：当前head0043/54。新增按投递和处理token读取原件/写预览的内部网关，数据库锁住来源、投递和当前成员，I/O后重验租约；客户端不能指定租户、对象key或跨任务前缀。摄取运行卷无MinIO/API密码；网关仅读源及读写预览，无列对象、删除、源写入或管理权，真实20 MiB分片上传通过。预览unit/manifest按内容hash寻址；过期PUT留下的旧对象不能覆盖新预览，旧manifest只在新地址实际缺失时回退且仍核验SHA。四格式真实HTTP/MinIO/解析/PG进入done并登记pending交接；独立Python进程仅持摄取DB密码/材料密钥同样完成，扫描器仍为替身。伪造参数、跨任务ID、错误内容SHA、旧/过期token、成员停用、写期间源/成员锁及迟到PUT验证通过。独立进程暴露此前服务密钥调用缺少file_env与provisioner缺少Path导入，已修复并用真实0600文件和实际初始化入口复验；原失败日志保留。统一176项实库、329项离线、193项前端及lint/build/material-automation通过且源码稳定，独占资源清理/共享指纹不变。实际Compose整栈/Linux worker、真实扫描浏览器全链及当前head恢复仍待P08/S06/S07；API协调仍是入口cap核验+原有幂等事务。回执ingestion_storage_gateway_result.json。

S03对象对账与迟到结果（2026-09-09）：修复实库复现的两条上传竞态：迟到失败会覆盖ready/held，迟到成功会重置scanning；相同原件的重复回包现在保留当前进度、token与审计。新增一次性运维对账入口和操作说明OBJECT_RECONCILE.md，先生成绑定集群/DB/head/存储身份的只读计划，再按当前源/任务/对象快照及引用重新核验。缺失源副本可从完整副本恢复；预览只在重新生成SHA与保存值完全相符时补写；原件已入桶但数据库未登记可由当前管理员完成同事务审计/持久投递。超过24小时的孤立对象在重新核对引用、锁和SHA/ETag/修改时间后清理；活动/未知引用/近期对象保留，损坏或两份都缺失明确inspect，不替代业务释放批准。操作前fsync动作日志，真实CLI在MinIO接受PUT后SIGKILL，重放识别ALREADY_REPAIRED；成员停用、登记后异常回滚、新引用、变更对象/任务、伪造跨前缀计划均有实库/真实MinIO验证。保留期使用可控时钟测试，不冒称等待24小时。统一185项实库、329项离线、193项前端与lint/build/material-automation通过，源码稳定；独占资源清理且共享指纹不变。此为运维命令定向证明，未运行共享/远端候选；实际Compose、ClamAV/浏览器全链和当前head恢复继续P08/S06/S07。回执object_reconcile_result.json。

S04 OCR复用（2026-09-09）：当前head0044/55。新增独立FORCE-RLS加密结果缓存，仅经当前任务/源/成员/租约函数读写；运行角色不能直接读表且不新增密码。PDF分析与本地索引、JPEG原生提取已接入，源/版本/完整请求、模型、提示词、渲染与代码/配置/epoch共同限定复用；旧backend-only checkpoint不迁入或读取，失败/空/不足覆盖不缓存。真实PG/HTTP/MinIO与实际渲染下，PDF分析→索引只调用合成模型一次；JPEG在OCR后注入失败、换租约重试仍只调用一次并保存可解密完整片段。0043已收口owner→0044升级及重放、密文篡改/跨身份/旧token/成员停用拒绝、写入后租约过期回滚、源变化拒绝及处理配置变化失效通过。FIFO只验证固定bundle身份与缓存接线，响应调用为替身。统一192项实库、329项离线、193项前端与lint/build/material-automation通过且源码稳定，独占资源清理/共享指纹不变。每页最多8个输入指纹，超预算继续OCR但不缓存；供应商同模型ID静默变更须递增epoch，不冒称自动可识别；已确认/冻结内容不改写。实际Compose/Linux、ClamAV/浏览器全链及当前head恢复仍待P08/S06/S07。说明OCR_CACHE.md，回执ocr_cache_result.json。

S06当前候选恢复（2026-09-09）：0044候选新增 backup/verify/plan/restore/check-runtime 运维命令及 CURRENT_HEAD_RECOVERY.md。真实CLI备份后移除源PG和MinIO，在全新PG/MinIO恢复；核对全部业务行和目录/ACL/角色/扩展owner、三个桶全部对象SHA及非等长multipart ETag，独立受限API进程用恢复密钥解密四格式、检索定位、重放QA和冻结HTML/PDF。缺/错密钥、损坏或不受信manifest、非空目标拒绝；注入SQL恢复失败整体回滚且零对象写入。未来租约阻止启动，实际时钟到期后可启动并以新token复用OCR缓存；未改写旧任务或冻结报告。统一restore 1个多阶段实际场景、192项集成、330项离线、193项前端与lint/build/material-automation通过，三门源码指纹一致且稳定。55仅为受保护表子集，不冒称全库表数。测试使用合成OCR和历史PDF基底，恢复SIGKILL/MinIO写失败未验；实际Compose/Linux/ClamAV/浏览器全链继续P08/S07。CI已接restore，远端未运行，browser/quality仍待接线；未提交/推送/部署。回执out/phase1_material_stability_2026-09-09/current_head_restore_result.json。

P08/S07/S08进行中（2026-09-09）：browser、restore与真实quality runner已接统一入口及CI配置。隔离Linux整栈包含实际ClamAV、PostgreSQL、MinIO、Redis、受限worker与独立Chrome；四格式由真实界面上传，PDF/JPEG经固定渲染SHA绑定的合成TLS OCR应答，不能作为识别质量证明。probe10的19项正常旅程、共享方法真实上传及报告五类来源引用通过，实际PDF/JPEG各一次OCR HTTP调用，后台卷无API或对象密码，资源清理且共享指纹不变。probe11新增版本/当前检索/旧冻结引用与原件保持、partial零片段与问答排除通过，最后报告错误提示失败，24/25通过；probe12确认真实Redis中断后4个相同上传delivery进入retry_wait，监控数据库/服务/readiness均ALERT，但worker重启失败。两处实际缺陷已修：摄取worker沿用hostname导致RQ旧注册冲突，现每个进程用独立身份；transport与adapter各有一套ApiError，导致索引专用提示及retryable分支失配，现复用同一错误类。前者真实Redis留存旧注册及受限摄取进程通过，后者真实HTTP→transport→页面5项新增回归通过，前端198项/lint/build通过；完整故障链仍待当前候选重跑，不把这些失败记作已全链通过。

S08独立运维验证：candidate_ops_check.py按明确候选容器/image、数据库cluster/head、迁移源码和显式阈值只读观测；近窗口失败与历史累计区分，FORCE RLS可见性与数据库拒绝写入均实际验证。12项离线、5项真实PG通过；probe12正常服务/DB/readiness通过，中断三项ALERT。未提供对象计划的该运行器objects为NOT_TESTED，不宣称对象已重新扫描；对象修复/恢复沿用S03/S06独立回执。说明CANDIDATE_OPERATIONS.md；没有创建定时任务、发送通知或操作目标服务器。真实quality无授权输入时NOT_TESTED，合成12样本的4正常/8指定拒绝仅为工程基准。新增版本、混合批次/未知回执刷新重试与全门统一指纹继续收口；远端CI/真实材料质量/人工接受/目标部署仍未完成。


## 2026-09-09 当前优先批次最终结果

冻结候选工作树SHA为`541490c1ad3be5c073a94f14a534d80086578ce21d48f269a2a0982b16907f44`，HEAD不变。最终回执在`out/phase1_material_stability_2026-09-09/final_frozen/`：offline 368项、frontend 198项与lint/build/material-automation、integration 197项、restore 1个多阶段场景、browser 29项全部PASSED且SOURCE_STABLE。quality真实模式无输入返回NOT_TESTED（0样本）；它不是通过项。结果写入后只更新状态/运维文档；总回执另核对文档差异，不能把新完整文档指纹冒称测试时指纹。

实际Linux Compose包含ClamAV/PG/MinIO/Redis/OIDC和三个受限后台。四格式真实UI上传→扫描→提取/人工修订→原件查看/QA→四格式及共享方法引用报告通过。DOCX新版本进入当前检索，旧冻结报告及原件引用保持；公式未决XLSX显示不完整、零可用片段、排除QA并阻止新报告。混合无效文件不影响有效文件；真实上传202后对浏览器丢失回包，刷新重选并重试返回同文档/版本/request ID，数据库仅一份文档与版本。关闭页签后的接续不在本轮范围。

真实Redis停机时4个相同摄取delivery进入retry_wait/attempt1；SIGKILL三个worker并恢复Redis/worker后，原4个delivery均done/attempt2，未手改状态或时钟。修复了摄取worker重复hostname注册阻止重启的问题；当前每个进程用新RQ身份。修复transport与adapter错误类不一致导致的专用提示/重试判定失效。浏览器实际PDF/JPEG OCR HTTP各1次，响应为合成且绑定渲染SHA，不能证明识别准确率。三个worker运行卷均无API数据库密码和对象存储凭据。专属容器/卷/网络归零，临时控制目录及本次镜像标签已移除，共享指纹保持。

0044备份在移除源PG/MinIO后恢复到新环境；实际独立受限API进程解密读取四格式、检索与QA重放、冻结HTML/PDF通过。实库集成包含权限拒绝、并发/旧token拒绝、对象修复及SIGKILL后重放、OCR身份/缓存失效、历史升级保留。浏览器故障范围是已入库上传队列及进程重启；不声称所有I/O时点断电或目标服务器恢复已验。

S08正常时十二服务、数据库与readyz通过，Redis中断时三者均ALERT；该次未提供对象计划，objects及正常总状态保持NOT_TESTED。对象对账的实际修复证明见S03，未冒称本次监控重新扫描。没有发送外部通知或创建周期任务。

| 剩余项 | 入口 | 依赖 | 验收条件 |
|---|---|---|---|
| P08真实材料质量 | scripts/MATERIAL_QUALITY_GATE.md；acceptance_gate.py --mode quality | 允许处理的四格式原件、独立人工金标、实际模型配置/处理授权、复核人 | 真实提取逐块比对数字/单位/遗漏/位置；记录实际模型调用，专业内容与人工接受分别验收 |
| S07远端CI | .github/workflows/acceptance.yml | 当前可审查候选的commit/push明确授权与可用远端仓库 | 同一提交的offline/integration/restore/browser四个job实际运行并保存逐例日志；无checks不算通过 |
| S08目标运行 | CANDIDATE_OPERATIONS.md、CURRENT_HEAD_RECOVERY.md、OBJECT_RECONCILE.md | 实际环境/账号、负责人、预算、阈值/通知渠道、备份保留与RPO/RTO（唯一决策台账Q07/Q08/Q11/Q12） | 目标预检与迁移/恢复、告警真实送达、运维交接及观察完成 |
| 完整一期其余范围 | PHASE1_GO_LIVE_PLAN.md B1/B3/B5–B7 | 原计划业务场景/职责/模板及验收输入 | 按R01–R22逐项验收；本批不关闭全部一期目标 |

本批状态为TARGETED_TEST_PASSED / NOT_RELEASE_VERIFIED；NOT_COMMITTED / NOT_PUSHED / NOT_DEPLOYED。首轮final_acceptance的SOURCE_CHANGED及5项加载失败、此前probe失败日志均保留；最终通过不改写失败事实。
