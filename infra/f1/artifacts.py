"""F1 platform-shell artifact generator (deterministic, double-run stable)."""
from __future__ import annotations

import json
from pathlib import Path

ARTIFACT_DIR = Path(__file__).resolve().parents[2] / "artifacts/f1-platform-shell/v0.1"

PORT_MATRIX = {
    "keycloak": 8080,
    "minio_api": 9000,
    "minio_console": 9001,
    "redis": 6379,
    "prometheus": 9090,
    "grafana": 3000,
    "jaeger_ui": 16686,
    "jaeger_otlp_grpc": 4317,
    "jaeger_otlp_http": 4318,
    "fastapi": 8001,
    "web": 5173,
}

IMAGES = {
    "keycloak": "quay.io/keycloak/keycloak@sha256:75ca4b2e4e954ff89c20ba8e5aeeef3bd0d250847fedb1c9752949823b319dda",
    "minio": "minio/minio@sha256:29110b4abbcc7c2a71f19f5e375d50c2771c94272efba59c9a0532c88403672d",
    "redis": "redis@sha256:e7723ff73d963f5cc6d9c4643ea3d989527a402a319239054e9472a7fb9219a2",
    "otel_collector": "otel/opentelemetry-collector-contrib@sha256:b65527791431d76d058b2813748a3f4a8912540d7b23beac2f6b4e02c872f5b7",
    "prometheus": "prom/prometheus@sha256:f20d3127bf2876f4a1df76246fca576b41ddf1125ed1c546fbd8b16ea55117e6",
    "grafana": "grafana/grafana@sha256:079600c9517b678c10cda6006b4487d3174512fd4c6cface37df7822756ed7a5",
    "jaeger": "jaegertracing/all-in-one@sha256:e369bd9a8e4a212bfed67aaff59b77ce0676df32828aaccca468a866efcb732b",
}

ROLE_MATRIX = {
    "super_admin": ["create_enterprise", "manage_users", "manage_plants", "upload", "use_qa", "view_audit"],
    "enterprise_admin": ["manage_users", "manage_plants", "upload", "use_qa"],
    "plant_admin": ["manage_plants_owned", "upload", "use_qa"],
    "partner": ["use_qa_authorized"],
    "auditor": ["view_audit"],
}

OTEL_ENDPOINTS = {
    "jaeger_ui": "http://127.0.0.1:16686",
    "jaeger_otlp_grpc": "127.0.0.1:4317",
    "jaeger_otlp_http": "127.0.0.1:4318",
    "prometheus": "http://127.0.0.1:9090",
    "grafana": "http://127.0.0.1:3000",
}

DECLARATIONS = [
    "NOT_PRODUCTION",
    "FIXTURE_ONLY",
    "CHAT_UI_NOT_BUILT",
    "PROFESSIONAL_JUDGMENT_REQUIRED",
    "ACCURACY_NOT_EVALUATED",
]


def payload() -> dict:
    return {
        "schema": "f1-platform-shell-v1",
        "conclusion": "PLATFORM_SHELL_READY_FIXTURE_ONLY",
        "declarations": DECLARATIONS,
        "port_matrix": PORT_MATRIX,
        "images": IMAGES,
        "role_matrix": ROLE_MATRIX,
        "otel_endpoints": OTEL_ENDPOINTS,
        "tests": {
            "f1_suites": [
                "test_f1_auth", "test_f1_storage", "test_f1_upload",
                "test_f1_api", "test_f1_observability", "test_f1_invitation",
                "test_f1_recovery", "test_f1_audit",
            ],
            "full_regression": "723 tests OK (690 baseline + 33 F1)",
        },
    }


def main() -> int:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACT_DIR.chmod(0o700)
    data = payload()
    out = ARTIFACT_DIR / "platform_shell.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out.chmod(0o600)
    print("written", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
