# A-Eco 分析报告远端双身份冒烟

状态：`REMOTE_TARGET_PENDING / NOT_DEPLOYED / NOT_PRODUCTION`。本文件只能在后续明确授权、测试 origin 与合成租户已复核后执行；不得对真实客户材料、生产 Ark 或生产身份发请求。

所有请求必须打 **Netlify origin**，从而同时验证 TLS、同源 rewrite 与 edge；不得直连内部端口。access/refresh token、Cookie、响应正文都不得打印。stdout 只保留状态、角色、布尔与安全计数。

## 1. 0700/0600 工作目录与输入

```bash
umask 077
SMOKE_DIR="$(mktemp -d /tmp/analysis-report-remote-smoke.XXXXXX)"
chmod 700 "$SMOKE_DIR"
cleanup() {
  case "$SMOKE_DIR" in
    /tmp/analysis-report-remote-smoke.*) rm -rf -- "$SMOKE_DIR" ;;
    *) return 64 ;;
  esac
}
trap cleanup EXIT
```

仅使用合成身份：provider=`tenant-a`，client=`invitee`。通过授权码流程分别取得 token 后，由授权执行器把原始 access token 写入以下两个普通 0600 文件；不得用 `echo`、`cat` 或命令行参数传 token：

```text
$SMOKE_DIR/provider.token
$SMOKE_DIR/client.token
```

把合成测试租户的 enterprise UUID 与 client-account UUID 分别写入 `$SMOKE_DIR/enterprise-id`、`$SMOKE_DIR/client-account-id`，同样设为 0600。然后在 Python 进程内部把 token 与租户 ID 合成 curl header 文件，秘密值不会进入 curl argv：

```bash
python3 - "$SMOKE_DIR/provider.token" "$SMOKE_DIR/provider.headers" "$SMOKE_DIR/enterprise-id" <<'PY'
import pathlib, sys, uuid
token_path, output_path, enterprise_path = map(pathlib.Path, sys.argv[1:])
token = token_path.read_text(encoding="utf-8").strip()
enterprise = enterprise_path.read_text(encoding="utf-8").strip()
uuid.UUID(enterprise)
if not token or any(char.isspace() for char in token):
    raise SystemExit(64)
output_path.write_text(
    "Authorization: Bearer " + token + "\nX-Enterprise-Id: " + enterprise + "\n",
    encoding="utf-8",
)
output_path.chmod(0o600)
PY
python3 - "$SMOKE_DIR/client.token" "$SMOKE_DIR/client.headers" "$SMOKE_DIR/enterprise-id" <<'PY'
import pathlib, sys, uuid
token_path, output_path, enterprise_path = map(pathlib.Path, sys.argv[1:])
token = token_path.read_text(encoding="utf-8").strip()
enterprise = enterprise_path.read_text(encoding="utf-8").strip()
uuid.UUID(enterprise)
if not token or any(char.isspace() for char in token):
    raise SystemExit(64)
output_path.write_text(
    "Authorization: Bearer " + token + "\nX-Enterprise-Id: " + enterprise + "\n",
    encoding="utf-8",
)
output_path.chmod(0o600)
PY
chmod 600 "$SMOKE_DIR/provider.headers" "$SMOKE_DIR/client.headers"
```

后续 curl 只能使用 `--header @file`；禁止把 Bearer 展开到 `-H` 或 URL。

## 2. health、ready、OIDC issuer 与 CORS

```bash
code="$(curl --silent --show-error \
  --output "$SMOKE_DIR/health.out" --write-out '%{http_code}' \
  "$NETLIFY_ORIGIN/api/healthz")"
test "$code" = "200"
python3 - "$SMOKE_DIR/health.out" <<'PY'
import json, pathlib, sys
data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert data == {"status": "ok"}
print("health_status=200")
PY

code="$(curl --silent --show-error \
  --output "$SMOKE_DIR/ready.out" --write-out '%{http_code}' \
  "$NETLIFY_ORIGIN/api/readyz")"
test "$code" = "200"
printf 'ready_status=%s\n' "$code"

code="$(curl --silent --show-error \
  --output "$SMOKE_DIR/oidc.out" --write-out '%{http_code}' \
  "$NETLIFY_ORIGIN/realms/anhuan/.well-known/openid-configuration")"
test "$code" = "200"
python3 - "$SMOKE_DIR/oidc.out" "$NETLIFY_ORIGIN/realms/anhuan" <<'PY'
import json, pathlib, sys
data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert data.get("issuer") == sys.argv[2]
print("oidc_issuer_exact=1")
PY

curl --silent --show-error --dump-header "$SMOKE_DIR/cors.headers" \
  --output /dev/null --header "Origin: $NETLIFY_ORIGIN" \
  "$NETLIFY_ORIGIN/api/healthz"
chmod 600 "$SMOKE_DIR/cors.headers"
python3 - "$SMOKE_DIR/cors.headers" "$NETLIFY_ORIGIN" <<'PY'
import pathlib, sys
lines = pathlib.Path(sys.argv[1]).read_text(encoding="iso-8859-1").splitlines()
values = [line.split(":", 1)[1].strip() for line in lines
          if line.lower().startswith("access-control-allow-origin:")]
assert len(values) <= 1
assert not values or values == [sys.argv[2]]
assert "*" not in values
print("cors_same_origin_or_exact=1")
PY
```

## 3. 双身份与反向权限门

```bash
code="$(curl --silent --show-error \
  --output "$SMOKE_DIR/provider-access.out" --write-out '%{http_code}' \
  --header "@$SMOKE_DIR/provider.headers" \
  "$NETLIFY_ORIGIN/api/v1/session/access")"
test "$code" = "200"
python3 - "$SMOKE_DIR/provider-access.out" <<'PY'
import json, pathlib, sys
data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert data.get("product_role") == "provider_admin"
print("provider_role=provider_admin")
PY

code="$(curl --silent --show-error \
  --output "$SMOKE_DIR/client-access.out" --write-out '%{http_code}' \
  --header "@$SMOKE_DIR/client.headers" \
  "$NETLIFY_ORIGIN/api/v1/session/access")"
test "$code" = "200"
python3 - "$SMOKE_DIR/client-access.out" <<'PY'
import json, pathlib, sys
data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert data.get("product_role") == "client_user"
print("client_role=client_user")
PY

CLIENT_ACCOUNT_ID="$(python3 - "$SMOKE_DIR/client-account-id" <<'PY'
import pathlib, sys, uuid
value = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").strip()
uuid.UUID(value)
print(value)
PY
)"

# provider 不得使用客户 published surface；client 不得使用运营台客户报告 surface。
code="$(curl --silent --show-error \
  --output "$SMOKE_DIR/provider-negative.out" --write-out '%{http_code}' \
  --header "@$SMOKE_DIR/provider.headers" \
  "$NETLIFY_ORIGIN/api/v1/analysis-reports/published")"
test "$code" = "404"
printf 'provider_client_surface_denied=%s\n' "$code"

code="$(curl --silent --show-error \
  --output "$SMOKE_DIR/client-negative.out" --write-out '%{http_code}' \
  --header "@$SMOKE_DIR/client.headers" \
  "$NETLIFY_ORIGIN/api/v1/analysis-reports/clients/$CLIENT_ACCOUNT_ID/reports")"
test "$code" = "404"
printf 'client_provider_surface_denied=%s\n' "$code"
```

## 4. 创建 → 生成 → 审核 → 发布

```bash
CREATE_REQUEST_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
code="$(curl --silent --show-error \
  --output "$SMOKE_DIR/create.out" --write-out '%{http_code}' \
  --header "@$SMOKE_DIR/provider.headers" \
  --header 'content-type: application/json' \
  --request POST \
  "$NETLIFY_ORIGIN/api/v1/analysis-reports/clients/$CLIENT_ACCOUNT_ID/reports" \
  --data "{\"request_id\":\"$CREATE_REQUEST_ID\"}")"
test "$code" = "200"
REPORT_ID="$(python3 - "$SMOKE_DIR/create.out" <<'PY'
import json, pathlib, sys, uuid
value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))["report_id"]
uuid.UUID(value)
print(value)
PY
)"
printf 'create_status=%s\n' "$code"

GENERATE_REQUEST_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
code="$(curl --silent --show-error \
  --output "$SMOKE_DIR/generate.out" --write-out '%{http_code}' \
  --header "@$SMOKE_DIR/provider.headers" \
  --header 'content-type: application/json' \
  --request POST \
  "$NETLIFY_ORIGIN/api/v1/analysis-reports/clients/$CLIENT_ACCOUNT_ID/reports/$REPORT_ID/generations" \
  --data "{\"request_id\":\"$GENERATE_REQUEST_ID\"}")"
test "$code" = "200"
VERSION_ID="$(python3 - "$SMOKE_DIR/generate.out" <<'PY'
import json, pathlib, sys, uuid
data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert data.get("status") == "draft"
value = data["version_id"]
uuid.UUID(value)
print(value)
PY
)"
printf 'generate_status=%s\n' "$code"

code="$(curl --silent --show-error \
  --output "$SMOKE_DIR/submit.out" --write-out '%{http_code}' \
  --header "@$SMOKE_DIR/provider.headers" --request POST \
  "$NETLIFY_ORIGIN/api/v1/analysis-reports/versions/$VERSION_ID/submit")"
test "$code" = "200"
printf 'submit_status=%s\n' "$code"

# 命令块不得整体无确认执行；运营人员先人工核对合成报告审核清单。
read -r -p "Type APPROVE SYNTHETIC VERSION $VERSION_ID to continue: " APPROVE_CONFIRM
test "$APPROVE_CONFIRM" = "APPROVE SYNTHETIC VERSION $VERSION_ID"
code="$(curl --silent --show-error \
  --output "$SMOKE_DIR/approve.out" --write-out '%{http_code}' \
  --header "@$SMOKE_DIR/provider.headers" --request POST \
  "$NETLIFY_ORIGIN/api/v1/analysis-reports/versions/$VERSION_ID/approve")"
test "$code" = "200"
printf 'approve_status=%s\n' "$code"

read -r -p "Type PUBLISH SYNTHETIC VERSION $VERSION_ID to continue: " PUBLISH_CONFIRM
test "$PUBLISH_CONFIRM" = "PUBLISH SYNTHETIC VERSION $VERSION_ID"
code="$(curl --silent --show-error \
  --output "$SMOKE_DIR/publish.out" --write-out '%{http_code}' \
  --header "@$SMOKE_DIR/provider.headers" --request POST \
  "$NETLIFY_ORIGIN/api/v1/analysis-reports/versions/$VERSION_ID/publish")"
test "$code" = "200"
printf 'publish_status=%s\n' "$code"
```

## 5. 客户可见、健康度测试标识与撤回不可见

```bash
code="$(curl --silent --show-error \
  --output "$SMOKE_DIR/client-list.out" --write-out '%{http_code}' \
  --header "@$SMOKE_DIR/client.headers" \
  "$NETLIFY_ORIGIN/api/v1/analysis-reports/published")"
test "$code" = "200"
python3 - "$SMOKE_DIR/client-list.out" "$REPORT_ID" <<'PY'
import json, pathlib, sys
data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert any(item.get("report_id") == sys.argv[2] for item in data.get("reports", []))
print("client_list_visible=1")
PY

code="$(curl --silent --show-error \
  --output "$SMOKE_DIR/client-detail.out" --write-out '%{http_code}' \
  --header "@$SMOKE_DIR/client.headers" \
  "$NETLIFY_ORIGIN/api/v1/analysis-reports/published/$REPORT_ID")"
test "$code" = "200"
printf 'client_detail_visible=1\n'

code="$(curl --silent --show-error \
  --output "$SMOKE_DIR/health-latest.out" --write-out '%{http_code}' \
  --header "@$SMOKE_DIR/client.headers" \
  "$NETLIFY_ORIGIN/api/v1/analysis-reports/health/latest")"
test "$code" = "200"
python3 - "$SMOKE_DIR/health-latest.out" "$REPORT_ID" "$VERSION_ID" <<'PY'
import json, pathlib, sys
data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
snapshot = data.get("snapshot")
assert isinstance(snapshot, dict)
assert snapshot.get("report_id") == sys.argv[2]
assert snapshot.get("version_id") == sys.argv[3]
assert snapshot.get("evidence_mode") == "deterministic_local"
print("health_snapshot=1")
print("health_mode=deterministic_local")
print("test_capability=1")
PY

code="$(curl --silent --show-error \
  --output "$SMOKE_DIR/withdraw.out" --write-out '%{http_code}' \
  --header "@$SMOKE_DIR/provider.headers" --request POST \
  "$NETLIFY_ORIGIN/api/v1/analysis-reports/versions/$VERSION_ID/withdraw")"
test "$code" = "200"
printf 'withdraw_status=%s\n' "$code"

code="$(curl --silent --show-error \
  --output "$SMOKE_DIR/client-after-withdraw.out" --write-out '%{http_code}' \
  --header "@$SMOKE_DIR/client.headers" \
  "$NETLIFY_ORIGIN/api/v1/analysis-reports/published/$REPORT_ID")"
test "$code" = "404"
printf 'client_visible_after_withdraw=0\n'
```

`deterministic_local` 必须在页面显著标成测试能力；不得称为正式评分器。HTTP 503、空 snapshot 或合同错误都必须让前端显示“暂不评分”，不能回退到旧分数或假绿。

## 6. Ark=0、mock=0 与证据边界

服务器执行者必须使用其已授权、不会输出请求/响应正文的计数接口读取本次合成租户窗口，只允许形成以下两行：

```text
ark_calls=0
mock_data=0
```

任一非零即失败。构建环境不得启用 `VITE_MATERIAL_RAG_REPORT_MOCK`、`VITE_MATERIAL_RAG_REPORT_MOCK_ROLE` 或 `VITE_MATERIAL_RAG_UAT_LOCAL`。不得查看或复制生产 provider 日志、真实材料或客户数据来凑计数。

全部机器门通过后，远端层最多写 `REMOTE_SMOKE_PASSED / HUMAN_VISUAL_ACCEPTANCE_PENDING / NOT_PRODUCTION`。当前本地候选包没有远端执行证据，仍是 `REMOTE_TARGET_PENDING / NOT_DEPLOYED / NOT_PRODUCTION`。
