# A-Eco 分析报告测试环境部署命令单

状态：已提交基线为 `codex/material-report-aeco-polish@955a274990cd37797dbb6ef2c11459b288074ff8`；当前修正为 `NOT_COMMITTED / NOT_PUSHED / REMOTE_TARGET_PENDING / NOT_DEPLOYED / NOT_PRODUCTION`。

本目录只提供可渲染模板、离线 preflight 和参数化命令单；不授权连接 Netlify/GitHub、改 PR、迁移远端数据库或部署。服务器规格、DNS、证书、站点 ID 与服务管理器均未知，执行者必须在另行授权后填入自己的参数并逐门留证。

## 0. OCR 架构硬门

当前唯一经过锁定的 F0-H OCR runtime 是 `linux/arm64`，镜像、离线 wheel、组件锁和 seccomp profile 都绑定 ARM64。完整的“上传 → OCR → 索引 → 报告”远端测试开始前，先在目标服务器只读确认：

```bash
uname -m
docker version --format '{{.Server.Arch}}'
```

两项都必须对应 `arm64/aarch64`。若目标是 `amd64/x86_64`，立即停止 OCR 部署；不得依赖未取证的 QEMU 模拟，也不得只改 Compose 的 `platform`。必须先另建并锁定 AMD64 runtime、wheel/hash、模型身份和 `SCMP_ARCH_X86_64` profile，再把新镜像作为独立候选验收。未完成前可以部署不含 OCR 的普通文本 PDF 测试环境，但不得声称扫描 PDF 全自动链路可用。

仓库为这种 amd64 测试机提供显式的无 OCR 候选模式：

```bash
export A_ECO_ANALYSIS_REPORT_OCR_MODE=disabled
python -B deploy/analysis-report/local_candidate.py start
```

该模式会追加 `infra/f1/docker-compose.analysis-report-no-ocr.yml`，把 OCR 服务移入未启用 profile，并让 API、dispatcher、普通 worker 与 ingestion worker 以 `F1_MATERIAL_OCR_ENABLED=false` 启动。它仍然保留 ClamAV、MinIO、Redis、PostgreSQL、Keycloak、入库调度和 native-text PDF 处理；扫描件或图片 PDF 必须 fail-closed 为 `OCR_REQUIRED`。默认未设置该变量时仍执行 ARM64 OCR 硬门，二者使用不同的 compose project/control identity，不得混用。

服务器已经存在旧 demo 时，不得把新代码覆盖到旧 checkout 后直接执行 `start`。应把当前候选解包到新的版本目录，由不同 project/volume 启动；受内存约束时只 `stop` 旧容器而不 `down`，新候选失败后重新启动旧 project。删除旧 volume、原地迁移旧数据库或覆盖旧目录都需要另行授权和备份证据。

架构正确也不等于镜像已就绪。Compose 只接受锁定的本地内容 ID `sha256:02e6300f52463818de7ceaf447bfb0765e5f8466251177006131dec4e55a27f5`，并且 `pull_policy: never`。仓库不包含可直接在新机器上构建的 F0-E 基础镜像或最终镜像 tar；因此必须由授权发布人通过受保护的交付通道转移并 `docker load` 该 ARM64 镜像，然后精确核验：

```bash
test "$(docker image inspect --format '{{.Id}}' sha256:02e6300f52463818de7ceaf447bfb0765e5f8466251177006131dec4e55a27f5)" = \
  "sha256:02e6300f52463818de7ceaf447bfb0765e5f8466251177006131dec4e55a27f5"
```

未完成该镜像交付时，本地 demo/UAT 入口会在启动容器前以 `LOCAL_ANALYSIS_REPORT_OCR_IMAGE_MISSING` 失败关闭；非 ARM64 Docker Server 会以 `LOCAL_ANALYSIS_REPORT_OCR_ARCH_UNSUPPORTED` 失败关闭。

## 1. 候选层与固定拓扑

- 合并顺序固定：PR #3（分析报告集成）先入 `main`；PR #4（A-Eco/健康度层）改 base 到 `main`、复核只剩本层后再合。
- Netlify 只托管 `src/web` 构建出的静态 `dist`。
- 浏览器只走 Netlify 同源相对路径 `/api`、`/realms/anhuan`、`/resources`。
- Netlify 按 `/api/*`、`/realms/*`、`/resources/*` 顺序代理到同一个 HTTPS edge；`/* → /index.html` 必须最后。
- edge 再把 API、Keycloak realm/resources 代理到私网服务。PostgreSQL、Keycloak 管理口、内部 API/worker 端口不得直接暴露公网。

Netlify origin 与 edge origin 必须是两个不同的 HTTPS DNS origin；HTTP、loopback、裸 IP、单标签主机、路径/query/fragment、同源循环与残留占位都会被 preflight 拒绝。仓库根不得出现 `netlify.toml`。

候选包的三个真源分工固定为：

- 迁移：`infra/f1/analysis-reports/migrate.py`，且成功后必须精确核验 `f1_0023`。
- 本地启停：`scripts/localctl analysis-report-demo-*`；`deploy/analysis-report/local_candidate.py` 是不绑定 checkout 绝对路径的薄入口。
- 就绪：`/api/readyz` 的 HTTP 200、`Cache-Control: no-store`、`status=ready` 与精确组件闭集。只有容器存在不算 ready。

仅支持上方专属 migrator：直接运行 Alembic 会绕过数据库 owner finalizer，单独执行 `infra/f1/roles.sql` 也不构成受支持的角色供应流程；两者都不得用于候选或远端迁移。`downgrade` 同样不受支持，回退只允许使用迁移前备份恢复。

本地候选入口必须使用已安装本仓运行依赖的 Python；未激活时可用 `A_ECO_PYTHON` 显式指定解释器，不得把某台开发机的 venv/check-out 绝对路径写入仓库：

```bash
python -B deploy/analysis-report/local_candidate.py start
python -B deploy/analysis-report/local_candidate.py check --origin http://127.0.0.1:<port>
python -B deploy/analysis-report/local_candidate.py stop
```

`start` 已包含专属迁移、fixture、闭集 status 和 HTTP readiness；不要在共享默认栈上单独执行 `migrate`。

## 2. 仓外 0600 参数文件

先在服务器的仓外目录准备只含参数、不含正文凭证的文件。尖括号必须全部替换；不得把该文件提交或贴进日志。

```bash
umask 077
install -d -m 700 "<OUTSIDE_REPO_RELEASE_DIR>"
install -m 600 /dev/null "<OUTSIDE_REPO_RELEASE_DIR>/release.env"
${EDITOR:?EDITOR_REQUIRED} "<OUTSIDE_REPO_RELEASE_DIR>/release.env"
chmod 600 "<OUTSIDE_REPO_RELEASE_DIR>/release.env"
```

`release.env` 的参数闭集：

```text
NETLIFY_ORIGIN=<NETLIFY_HTTPS_ORIGIN>
EDGE_ORIGIN=<EDGE_HTTPS_ORIGIN>
ENVIRONMENT_NAME=<TEST_ENVIRONMENT_NAME>
NETLIFY_SITE_ID=<TEST_SITE_ID>
F1_PG_HOST=<PRIVATE_PG_HOST>
F1_PG_PORT=<PG_PORT>
F1_PG_DATABASE=<ANALYSIS_REPORT_DATABASE>
F1_SECRETS_DIR=<ABSOLUTE_0700_SECRET_DIR>
PGPASSFILE=<ABSOLUTE_0600_PGPASS_FILE>
BACKUP_ROOT=<ABSOLUTE_0700_BACKUP_ROOT>
```

加载后先核权限，不打印值：

```bash
set -a
. "<OUTSIDE_REPO_RELEASE_DIR>/release.env"
set +a
test "$(stat -c '%a' "$F1_SECRETS_DIR")" = "700"
test "$(stat -c '%a' "$PGPASSFILE")" = "600"
```

所有后续命令都从当前 Git checkout 自定位仓库根，不再要求手工填写候选 checkout 绝对路径：

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
test -f "$REPO_ROOT/deploy/analysis-report/preflight.py"
test -f "$REPO_ROOT/infra/f1/analysis-reports/migrate.py"
cd "$REPO_ROOT"
```

## 3. 渲染 Netlify 配置

渲染只允许写仓外。生成文件会原子写入并强制为普通 `0600` 文件。

```bash
cd "$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT/src:$REPO_ROOT"
python3 -B deploy/analysis-report/preflight.py \
  --netlify-origin "$NETLIFY_ORIGIN" \
  --edge-origin "$EDGE_ORIGIN" \
  --environment-name "$ENVIRONMENT_NAME" \
  --output "<OUTSIDE_REPO_RELEASE_DIR>/netlify.toml"
test "$(stat -c '%a' '<OUTSIDE_REPO_RELEASE_DIR>/netlify.toml')" = "600"
```

成功 stdout 只能是 `NETLIFY_TOML_RENDERED`；失败 stderr 只能是错误码且 exit=2。不得把生成物复制到仓库根。

## 4. DNS、TLS 与 edge 反代门

从两个 origin 只抽取主机名后检查 DNS；不得把裸 IP 回填为 origin。

```bash
NETLIFY_HOST="$(python3 -c 'import sys,urllib.parse; print(urllib.parse.urlsplit(sys.argv[1]).hostname)' "$NETLIFY_ORIGIN")"
EDGE_HOST="$(python3 -c 'import sys,urllib.parse; print(urllib.parse.urlsplit(sys.argv[1]).hostname)' "$EDGE_ORIGIN")"
dig +short "$NETLIFY_HOST"
dig +short "$EDGE_HOST"
curl --fail --silent --show-error --head --proto '=https' --tlsv1.2 "$NETLIFY_ORIGIN/"
curl --fail --silent --show-error --output /dev/null --proto '=https' --tlsv1.2 "$EDGE_ORIGIN/api/healthz"
```

证书 SAN 必须覆盖各自主机名，证书链与有效期由服务器执行者按其 TLS 终止器核验。edge 反代必须保持以下路径合同：

| 公网路径 | 私网上游行为 |
| --- | --- |
| `/api/healthz` | API `/healthz`，不缓存 |
| `/api/readyz` | API `/readyz`，不缓存 |
| `/api/*` | 保留 `/api/*` 代理到 API，不缓存 |
| `/realms/*` | 保留路径代理到 Keycloak，不缓存 |
| `/resources/*` | 保留路径代理到 Keycloak，不缓存 |

edge 日志只记 method/path/status，不记 Authorization、Cookie、请求体或响应体。除 TLS edge 外不开放数据库、管理端口或内部服务。

## 5. Keycloak issuer、redirect/web origin 与 CORS

- `F1_WEB_PUBLIC_ORIGIN` 精确设为 `$NETLIFY_ORIGIN`。
- `F1_KEYCLOAK_ISSUER_URL` 精确设为 `$NETLIFY_ORIGIN/realms/anhuan`；内部 `KEYCLOAK_URL` 仍指向私网 Keycloak origin。
- client `anhuan-web` 的 redirect URI 闭集为 `$NETLIFY_ORIGIN` 与 `$NETLIFY_ORIGIN/*`；web origin 闭集只有 `$NETLIFY_ORIGIN`。不得出现通配 host、HTTP、旧 origin 或 loopback。
- edge/Keycloak 必须用转发头或平台 hostname 设置，使 discovery 的 `issuer` 精确返回 `$NETLIFY_ORIGIN/realms/anhuan`，不能返回 edge 或内部地址。
- 当前浏览器拓扑是同源 rewrite，不依赖宽松 CORS。若 edge 仍发送 `Access-Control-Allow-Origin`，只能精确为 `$NETLIFY_ORIGIN`；禁止 `*` 与 credentials 组合。`REMOTE_SMOKE.md` 会核这个边界。

## 6. 公开 VITE 变量与测试能力标识

远端静态构建不需要任何 `VITE_*` 参数。代码识别的以下三项都属于浏览器公开测试开关，测试 edge 构建时必须保持未设置：

- `VITE_MATERIAL_RAG_REPORT_MOCK`
- `VITE_MATERIAL_RAG_REPORT_MOCK_ROLE`
- `VITE_MATERIAL_RAG_UAT_LOCAL`

```bash
env -u VITE_MATERIAL_RAG_REPORT_MOCK \
  -u VITE_MATERIAL_RAG_REPORT_MOCK_ROLE \
  -u VITE_MATERIAL_RAG_UAT_LOCAL \
  npm --prefix src/web run build
```

后端若为本轮测试 edge 开启 `F1_MATERIAL_ANALYSIS_REPORT_LOCAL=1` 与 `F1_LOCAL_ENGINEERING=1`，同时必须保持 `F1_EXTERNAL_PIPELINES_ENABLED=false`；这只会开启 `evidence_local` 报告测试生成能力，且必须保持 `ark_calls=0 / mock_data=0`。当前健康度评分器拒绝根据文档数量或关键词制造分数，HTTP 合同应为 `snapshot=null`，页面显示“暂不评分”。该本地证据生成器不是正式评分器、Ark、生产 worker 或真实客户能力。

## 7. 密钥注入

- `F1_SECRETS_DIR` 必须是执行用户拥有的真实 0700 目录，secret/DSN 文件必须为真实普通 0600 文件且不得是 symlink；沿用代码现有的 `f1_bootstrap_dsn`、`f1_migration_dsn` 与运行时 secret-file 合同。
- PostgreSQL 工具只通过 0600 `PGPASSFILE` 或等价 secret manager 注入凭证；不得把 DSN/口令放进 argv、仓库、`release.env`、Netlify 变量或日志。
- 浏览器 token 只允许进入 `REMOTE_SMOKE.md` 创建的 0600 临时文件；curl 通过 header 文件读取，禁止 `Authorization: Bearer ...` 出现在进程参数。
- 本轮不注入生产 Ark key、真实客户凭证或客户数据；外部 pipeline 保持关闭。

## 8. PostgreSQL 备份点与线性前向迁移 `0017 → 0023`

进入维护窗口并停止业务写入后执行。若当前 head 不是精确 `f1_0017`，停止，不猜测、不跳版。

```bash
export PGHOST="$F1_PG_HOST" PGPORT="$F1_PG_PORT" PGDATABASE="$F1_PG_DATABASE"
export PGUSER=f0d_bootstrap PGPASSFILE
head_before="$(psql -X -A -t -v ON_ERROR_STOP=1 -c 'SELECT version_num FROM f1.alembic_version')"
test "$head_before" = "f1_0017"

BACKUP_ID="pre-f1-0023-$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$BACKUP_ROOT/$BACKUP_ID"
install -d -m 700 "$BACKUP_DIR"
pg_dump --format=custom --file="$BACKUP_DIR/database.dump"
chmod 600 "$BACKUP_DIR/database.dump"
pg_restore --list "$BACKUP_DIR/database.dump" > "$BACKUP_DIR/database.list"
chmod 600 "$BACKUP_DIR/database.list"
sha256sum "$BACKUP_DIR/database.dump" > "$BACKUP_DIR/SHA256SUMS"
chmod 600 "$BACKUP_DIR/SHA256SUMS"

cd "$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT/src:$REPO_ROOT"
python3 -B infra/f1/analysis-reports/migrate.py
head_after="$(psql -X -A -t -v ON_ERROR_STOP=1 -c 'SELECT version_num FROM f1.alembic_version')"
test "$head_after" = "f1_0023"
```

专属 migrator 成功 stdout 应为 `LOCAL_ANALYSIS_REPORT_MIGRATE_OK`。默认工程仍锁 `f1_0014`，material-RAG 专属目标仍为 `f1_0016`；不得改默认 seed/verify/backup 目标。从 `f1_0017` 到 `f1_0023` 必须线性经过 0018–0022；若失败或应用回退，执行 `ROLLBACK.md` 的恢复式回滚，禁止直接 downgrade。

## 9. 后续授权的静态交付与门禁

只有 PR 顺序、远端参数、备份点、迁移、edge/Keycloak 配置都由执行者复核后，才可把 `src/web/dist` 与仓外生成的 Netlify 配置交给其 Netlify 发布步骤。站点创建/别名切换命令取决于服务器团队的 Netlify 工作流，本包不编造已实测命令，也不执行 deploy。

部署后必须完整执行 `REMOTE_SMOKE.md`；失败按 `ROLLBACK.md` 恢复。站点删除永远不是默认回滚，必须人工二次确认。
