# 本机分析报告专属测试环境交接

本目录只描述 **本机可重复启停** 的专属测试栈。  
`REMOTE_STAGING_TARGET_NOT_AUTHORIZED`。  
`NOT_PRODUCTION`。  
不是共享 `anhuan-f1` 栈，不是远端预发，不是生产。

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
- **后端确定性本地生成器**：`F1_MATERIAL_ANALYSIS_REPORT_LOCAL=1` 且 `F1_EXTERNAL_PIPELINES_ENABLED=false`。fixture 写入的是 **released + clean + preview-ready 的合成材料**，供 JOIN 生成 7 节、引用≥2 的草稿。这不是真实客户数据，也不是真实模型/Ark 生成。
- `status.generator` 应为 `deterministic_local`，`ark_calls=0`，`mock_data=0`。

## 能力边界

可在本机重复验证：报告创建 → 生成首个版本 → 提交审核 → 勾选清单 → 批准 → 发布 → 客户阅读 → 撤回后对客户不可见。

不可声称：真实 Ark/RAGFlow 生成通过、远端预发授权、生产就绪、人工验收已完成。

浏览器全流程门：`./scripts/localctl analysis-report-workflow-uat-check`  
成功时 stdout 仅 canonical JSON + `LOCAL_ANALYSIS_REPORT_WORKFLOW_BROWSER_OK`，stderr 空。

现役结论仅允许：

`ANALYSIS_REPORT_WORKFLOW_BROWSER_UAT_PASSED / LOCAL_TEST_ENVIRONMENT_HANDOFF_READY / CHECKPOINT_COMMITTED / DRAFT_PR_UPDATED / REMOTE_STAGING_TARGET_NOT_AUTHORIZED / HUMAN_ACCEPTANCE_PENDING / NOT_PRODUCTION`
