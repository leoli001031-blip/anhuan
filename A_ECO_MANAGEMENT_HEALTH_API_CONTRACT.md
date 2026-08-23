# 安环管理健康度 HTTP 合同（冻结）

路径：`GET /api/v1/analysis-reports/health/latest`<br>
身份仅来自会话；查询与正文不得携带客户/租户身份字段。

## Envelope（exact-key，顺序固定）

```text
schema, snapshot
```

- `schema` 必须等于 `anhuan-analysis-report-health-v1`
- `snapshot` 必须是 `null` 或下方对象；不得缺键、多键或改序

无绑定、无已发布版本、无快照、已撤回：`200` 且 `snapshot=null`。<br>
载荷无法通过本合同时拒绝；库内哈希/完整性失败或数据库错误：`503`，禁止回退旧分。

## snapshot 对象（exact-key，顺序固定）

```text
report_id
version_id
version_number
report_title
score
max_score
status_label
assessed_on
basis_label
evidence_mode
dimensions
priorities
boundary
```

- `report_id` / `version_id`：UUID
- `version_number`：正整数
- `report_title`：非空字符串
- `score`：整数，等于六维 `score` 之和，范围 `[0, 100]`
- `max_score`：必须为 `100`
- `status_label` / `basis_label` / `boundary`：非空字符串
- `assessed_on`：ISO-8601
- `evidence_mode`：本轮仅 `deterministic_local`（本地双开关演示）。正式环境无评分器时不得发明其他 mode，只能 `snapshot=null`

## dimensions（长度 6，顺序与闭集固定）

维对象 exact-key 顺序：

```text
key, label, score, max_score, summary, tone
```

| 序号 | key | max_score |
|------|-----|-----------|
| 1 | material-completeness | 15 |
| 2 | permits | 20 |
| 3 | monitoring | 20 |
| 4 | remediation | 25 |
| 5 | expiry | 10 |
| 6 | evidence | 10 |

- 每维 `score` 为整数且 `0 ≤ score ≤ max_score`
- `tone` 仅 `positive` / `attention` / `priority`
- 缺维、多维、错序一律拒绝

## priorities

- 长度 1–3
- 对象 exact-key 顺序：`title`, `level`
- `level` 仅 `high` / `medium`

## 响应禁词（任意层级键名）

禁止出现：`provider`、`client`、`binding`、`scope`、`dataset`、`chunk`、`sha`、`lease`、`request_id` / `request-id`。<br>
`payload_sha256` 只存在数据库，不得进入 HTTP。
