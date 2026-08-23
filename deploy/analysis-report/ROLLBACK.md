# A-Eco 分析报告测试环境回滚命令单

本文件是后续授权执行模板；当前 `NOT_DEPLOYED / NOT_PRODUCTION`。回滚必须恢复到已知可用发布与 `f1_0017` 备份点，不通过热改业务代码、放宽 RLS/CORS、暴露内部端口或伪造健康状态止血。

## 1. 决策顺序

1. 立即停止新写入和新的 Netlify 别名切换，保留失败现场与日志边界。
2. 仅前端/edge 配置故障：先回 Netlify deploy，再回 Keycloak 精确 origin；数据库不动。
3. `f1_0018` 迁移后应用不兼容：走下方“恢复式回滚”，恢复 pre-0018 备份到新数据库，再切换服务；**禁止执行 Alembic downgrade**。
4. 任何恢复后都重新跑 `REMOTE_SMOKE.md`；人工验收仍为 pending。

## 2. Netlify 发布回滚

Netlify token 只能由执行环境的 secret manager 注入，不打印、不写命令行值。当前官方 CLI 没有 `deploy:list` 或 `rollback` 子命令；本命令单改用 CLI 的通用 `api` 命令调用官方 `listSiteDeploys` 与 `restoreSiteDeploy` API。服务器执行者必须先记录 CLI 版本并确认这两个 API method 在其安装版本可用；任一检查失败都停止，不猜命令。

```bash
ROLLBACK_EVIDENCE_DIR="<OUTSIDE_REPO_RELEASE_DIR>/netlify-rollback"
install -d -m 700 "$ROLLBACK_EVIDENCE_DIR"
netlify --version > "$ROLLBACK_EVIDENCE_DIR/netlify-cli-version.txt"
netlify api --list > "$ROLLBACK_EVIDENCE_DIR/netlify-api-methods.txt"
chmod 600 "$ROLLBACK_EVIDENCE_DIR/netlify-cli-version.txt" \
  "$ROLLBACK_EVIDENCE_DIR/netlify-api-methods.txt"
grep -q 'listSiteDeploys' "$ROLLBACK_EVIDENCE_DIR/netlify-api-methods.txt"
grep -q 'restoreSiteDeploy' "$ROLLBACK_EVIDENCE_DIR/netlify-api-methods.txt"

LIST_DATA="$(python3 - "$NETLIFY_SITE_ID" <<'PY'
import json, sys
print(json.dumps({"site_id": sys.argv[1], "per_page": 20}, separators=(",", ":")))
PY
)"
netlify api listSiteDeploys --data "$LIST_DATA" \
  > "$ROLLBACK_EVIDENCE_DIR/deploys.json"
chmod 600 "$ROLLBACK_EVIDENCE_DIR/deploys.json"

# 人工从 0600 deploys.json 核对并填写，不自动选择“最近一次”。
PREVIOUS_KNOWN_GOOD_DEPLOY_ID="<PREVIOUS_KNOWN_GOOD_DEPLOY_ID>"
case "$PREVIOUS_KNOWN_GOOD_DEPLOY_ID" in
  ""|*'<'*|*'>'*) exit 64 ;;
esac
read -r -p "Type RESTORE DEPLOY $PREVIOUS_KNOWN_GOOD_DEPLOY_ID to continue: " RESTORE_CONFIRM
test "$RESTORE_CONFIRM" = "RESTORE DEPLOY $PREVIOUS_KNOWN_GOOD_DEPLOY_ID"

RESTORE_DATA="$(python3 - "$NETLIFY_SITE_ID" "$PREVIOUS_KNOWN_GOOD_DEPLOY_ID" <<'PY'
import json, sys
print(json.dumps({"site_id": sys.argv[1], "deploy_id": sys.argv[2]}, separators=(",", ":")))
PY
)"
netlify api restoreSiteDeploy --data "$RESTORE_DATA" \
  > "$ROLLBACK_EVIDENCE_DIR/restore-response.json"
chmod 600 "$ROLLBACK_EVIDENCE_DIR/restore-response.json"
python3 - "$ROLLBACK_EVIDENCE_DIR/restore-response.json" "$NETLIFY_SITE_ID" \
  "$PREVIOUS_KNOWN_GOOD_DEPLOY_ID" <<'PY'
import json, pathlib, sys
data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert data.get("site_id") == sys.argv[2]
assert data.get("id") == sys.argv[3]
print("netlify_restore_requested=1")
PY
```

官方合同依据：Netlify CLI [`api`](https://cli.netlify.com/commands/api/) 命令、OpenAPI 的 [`listSiteDeploys` / `restoreSiteDeploy`](https://open-api.netlify.com/)。回滚后检查最终 origin 的 TLS、headers、四条 rewrite 顺序与 SPA fallback；不要把根 `netlify.toml` 写回仓库。

## 3. Keycloak 与 edge 恢复

- client `anhuan-web` 的 redirect URI 恢复为已知良好 origin 的精确闭集：`<PREVIOUS_NETLIFY_ORIGIN>` 与 `<PREVIOUS_NETLIFY_ORIGIN>/*`。
- web origin 只保留 `<PREVIOUS_NETLIFY_ORIGIN>`；移除失败 deploy 的 origin，不保留 wildcard、HTTP 或 loopback。
- `F1_KEYCLOAK_ISSUER_URL` 与 discovery `issuer` 都恢复为 `<PREVIOUS_NETLIFY_ORIGIN>/realms/anhuan`；内部 `KEYCLOAK_URL` 不改成公网地址。
- edge 故障时撤掉指向坏 edge 的 rewrite/别名，不临时公开 PostgreSQL、Keycloak 管理口或内部 API。
- CORS 若存在，只能精确允许恢复后的 Netlify origin，禁止 `*` 与 credentials 组合。

## 4. PostgreSQL 恢复式回滚

适用条件：已经成功到 `f1_0018`，且应用/数据兼容性要求回到迁移前。只使用 `DEPLOYMENT.md` 生成的 pre-0018 custom dump；不在原数据库上执行 down migration，不覆盖或删除原数据库。

在 0600 `release.env` 中另行填入新的、未存在的 `RESTORE_DATABASE`，然后：

```bash
umask 077
test -n "$BACKUP_DIR"
test -f "$BACKUP_DIR/database.dump"
test "$(stat -c '%a' "$BACKUP_DIR/database.dump")" = "600"
sha256sum --check "$BACKUP_DIR/SHA256SUMS"
pg_restore --list "$BACKUP_DIR/database.dump" > "$BACKUP_DIR/restore.list"
chmod 600 "$BACKUP_DIR/restore.list"

export PGHOST="$F1_PG_HOST" PGPORT="$F1_PG_PORT" PGUSER=f0d_bootstrap PGPASSFILE
PGDATABASE=postgres createdb --owner=f0d_bootstrap "$RESTORE_DATABASE"
PGDATABASE="$RESTORE_DATABASE" pg_restore \
  --exit-on-error \
  --dbname="$RESTORE_DATABASE" \
  "$BACKUP_DIR/database.dump"

restored_head="$(PGDATABASE="$RESTORE_DATABASE" psql -X -A -t -v ON_ERROR_STOP=1 \
  -c 'SELECT version_num FROM f1.alembic_version')"
test "$restored_head" = "f1_0017"
```

随后由服务器执行者把 API/worker 的 `F1_PG_DATABASE` 原子切到 `$RESTORE_DATABASE`，按其服务管理器重启并核 `/api/readyz`。原 `f1_0018` 数据库保持隔离只读，直到另一次明确的数据处置授权；本命令单不删除数据库、备份、卷或 secret。

若迁移命令本身失败且事务回滚后 head 仍精确为 `f1_0017`，保留备份和错误输出，修配置后重试门禁；不要无条件恢复覆盖一枚仍正确的数据库。

## 5. 测试站点删除：仅人工二次确认

站点删除不是正常回滚。只有负责人已先确认 Keycloak 移除该 origin、证据已导出、站点 ID 已复核，并在终端进行**人工二次确认**后才允许执行。当前任务严禁执行。

```bash
read -r -p "Type DELETE TEST SITE $NETLIFY_SITE_ID to continue: " SITE_DELETE_CONFIRM
test "$SITE_DELETE_CONFIRM" = "DELETE TEST SITE $NETLIFY_SITE_ID"
netlify sites:delete "$NETLIFY_SITE_ID"
```

`sites:delete` 的当前官方语法使用位置参数 `id`，不使用 `--site`；依据见 [Netlify CLI sites 命令参考](https://cli.netlify.com/commands/sites/)。该操作不可由脚本默认触发，不得使用 `--force`、不得预填确认串、不得在本轮执行。

## 6. 恢复后状态

恢复成功最多写：`REMOTE_SMOKE_PASSED / HUMAN_VISUAL_ACCEPTANCE_PENDING / NOT_PRODUCTION`。没有实际远端执行证据时仍保持 `REMOTE_TARGET_PENDING / NOT_DEPLOYED / NOT_PRODUCTION`。
