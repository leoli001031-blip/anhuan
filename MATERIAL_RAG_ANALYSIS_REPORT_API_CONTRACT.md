# Analysis Report API Contract v1

schema: `anhuan-analysis-report-api-v1`  
frozen: 2026-08-21  
implementation MUST NOT silently rename or drop fields.

Identity header: `Authorization: Bearer`. Optional `X-Enterprise-Id` only selects among the caller's memberships (existing platform rule). Client bodies/queries MUST NOT include `client_account_id`, `tenant_id`, `enterprise_id`, or `knowledge_scope_id`.

## GET /api/v1/session/access

Response `SessionAccessV1`:

| field | type | notes |
| --- | --- | --- |
| schema | const `anhuan-analysis-report-session-v1` | |
| product_role | `provider_admin` \| `client_user` | derived; never requested |
| enterprise_id | uuid | session tenant |
| template_id | const `enterprise-ehs-material-analysis-v1` | |
| template_title | const `企业安环资料分析报告` | |
| capabilities | string[] | closed set below |

provider capabilities: `list_client_reports`, `create_report`, `generate`, `review`, `publish`, `withdraw`  
client capabilities: `list_published`, `read_published`

## Client

### GET /api/v1/analysis-reports/published

List. No identity fields. 200 `{schema, reports:[PublishedReportSummaryV1]}`.

`PublishedReportSummaryV1`: `report_id`, `version_id`, `version_number`, `title`, `published_at`, `artifact_ready`.

### GET /api/v1/analysis-reports/published/{report_id}

Detail or **404**. Never 403 for wrong tenant/client/unpublished.

`PublishedReportDetailV1`: summary fields plus `sections[]`, `citations[]`.

`SectionV1`: `key`, `title`, `body`  
`CitationV1`: `citation_id`, `document_version_id`, `document_name`, `version_number`, `page_number`, `excerpt`

Section keys frozen: `source_scope`, `status_summary`, `key_findings`, `risks_and_gaps`, `remediation`, `citations`, `usage_boundary`.

## Provider

Path param `client_account_id` is the sole client selector and MUST be a `crm_account.id` owned by the session enterprise.

### GET /api/v1/analysis-reports/clients/{client_account_id}/reports

`{schema: anhuan-analysis-report-provider-list-v1, reports:[ProviderReportSummaryV1]}`  
`ProviderReportSummaryV1`: `report_id`, `current_version_id`, `current_status`, `version_number`, `title`, `updated_at`

### POST /api/v1/analysis-reports/clients/{client_account_id}/reports

Body `{request_id: uuid}` only. Creates empty report. 200 `ProviderReportSummaryV1`. Same `request_id`+same client replays. Different client/template → 409 `REQUEST_ID_CONFLICT`.

### POST /api/v1/analysis-reports/clients/{client_account_id}/reports/{report_id}/generations

Body `{request_id: uuid}`. Requires flags. Creates immutable version + job, freezes source fingerprint, runs fake generator.  
200 `GenerationAcceptedV1`: `job_id`, `version_id`, `status` (`generating` or terminal `draft`/`failed`).  
409 `REQUEST_ID_CONFLICT` if request_id reused with different fingerprint.  
404 if client/report mismatch, empty client sources, ineligible sources.

### GET /api/v1/analysis-reports/jobs/{job_id}

`JobStatusV1`: `job_id`, `version_id`, `status`, `error_reason` (code or null). No source body.

### GET /api/v1/analysis-reports/versions/{version_id}

Draft/review detail, schema `anhuan-analysis-report-draft-v1`. Same section/citation shapes. 404 if not in session tenant.

### POST .../versions/{version_id}/submit | return | approve | publish | withdraw

Empty body. 200 `ProviderReportSummaryV1`. Illegal transition → 409 `REPORT_TRANSITION_INVALID`.

- submit: `draft` → `review_pending`
- return: `review_pending` → `changes_requested` (edit continues only via new generation)
- approve: `review_pending` → `approved`
- publish: `approved` → `published`; previous published on same report → `superseded`; `artifact_ready=true`
- withdraw: `published` → `withdrawn`

### GET /api/v1/analysis-reports/{report_id}/versions

`{schema, versions:[VersionHistoryItemV1]}`  
`VersionHistoryItemV1`: `version_id`, `version_number`, `status`, `created_at`

## Error envelope

HTTP 404 `{"detail":"REPORT_NOT_FOUND"}` for all client misses.  
409 `REQUEST_ID_CONFLICT` / `REPORT_TRANSITION_INVALID`.  
404 `ANALYSIS_REPORT_GENERATION_DISABLED` when flags off (fail-closed, no disclose).

## Forbidden response keys

`dataset_id`, `chunk_id`, `knowledge_scope_id`, `lease_token`, `object_key`, `ragflow_*`.
