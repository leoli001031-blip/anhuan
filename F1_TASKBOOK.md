# F1 任务书：真实平台壳

> **历史执行合同。** 本文记录 F1 启动时技术基线（包括当时的 React 18 选择），不代表当前依赖或项目状态；现役事实见 [PROJECT_STATUS.md](./PROJECT_STATUS.md) 与 [src/web/README.md](./src/web/README.md)。

你是执行者，本书是唯一任务来源；中途没人可问，拿不准的写 `BLOCKED.md`，跳过继续别项。
断线先读 `PROGRESS.md` 最后一节接着做；每完成一项任务立即在 `PROGRESS.md` 追加 ≤12 行回执并贴真实命令输出。
目标：把 F0-J1 的 Fixture 工程升级为**可对外提供服务的平台壳**，但仍不开放真实业务。
**本书面向 DeepSeek V4 Flash 执行者**：每个任务包含可复制的命令、期望输出、产物路径；遇到未明确的二义性写 `BLOCKED.md`，不要猜测。
冲突时：租户/数据安全与证据正确 > 可恢复 > 可复现 > 覆盖率 > 速度。
"只允许/不许"违反即失败；"建议"可换，但须在 `PROGRESS.md` 记原因。

## 我替领导拍的板

- **前置决定已固化**：F0-J1 已完成，RAGFlow + 豆包 embedding + DeepSeek v4-pro 证据化 QA 链路可用。
- **F1 只建平台壳，不开真实业务**：仍使用 Fixture/合成数据；不接入真实客户生产环境；finding、报告、法规库仍为候选/草稿态。
- **成熟方案优先，不自建**：
  - 身份认证：**Keycloak**（OIDC/OAuth2，Docker 自托管，可替换为企业微信/钉钉/Auth0）。
  - 对象存储：**MinIO**（S3 兼容，已存在于 RAGFlow 栈，复用并扩展）。
  - 后端：**FastAPI + SQLAlchemy 2.0 + asyncpg/psycopg**。
  - 前端：**React 18 + TypeScript + Vite + Ant Design**。
  - 可观测性：**OpenTelemetry + Prometheus + Grafana + Jaeger**（容器化）。
  - 任务队列：**Redis + RQ**（或 Celery，任选其一，禁止自研调度）。
- **权限模型**：RBAC，角色为 `super_admin` / `enterprise_admin` / `plant_admin` / `partner` / `auditor`。
- **数据授权依据 D06**：26 份 Fixture 允许本机/隔离环境开发与评估。真实文件上传后必须进对象存储加密桶，密钥由平台管理，RAGFlow 索引只保存向量与 metadata。
- **PostgreSQL 仍为核心事实源**：F0-I 的 `f0i.*` 表继续作为 chunk 的加密权威来源；F1 新增业务表放在 `f1.*` schema，禁止修改 `f0i.*` 表结构。

## 边界

只允许改/建：
- `src/platform_foundation/f1/**`
- `src/web/**`（React 前端）
- `infra/f1/**`（Docker Compose、Keycloak/MinIO/Redis/OTel 配置）
- `tests/test_f1_*.py`
- `F1_TASKBOOK.md`（本书）、`PROGRESS.md`（追加）、`BLOCKED.md`（追加）
- `artifacts/f1-platform-shell/v0.1/**`

运行物限 `/private/tmp/anhuan-f1-*`（0700）与 `anhuan-f1-*` 容器/卷/网络。

**冻结（任一不符 → 原始输出置 BLOCKED.md 顶部并停止）**：
- F0-I 三产物 SHA 不变；F0-J1 产物不变。
- Alembic head 只允许新增 `f1_0001` ~ `f1_000N`；禁止修改 `f0d_*` migration。
- PostgreSQL `f0i.*` 表结构只读；合成租户 B 数据仍不进 PostgreSQL。
- 端口只绑 `127.0.0.1`；运行期不出网到 Ark/DeepSeek/Keycloak/MinIO 以外。

不读 `.env.local`；不改 F0-A～F0-J1 源码/测试/产物/锁文件；不引入 LangChain/LlamaIndex；不打开 acceptance.json 里的 closed gate。

## 环境准备（必须先执行）

所有后续命令假设：
- Python 虚拟环境已激活：`.venv/bin/python`
- Node.js ≥ 18 已安装：`node -v` 输出 v18+
- Docker Desktop / dockerd 已运行

安装后端依赖（追加到现有 `pyproject.toml` 或 `requirements.txt`，不许破坏已有依赖）：

```toml
# pyproject.toml [project.dependencies] 追加
"fastapi[standard]>=0.111",
"sqlalchemy[asyncio]>=2.0",
"asyncpg>=0.29",
"alembic>=1.13",
"minio>=7.2",
"redis>=5.0",
"rq>=1.16",
"python-jose[cryptography]>=3.3",
"httpx>=0.27",
"opentelemetry-api>=1.25",
"opentelemetry-sdk>=1.25",
"opentelemetry-instrumentation-fastapi>=0.46b0",
"opentelemetry-instrumentation-sqlalchemy>=0.46b0",
"opentelemetry-instrumentation-httpx>=0.46b0",
"opentelemetry-exporter-otlp>=1.25",
```

执行安装：

```bash
.venv/bin/python -m pip install -e .
# 或：.venv/bin/python -m pip install -r requirements.txt
```

前端依赖在 `src/web/` 初始化后安装，见任务 5。

## 泄漏红线

1. 聊天/PROGRESS/BLOCKED 只出现：聚合计数、布尔、退出码、SHA-256、脱敏 reason code。**禁止**：DSN/口令、源文件名、chunk 正文、密钥字节、绝对路径、LLM 完整回答原文、用户密码哈希、OIDC client_secret。
2. 禁止整文件输出 config/bootstrap/测试 DSN 段。
3. 原件校验用 `shasum -s -c` 静默，只报退出码。
4. 禁止 `|| true`。
5. Ark key / DeepSeek key / Keycloak admin / MinIO root 仅存 secrets 目录（0600），聊天永不出现。
6. 测试只断言状态码/角色/citation 存在/审计字段，不写回答原文或用户敏感信息。

## 任务 0：基线与前置闸门

执行命令并满足以下输出：

```bash
# 1. F0-I 三产物 SHA 复核
cd /Users/lichenhao/Desktop/安环项目
shasum -s -c artifacts/f0i-acceptance-v01/sha256sums.txt
echo "exit=$?"  # 期望 exit=0

# 2. 容器残留检查
docker ps --format '{{.Names}}' | grep -E '^anhuan-f0(j0|j1|f1)-' || true
# 期望输出为空

docker ps --format '{{.Names}}' | grep '^anhuan-f0d-postgres-1$'
# 期望输出一行

# 3. key 存在性
ls -l /private/tmp/anhuan-f0i-acceptance-v01.key
# 期望：-rw-------  1 ... 32 ...

# 4. F0-I 库基线
.venv/bin/python - <<'PY'
from platform_foundation.f0i.config import database_config
from platform_foundation.database import role_transaction
config = database_config()
with role_transaction(config, "f0d_migration") as conn:
    cur = conn.cursor()
    cur.execute("SELECT version_num FROM alembic_version")
    assert cur.fetchone()["version_num"] == "f0d_0006"
    cur.execute("""
        SELECT 'configuration' as t, count(*) as c FROM f0i.configuration
        UNION ALL SELECT 'run', count(*) FROM f0i.run
        UNION ALL SELECT 'document_scope', count(*) FROM f0i.document_scope
        UNION ALL SELECT 'page', count(*) FROM f0i.page
        UNION ALL SELECT 'block', count(*) FROM f0i.block
        UNION ALL SELECT 'chunk', count(*) FROM f0i.chunk
        UNION ALL SELECT 'chunk_block_link', count(*) FROM f0i.chunk_block_link
    """)
    rows = {r["t"]: r["c"] for r in cur.fetchall()}
expected = {"configuration": 1, "run": 2, "document_scope": 26, "page": 249, "block": 1909, "chunk": 553, "chunk_block_link": 1636}
assert rows == expected, f"{rows} != {expected}"
print("BASELINE_OK")
PY

# 5. 全仓回归
.venv/bin/python -m pytest tests/ -q
# 期望最后一行：Ran 690+N tests / OK

# 6. 镜像 digest 预登记
mkdir -p artifacts/f1-platform-shell/v0.1
.venv/bin/python - <<'PY'
import json, subprocess
def digest(image):
    out = subprocess.check_output(["docker", "pull", image], text=True)
    # 取本地 digest
    info = subprocess.check_output(["docker", "inspect", "--format={{index .RepoDigests 0}}", image], text=True)
    return info.strip()
images = {
    "keycloak": "quay.io/keycloak/keycloak:25.0",
    "minio": "minio/minio:RELEASE.2024-07-29T22-14-52Z",
    "redis": "redis:7-alpine",
    "otel_collector": "otel/opentelemetry-collector-contrib:0.107.0",
    "prometheus": "prom/prometheus:v2.53.1",
    "grafana": "grafana/grafana:11.1.0",
    "jaeger": "jaegertracing/all-in-one:1.59",
}
result = {k: digest(v) for k, v in images.items()}
Path("artifacts/f1-platform-shell/v0.1/images.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
PY
```

任一检查失败 → BLOCKED。

## 任务 1：基础设施与本地开发栈

1. 创建 `infra/f1/docker-compose.yml`，端口固定如下（全部绑 127.0.0.1）：
   - Keycloak：`8080`
   - MinIO API：`9000`，Console：`9001`
   - Redis：`6379`
   - Prometheus：`9090`
   - Grafana：`3000`
   - Jaeger UI：`16686`，OTel gRPC：`4317`，OTel HTTP：`4318`
   - 全部容器/卷/网络前缀 `anhuan-f1-`。
   - Keycloak 使用内置 H2 开发模式；生产前必须迁移到 PostgreSQL。

2. 创建 secrets 目录与文件：

```bash
mkdir -p /private/tmp/anhuan-f1-secrets
chmod 0700 /private/tmp/anhuan-f1-secrets
printf 'admin123!' > /private/tmp/anhuan-f1-secrets/keycloak_admin_password
printf 'minioadmin' > /private/tmp/anhuan-f1-secrets/minio_root_user
printf 'minioadmin' > /private/tmp/anhuan-f1-secrets/minio_root_password
chmod 0600 /private/tmp/anhuan-f1-secrets/*
```

3. 创建 `infra/f1/.env.example`（脱敏模板，不含真实密码）：

```dotenv
# infra/f1/.env.example
KEYCLOAK_ADMIN=admin
KEYCLOAK_ADMIN_PASSWORD_FILE=/run/secrets/keycloak_admin_password
MINIO_ROOT_USER_FILE=/run/secrets/minio_root_user
MINIO_ROOT_PASSWORD_FILE=/run/secrets/minio_root_password
```

4. 启动并健康检查：

```bash
cd infra/f1
docker compose up -d
sleep 20

# Keycloak
curl -sf http://127.0.0.1:8080/realms/master > /dev/null
echo "keycloak exit=$?"  # 期望 0

# MinIO
.venv/bin/python -m pip install minio
.venv/bin/python - <<'PY'
from minio import Minio
import os
with open("/private/tmp/anhuan-f1-secrets/minio_root_user") as f: u=f.read().strip()
with open("/private/tmp/anhuan-f1-secrets/minio_root_password") as f: p=f.read().strip()
c = Minio("127.0.0.1:9000", access_key=u, secret_key=p, secure=False)
c.make_bucket("anhuan-f1-documents") if not c.bucket_exists("anhuan-f1-documents") else None
print("minio ok")
PY

# Redis
redis-cli -h 127.0.0.1 -p 6379 ping
# 期望 PONG

# Prometheus
curl -sf http://127.0.0.1:9090/api/v1/status/targets > /dev/null
echo "prometheus exit=$?"  # 期望 0
```

5. 产物：`infra/f1/docker-compose.yml`、`infra/f1/.env.example`、`artifacts/f1-platform-shell/v0.1/health_check.json`（记录各端点状态）。

任一健康检查失败 → BLOCKED。

## 任务 2：身份服务（Keycloak）集成

1. 用 Keycloak Admin CLI 创建 Realm、客户端、角色、默认用户。全部命令必须在 `infra/f1/` 执行：

```bash
# 获取 admin token
KC_ADMIN=$(cat /private/tmp/anhuan-f1-secrets/keycloak_admin_password)
KC_TOKEN=$(curl -sf -X POST http://127.0.0.1:8080/realms/master/protocol/openid-connect/token \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "username=admin" -d "password=$KC_ADMIN" \
  -d 'grant_type=password' -d 'client_id=admin-cli' \
  | .venv/bin/python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 创建 realm
curl -sf -X POST http://127.0.0.1:8080/admin/realms \
  -H "Authorization: Bearer $KC_TOKEN" -H 'Content-Type: application/json' \
  -d '{"realm":"anhuan","enabled":true}'

# 创建角色
for role in super_admin enterprise_admin plant_admin partner auditor; do
  curl -sf -X POST http://127.0.0.1:8080/admin/realms/anhuan/roles \
    -H "Authorization: Bearer $KC_TOKEN" -H 'Content-Type: application/json' \
    -d "{\"name\":\"$role\"}"
done

# 创建 anhuan-web 客户端（public，OIDC）
curl -sf -X POST http://127.0.0.1:8080/admin/realms/anhuan/clients \
  -H "Authorization: Bearer $KC_TOKEN" -H 'Content-Type: application/json' \
  -d '{
    "clientId": "anhuan-web",
    "publicClient": true,
    "redirectUris": ["http://127.0.0.1:5173/*", "http://127.0.0.1:5173"],
    "webOrigins": ["http://127.0.0.1:5173"],
    "enabled": true
  }'

# 创建 anhuan-api 客户端（bearer-only）
curl -sf -X POST http://127.0.0.1:8080/admin/realms/anhuan/clients \
  -H "Authorization: Bearer $KC_TOKEN" -H 'Content-Type: application/json' \
  -d '{
    "clientId": "anhuan-api",
    "bearerOnly": true,
    "enabled": true
  }'

# 创建默认管理员用户
curl -sf -X POST http://127.0.0.1:8080/admin/realms/anhuan/users \
  -H "Authorization: Bearer $KC_TOKEN" -H 'Content-Type: application/json' \
  -d '{
    "username": "admin@anhuan.local",
    "email": "admin@anhuan.local",
    "enabled": true,
    "requiredActions": ["UPDATE_PASSWORD"],
    "realmRoles": ["super_admin"]
  }'
```

2. 导出 Realm 配置到 `infra/f1/keycloak/realm-export.json`（不含 secret）：

```bash
mkdir -p infra/f1/keycloak
curl -sf http://127.0.0.1:8080/admin/realms/anhuan \
  -H "Authorization: Bearer $KC_TOKEN" \
  | .venv/bin/python -m json.tool > infra/f1/keycloak/realm-export.json
```

3. 后端实现 `src/platform_foundation/f1/auth.py`，最小骨架：

```python
from __future__ import annotations
import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

KEYCLOAK_URL = "http://127.0.0.1:8080"
REALM = "anhuan"
JWKS_URL = f"{KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/certs"

_security = HTTPBearer(auto_error=False)

async def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(_security)) -> dict:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    # TODO: 拉取 JWKS，校验 RS256，返回 {sub, email, roles}
    ...

def require_role(*roles: str):
    async def checker(user: dict = Depends(current_user)):
        if not any(r in user.get("roles", []) for r in roles):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        return user
    return checker
```

4. 测试：`tests/test_f1_auth.py`，运行：

```bash
.venv/bin/python -m pytest tests/test_f1_auth.py -v
# 期望：5 passed
```

5. 产物：`src/platform_foundation/f1/auth.py`、`tests/test_f1_auth.py`、`infra/f1/keycloak/realm-export.json`。

## 任务 3：对象存储与文件上传

1. 后端 `src/platform_foundation/f1/storage.py`：
   - 封装 MinIO client：上传、下载、预签名 URL（开发期可关闭，生产期启用）。
   - 文件类型白名单：pdf/doc/docx/xls/xlsx/ppt/pptx/jpg/png；大小限制 ≤ 100MB。
   - 上传后返回 `object_key`（UUID）、`etag`、`size`、`content_type`。
2. 后端 `src/platform_foundation/f1/upload_task.py`：
   - 使用 RQ 异步任务：上传 →  virus scan（可选，先占位）→ 解密/转储 → 调用 F0-J1 适配器索引到 RAGFlow。
   - 任务状态持久化到 PostgreSQL `f1.upload_task` 表：pending / scanning / indexing / done / failed。
3. 前端：上传组件（拖拽、进度条、状态显示）。
4. 测试：`tests/test_f1_storage.py` 验证上传、下载、非法类型拒绝、大文件拒绝。
5. 产物：`src/platform_foundation/f1/storage.py`、`src/platform_foundation/f1/upload_task.py`、`tests/test_f1_storage.py`、前端上传组件。

## 任务 4：业务数据模型与 API（FastAPI）

1. Alembic 环境：复用项目已有 `alembic/` 目录，新增 `f1_0001` migration 放在 `alembic/versions/f1_0001_platform_shell_baseline.py`。新增分支 head 命名规则 `f1_0001`，不允许覆盖 `f0d_0006`。执行：

```bash
.venv/bin/alembic revision -m "f1 platform shell baseline" --head=f0d_0006 --branch-label=f1
# 生成后重命名为 alembic/versions/f1_0001_platform_shell_baseline.py
# 编辑 upgrade() 创建 f1.* 表
```

2. 表结构在 migration 中定义：
   - `f1.enterprise`（id, name, license_no, created_at, updated_at）
   - `f1.plant`（id, enterprise_id, name, address, created_at）
   - `f1.user_profile`（id, keycloak_sub, email, created_at）
   - `f1.enterprise_user`（id, enterprise_id, user_id, role, created_at）
   - `f1.document`（id, enterprise_id, plant_id, object_key, filename, size, content_type, status, created_at）
   - `f1.audit_log`（id, user_sub, action, resource_type, resource_id, result, created_at）

3. 后端目录结构：

```
src/platform_foundation/f1/
  __init__.py
  auth.py
  storage.py
  upload_task.py
  invitation.py
  audit.py
  database.py      # SQLAlchemy async engine/session
  models.py        # declarative models for f1.*
  api/
    __init__.py
    main.py
    routers/
      __init__.py
      enterprises.py
      plants.py
      users.py
      documents.py
      qa.py
      audit.py
```

4. `src/platform_foundation/f1/api/main.py` 最小骨架：

```python
from fastapi import FastAPI
from .routers import enterprises, plants, users, documents, qa, audit

app = FastAPI(title="AnHuan F1 Platform Shell")
app.include_router(enterprises.router, prefix="/api/v1/enterprises", tags=["enterprises"])
app.include_router(plants.router, prefix="/api/v1/plants", tags=["plants"])
app.include_router(users.router, prefix="/api/v1/users", tags=["users"])
app.include_router(documents.router, prefix="/api/v1/documents", tags=["documents"])
app.include_router(qa.router, prefix="/api/v1/qa", tags=["qa"])
app.include_router(audit.router, prefix="/api/v1/audit", tags=["audit"])
```

5. 启动后端：

```bash
PYTHONPATH=src .venv/bin/python -m uvicorn platform_foundation.f1.api.main:app --reload --host 127.0.0.1 --port 8000
# 期望：Application startup complete.
```

6. 测试：

```bash
.venv/bin/python -m pytest tests/test_f1_api.py -v
# 期望：全部通过
```

7. 产物：migration、模型、router、测试。

## 任务 5：前端平台壳（React）

1. 初始化 `src/web/`（使用 Vite 官方模板）：

```bash
npm create vite@latest web -- --template react-ts
cd src/web
npm install antd react-router-dom@6 oidc-client-ts
npm install
```

2. 目录结构：

```
src/web/
  src/
    main.tsx
    App.tsx
    components/
      UploadDragger.tsx
      QAPanel.tsx
      AuditLogTable.tsx
    pages/
      Login.tsx
      Layout.tsx
      EnterpriseList.tsx
      EnterpriseDetail.tsx
      DocumentList.tsx
      QAPage.tsx
      AuditPage.tsx
    auth/
      OidcProvider.tsx
      useAuth.ts
  index.html
  package.json
  vite.config.ts
```

3. OIDC 配置（`src/web/src/auth/OidcProvider.tsx`）：
   - authority: `http://127.0.0.1:8080/realms/anhuan`
   - client_id: `anhuan-web`
   - redirect_uri: `http://127.0.0.1:5173/callback`

4. 启动前端：

```bash
cd src/web
npm run dev
# 期望：VITE v...  ready in ...ms
# Local: http://127.0.0.1:5173/
```

5. 权限控制：前端按角色隐藏菜单/按钮；但所有权限 gate 在后端重新校验。
6. 产物：`src/web/` 完整可运行前端；`src/web/README.md` 说明启动方式。

## 任务 6：可观测性

1. 后端接入 OpenTelemetry Python SDK：
   - 自动 instrument FastAPI、SQLAlchemy、HTTPX/urllib。
   - 手动 span：QA 调用、文件上传任务、RAGFlow 检索。
2. 前端接入 OpenTelemetry JS SDK：
   - 自动 instrument fetch/XHR；记录路由切换。
3. OTel Collector 接收 trace/metric/log，分别转发：
   - traces → Jaeger
   - metrics → Prometheus
   - logs → 本地文件（开发期）或 Loki（可选）
4. Grafana 预置 dashboard：
   - API QPS / 延迟 / 错误率
   - QA 调用次数、平均延迟
   - 上传任务成功/失败数
5. 测试：触发一个 QA 请求，确认 Jaeger 出现完整 trace。

```bash
.venv/bin/python -m pytest tests/test_f1_observability.py -v
# 期望：trace 在 http://127.0.0.1:16686 可查询
```

6. 产物：`infra/f1/otel/` 配置、Grafana dashboard JSON、`tests/test_f1_observability.py`。

## 任务 7：权限矩阵与邀请流程

1. 明确权限矩阵并落地为 `require_role` + 数据范围校验：

| 能力 | super_admin | enterprise_admin | plant_admin | partner | auditor |
|------|-------------|------------------|-------------|---------|---------|
| 创建企业 | ✅ | ❌ | ❌ | ❌ | ❌ |
| 管理本企业用户 | ✅ | ✅ | ❌ | ❌ | ❌ |
| 管理本企业厂区 | ✅ | ✅ | ✅（限所辖厂区） | ❌ | ❌ |
| 上传文档 | ✅ | ✅ | ✅ | ❌ | ❌ |
| 使用 QA | ✅ | ✅ | ✅ | ✅（限授权企业） | ❌ |
| 查看审计日志 | ✅ | ❌ | ❌ | ❌ | ✅ |

2. 邀请流程：
   - enterprise_admin 输入被邀请人邮箱 → 后端生成一次性 invite link（JWT，24h 有效）→ 邮件占位（开发期打印到日志）。
   - 被邀请人点击 link → 跳转 Keycloak 注册 → 自动加入企业并绑定角色。
3. 测试：

```bash
.venv/bin/python -m pytest tests/test_f1_invitation.py -v
# 期望：全部通过
```

4. 产物：`src/platform_foundation/f1/invitation.py`、邀请页面、测试。

## 任务 8：任务恢复与幂等

1. 上传任务支持恢复：
   - 服务重启后，RQ worker 自动消费 `f1.upload_task` 中状态为 pending/scanning/indexing 的任务。
   - 同一 object_key 重复索引时，RAGFlow 侧先删后建（幂等）。
2. QA 调用支持幂等重试：
   - 客户端可带 `request_id`（UUID），服务端缓存结果 5 分钟。
3. 测试：

```bash
.venv/bin/python -m pytest tests/test_f1_recovery.py -v
# 期望：全部通过
```

4. 产物：恢复逻辑、测试。

## 任务 9：审计后台与管理后台

1. 管理后台页面：
   - 企业列表、禁用/启用。
   - 用户列表、角色调整（super_admin 操作）。
   - 系统级审计日志查询。
2. 审计后台页面：
   - auditor 查看本企业/全平台操作日志。
   - 支持按时间、用户、资源类型过滤。
3. 后端 `src/platform_foundation/f1/audit.py`：
   - 统一 `log_event(...)` 函数。
   - 所有 API 写操作自动记录。
4. 测试：

```bash
.venv/bin/python -m pytest tests/test_f1_audit.py -v
# 期望：全部通过
```

5. 产物：`src/platform_foundation/f1/audit.py`、管理后台页面、审计后台页面、测试。

## 任务 10：产物与收口

1. 生成 `artifacts/f1-platform-shell/v0.1/platform_shell.json` + `platform_shell.md`（0700/0600）：
   - 架构图、端口矩阵、权限矩阵、镜像 digest、OTel 端点。
   - 结论字段：`PLATFORM_SHELL_READY_FIXTURE_ONLY`。
   - 固定声明：`NOT_PRODUCTION / FIXTURE_ONLY / CHAT_UI_NOT_BUILT / PROFESSIONAL_JUDGMENT_REQUIRED / ACCURACY_NOT_EVALUATED`。

2. 生成脚本示例（`src/platform_foundation/f1/artifacts.py` 或直接命令）：

```bash
mkdir -p artifacts/f1-platform-shell/v0.1
.venv/bin/python - <<'PY'
import json, hashlib, subprocess, datetime
from pathlib import Path

port_matrix = {
    "keycloak": 8080,
    "minio_api": 9000, "minio_console": 9001,
    "redis": 6379,
    "prometheus": 9090,
    "grafana": 3000,
    "jaeger_ui": 16686,
    "otel_grpc": 4317,
    "otel_http": 4318,
    "fastapi": 8000,
    "web": 5173,
}

images = json.loads(Path("artifacts/f1-platform-shell/v0.1/images.json").read_text())

payload = {
    "schema": "f1-platform-shell-v1",
    "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
    "conclusion": "PLATFORM_SHELL_READY_FIXTURE_ONLY",
    "declarations": [
        "NOT_PRODUCTION",
        "FIXTURE_ONLY",
        "CHAT_UI_NOT_BUILT",
        "PROFESSIONAL_JUDGMENT_REQUIRED",
        "ACCURACY_NOT_EVALUATED",
    ],
    "port_matrix": port_matrix,
    "images": images,
    "role_matrix": {
        "super_admin": ["create_enterprise", "manage_users", "manage_plants", "upload", "use_qa", "view_audit"],
        "enterprise_admin": ["manage_users", "manage_plants", "upload", "use_qa"],
        "plant_admin": ["manage_plants_owned", "upload", "use_qa"],
        "partner": ["use_qa_authorized"],
        "auditor": ["view_audit"],
    },
}

out = Path("artifacts/f1-platform-shell/v0.1/platform_shell.json")
out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
out.chmod(0o600)
print(out)
PY
```

3. 连续生成两次并比较 SHA：

```bash
sha256sum artifacts/f1-platform-shell/v0.1/platform_shell.json > /tmp/f1_sha1.txt
# 再运行一次生成脚本
sha256sum artifacts/f1-platform-shell/v0.1/platform_shell.json > /tmp/f1_sha2.txt
diff /tmp/f1_sha1.txt /tmp/f1_sha2.txt
# 期望：无输出
```

4. 卫生扫描：

```bash
grep -R -E '(password|secret|dsn|postgresql://|/Users/)' artifacts/f1-platform-shell/v0.1/ || true
# 期望无匹配（排除预期字段如 images.json 中的官方镜像名）
```

5. 全仓回归：

```bash
.venv/bin/python -m pytest tests/ -q
# 期望最后一行：Ran 690+N+M tests / OK
```

6. 零残留验证：

```bash
cd infra/f1
docker compose down -v
docker ps -a --format '{{.Names}}' | grep '^anhuan-f1-' || true
docker volume ls -q | grep '^anhuan-f1-' || true
docker network ls -q | grep '^anhuan-f1-' || true
# 期望均输出为空
```

7. PROGRESS/BLOCKED 更新；未开任何闸门；未宣称生产可用。

## 规矩

禁止 skip/todo（除栈缺失明确 skip）、mock 被测引擎、删改旧测试、放宽阈值、改冻结件、吞异常、`|| true` 假绿。
同一验收连败 3 次换项；全书最多 8 轮；第 8 轮如实交付卡点与半成品。
每条回执贴实际命令输出（含红→绿）；失败输出先脱敏再贴。

## 完成条件

1. `infra/f1/docker-compose.yml` 一键启动 Keycloak/MinIO/Redis/FastAPI/前端/OTel/Prometheus/Grafana/Jaeger，全部 healthy。
2. OIDC 登录可用，角色权限矩阵落地并通过测试。
3. 文件可上传至 MinIO，异步索引到 RAGFlow，任务状态可追踪、可恢复。
4. 前端可完成登录、企业/厂区/用户/文档/QA/审计日志页面浏览。
5. 所有 API 写操作记录审计日志。
6. OpenTelemetry trace/metric 可在 Grafana/Jaeger 中查看。
7. PostgreSQL `f0i.*` 结构与 F0-I 产物零漂移；`f1.*` schema 通过 Alembic 管理。
8. 栈可拆除/重建；产物双跑 SHA 一致、卫生扫描全 0、结论字段合法。
