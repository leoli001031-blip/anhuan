# 本机分析报告专属测试环境交接

本目录只描述 **本机可重复启停** 的专属测试栈。
组件级机器门：`VISUAL_IMPLEMENTATION_LOCAL_PASSED / WORKFLOW_UAT_PASSED / TARGETED_TEST_PASSED / DEPLOYMENT_PACKAGE_INTEGRATED_LOCAL`。
整体证据：`BLOCKED(HISTORICAL_OIDC_CALLBACK_CODE_OUTPUT / FORBIDDEN_OR_TRUE_COMMAND_USED)`；不得写 `RELEASE_CANDIDATE_LOCAL_PASSED`。
发布边界：`COMMITTED_LOCAL / NOT_PUSHED / HUMAN_VISUAL_ACCEPTANCE_PENDING / REMOTE_TARGET_PENDING / NOT_DEPLOYED / NOT_PRODUCTION`。
不是共享 `anhuan-f1` 栈，不是远端预发，不是生产。

## 2026-08-24 现役交卷结果

- 部署 preflight 定向门：`Ran 19 / OK / skipped=0`，`TARGETED_TEST_PASSED`。
- 独立新授权侧边浏览器轮已取得 7 页 × 桌面/390px 共 14 张最终截图，全部 `overflowX=false`；三个响应式 P1 与一个审核清单状态真值 P1 已有前后证据。
- workflow UAT exit=0、stderr 为空，canonical 输出满足 `ark_calls=0 / mock_data=0 / dedicated_c=0 / dedicated_v=0 / dedicated_n=0 / shared_match=1 / skipped=0`。
- lint exit=0，保留 19 条既有 warning；build exit=0、`3228 modules transformed`，保留 `>500 kB` chunk warning；`git diff --check` exit=0。
- demo 已停止，专属 containers/volumes/networks 为 `0/0/0`；临时 `src/web/node_modules` symlink 与 `src/web/dist` 均不存在。
- PR #3/#4 元数据未变，仍为 `OPEN / draft / MERGEABLE / statusCheckRollup=[]`；这是无 CI 门禁风险，不是 CI 通过。
- 首轮 runner 的三次失败仍在 `RELEASE_CANDIDATE_BLOCKED.md` 作为历史事故保留，不再表示当前 14 图缺失。整体证据仍因历史 OIDC callback code 输出与本次收尾过程命令违规保持 `BLOCKED`。

## 启停

在仓库根目录：

```bash
export PYTHONPATH="$PWD/src:$PWD"
export F1_KEYCLOAK_ISSUER_URL=http://material-rag.invalid/realms/anhuan

./scripts/localctl analysis-report-demo-start
./scripts/localctl analysis-report-demo-status
./scripts/localctl analysis-report-demo-stop
```

`start` 只打印三行，不含密码：

```
url=http://127.0.0.1:<port>
provider_username=tenant-a
client_username=invitee
```

`status` 在就绪时为闭集 JSON，键仅为：

`ready, f1_head, provider_login_ready, client_login_ready, workflow_seeded, generator, ark_calls, mock_data, shared_match`

其中 `f1_head` 必须精确为 `f1_0018`；默认工程仍是 `f1_0014`，只有分析报告专属 migrator 到 0018。

停止后专属容器/卷/网络为 0，控制目录删除。失败也必须收口，禁止按前缀扫共享栈。

## 两个角色

| 用户名 | 用途 |
| --- | --- |
| `tenant-a` | 服务商运营台：创建报告、生成、提交、批准、发布、撤回 |
| `invitee` | 客户门户：仅已发布版本可见；撤回后列表空、详情「内容不存在」 |

密码在专属控制目录的 0600 secret 文件中，本交接文件不写出口令。
`employee` 保持企业 A / `plant_admin`，fixture 不改其 membership/角色。

## 前端 mock 与后端合成材料（必须分开）

- **前端 mock 关闭**：`VITE_MATERIAL_RAG_REPORT_MOCK` 未启用；页面不得出现「本地合成数据」。浏览器走真实 UI 与 HTTP。
- **后端确定性本地生成器**：`F1_MATERIAL_ANALYSIS_REPORT_LOCAL=1`、`F1_LOCAL_ENGINEERING=1` 且 `F1_EXTERNAL_PIPELINES_ENABLED=false`。fixture 写入的是 **released + clean + preview-ready 的合成材料**，供 JOIN 生成 7 节、引用≥2 的草稿，并形成 `evidence_mode=deterministic_local` 的健康度测试快照。这不是真实客户数据，不是真实模型/Ark 生成，也不是正式评分器。
- `status.generator` 应为 `deterministic_local`，`ark_calls=0`，`mock_data=0`。
- 页面必须把 `deterministic_local` 显著标为测试能力；HTTP 无快照或 503 时只能显示“暂不评分”，不得回退到 60/100 假绿。

## 能力边界

可在本机重复验证：报告创建 → 生成首个版本 → 提交审核 → 勾选清单 → 批准 → 发布 → 客户阅读 → 撤回后对客户不可见。

不可声称：真实 Ark/RAGFlow 生成通过、远端预发授权、生产就绪、人工验收已完成。

浏览器全流程门：`./scripts/localctl analysis-report-workflow-uat-check`
成功时 stdout 仅 canonical JSON + `LOCAL_ANALYSIS_REPORT_WORKFLOW_BROWSER_OK`，stderr 空。

现役结论仅允许：

机器门已经实际运行并满足 exit=0、stderr 空、canonical JSON + `LOCAL_ANALYSIS_REPORT_WORKFLOW_BROWSER_OK`，可记录上方组件级通过。由于两项硬规则过程例外仍在，整体不得记录 `RELEASE_CANDIDATE_LOCAL_PASSED`。Agent 视觉检查不能替代甲方签字，始终保留 `HUMAN_VISUAL_ACCEPTANCE_PENDING`。

## 远端交付包（与本机 demo 分开）

上方启停命令只用于本机 `localctl analysis-report-demo-*`。
本段描述仓库内 `deploy/analysis-report/`：**已本地提交但未 push；尚未授权远端目标，未部署**。

- PR 栈：PR #3 先合；PR #4 改 base 到 `main` 后复核只剩本层；本轮不改 PR、不合并。
- 拓扑：Netlify 静态前端 + 单一 HTTPS edge；`/api`、`/realms`、`/resources` 同源 rewrite，SPA fallback 最后。
- 渲染：`python3 deploy/analysis-report/preflight.py --netlify-origin <HTTPS> --edge-origin <HTTPS> --output <仓外路径>/netlify.toml`
- 操作说明：`DEPLOYMENT.md` / `ROLLBACK.md` / `REMOTE_SMOKE.md`
- 数据库：先建 pre-0018 PG 备份点，再用 `infra/f1/analysis-reports/migrate.py` 前向 `f1_0017→f1_0018`；回退只能恢复备份到新数据库，禁止 downgrade。
- 身份与网络：Keycloak issuer/redirect/web origin、CORS、DNS、TLS、edge 路由与双身份 smoke 均在部署命令单中闭合；Bearer 只进 0600 header/config 文件，不进 curl argv。
- **前端 mock 关闭** 与 **后端合成 fixture** 仍然分开：不得设 `VITE_MATERIAL_RAG_REPORT_MOCK=1`；确定性本地生成器若在测试 edge 打开，只服务于测试材料，不是真实客户数据，也不是 Ark。
- 本机浏览器 UAT 通过 **不是** 远端部署证据。

现役结论仅允许：

目标技术终态是 `RELEASE_CANDIDATE_LOCAL_PASSED / DEPLOYMENT_PACKAGE_INTEGRATED_LOCAL / COMMITTED_LOCAL / NOT_PUSHED / HUMAN_VISUAL_ACCEPTANCE_PENDING / REMOTE_TARGET_PENDING / NOT_DEPLOYED / NOT_PRODUCTION`；当前因两项硬规则过程例外仍为 `BLOCKED`，尚未达到该终态。

在所有本地机器门实际通过前，不得提前写第一项；无论本地结果如何，后六项边界保持不变，直到另行授权与取证。
