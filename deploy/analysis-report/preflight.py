"""Render and preflight Netlify config for the analysis-report test frontend.

Generated files must be written outside the repository. This module does not
deploy, upload, or contact Netlify.
"""
from __future__ import annotations

import argparse
import ipaddress
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

DEPLOY_DIR = Path(__file__).resolve().parent
REPO_ROOT = DEPLOY_DIR.parents[1]
TEMPLATE_NAME = "netlify.toml.template"
PLACEHOLDER_EDGE = "__EDGE_ORIGIN__"

REQUIRED_CONFIG_KEYS = frozenset({"NETLIFY_ORIGIN", "EDGE_ORIGIN"})
ALLOWED_CONFIG_KEYS = frozenset({"NETLIFY_ORIGIN", "EDGE_ORIGIN", "ENVIRONMENT_NAME"})
VITE_SECRET_KEY_RE = re.compile(r"^VITE_.*(?:SECRET|TOKEN|KEY).*$", re.IGNORECASE)
PLACEHOLDER_RE = re.compile(r"__[A-Z][A-Z0-9_]*__")
DNS_HOST_RE = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)
MOCK_ENABLE_RE = re.compile(
    r"VITE_MATERIAL_RAG_REPORT_MOCK\s*=\s*['\"]?(?:1|true|yes)['\"]?",
    re.IGNORECASE,
)
SECRET_RES = (
    re.compile(r"Bearer\s+\S+", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+\."),
    re.compile(r"password\s*[:=]", re.IGNORECASE),
    re.compile(r"api[_-]?key\s*[:=]", re.IGNORECASE),
    re.compile(r"ARK_API_KEY", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
)
LOOPBACK_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
}
EXPECTED_FROM = ("/api/*", "/realms/*", "/resources/*", "/*")
PROXY_TO_SUFFIX = {
    "/api/*": "/api/:splat",
    "/realms/*": "/realms/:splat",
    "/resources/*": "/resources/:splat",
}


class PreflightError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(code if not detail else f"{code}: {detail}")


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _reject_secret_text(text: str) -> None:
    for pattern in SECRET_RES:
        if pattern.search(text):
            raise PreflightError("LOCAL_DEPLOY_SECRET_PATTERN")
    if MOCK_ENABLE_RE.search(text):
        raise PreflightError("LOCAL_DEPLOY_MOCK_ENABLED")


def _validate_config_keys(config: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(config, Mapping):
        raise PreflightError("LOCAL_DEPLOY_CONFIG_KEYS_INVALID")
    raw_keys = list(config.keys())
    for key in raw_keys:
        if not isinstance(key, str):
            raise PreflightError("LOCAL_DEPLOY_CONFIG_KEYS_INVALID")
        if VITE_SECRET_KEY_RE.fullmatch(key):
            raise PreflightError("LOCAL_DEPLOY_VITE_SECRET_KEY")
    missing = REQUIRED_CONFIG_KEYS.difference(raw_keys)
    extra = set(raw_keys).difference(ALLOWED_CONFIG_KEYS)
    if missing or extra:
        raise PreflightError("LOCAL_DEPLOY_CONFIG_KEYS_INVALID")
    normalized: dict[str, str] = {}
    for key in raw_keys:
        value = config[key]
        if not isinstance(value, str):
            raise PreflightError("LOCAL_DEPLOY_CONFIG_KEYS_INVALID")
        stripped = value.strip()
        if key in REQUIRED_CONFIG_KEYS and not stripped:
            raise PreflightError("LOCAL_DEPLOY_ORIGIN_EMPTY")
        if key == "ENVIRONMENT_NAME" and stripped:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", stripped):
                raise PreflightError("LOCAL_DEPLOY_CONFIG_KEYS_INVALID")
        _reject_secret_text(stripped)
        if PLACEHOLDER_RE.search(stripped):
            raise PreflightError("LOCAL_DEPLOY_PLACEHOLDER_RESIDUAL")
        normalized[key] = stripped
    return normalized


def normalize_https_origin(raw: str) -> str:
    if raw is None or not str(raw).strip():
        raise PreflightError("LOCAL_DEPLOY_ORIGIN_EMPTY")
    value = str(raw).strip()
    if PLACEHOLDER_RE.search(value):
        raise PreflightError("LOCAL_DEPLOY_PLACEHOLDER_RESIDUAL")
    parsed = urlparse(value)
    if parsed.scheme != "https":
        raise PreflightError("LOCAL_DEPLOY_ORIGIN_NOT_HTTPS")
    if parsed.username or "@" in (parsed.netloc or ""):
        raise PreflightError("LOCAL_DEPLOY_ORIGIN_INVALID")
    if parsed.query or parsed.fragment:
        raise PreflightError("LOCAL_DEPLOY_ORIGIN_HAS_PATH")
    path = parsed.path or ""
    if path not in ("", "/"):
        raise PreflightError("LOCAL_DEPLOY_ORIGIN_HAS_PATH")
    host = parsed.hostname
    if not host:
        raise PreflightError("LOCAL_DEPLOY_ORIGIN_INVALID")
    try:
        port = parsed.port
    except ValueError as exc:
        raise PreflightError("LOCAL_DEPLOY_ORIGIN_INVALID") from exc
    if port not in (None, 443):
        raise PreflightError("LOCAL_DEPLOY_ORIGIN_INVALID")
    lowered = host.lower().rstrip(".")
    if lowered in LOOPBACK_HOSTS or lowered.endswith(".localhost"):
        raise PreflightError("LOCAL_DEPLOY_ORIGIN_LOOPBACK")
    if lowered.endswith(".local"):
        raise PreflightError("LOCAL_DEPLOY_ORIGIN_LOOPBACK")
    try:
        ip_obj = ipaddress.ip_address(host)
    except ValueError:
        ip_obj = None
    if ip_obj is not None:
        if ip_obj.is_loopback or ip_obj.is_unspecified or ip_obj.is_link_local:
            raise PreflightError("LOCAL_DEPLOY_ORIGIN_LOOPBACK")
        raise PreflightError("LOCAL_DEPLOY_ORIGIN_BARE_IP")
    if not DNS_HOST_RE.fullmatch(lowered):
        raise PreflightError("LOCAL_DEPLOY_ORIGIN_INVALID")
    return f"https://{lowered}"


def parse_redirect_blocks(text: str) -> list[dict[str, str | int]]:
    parts = re.split(r"(?=\[\[redirects\]\])", text)
    blocks: list[dict[str, str | int]] = []
    for part in parts:
        if not part.lstrip().startswith("[[redirects]]"):
            continue
        from_m = re.search(r'(?m)^\s*from\s*=\s*"([^"]*)"\s*$', part)
        to_m = re.search(r'(?m)^\s*to\s*=\s*"([^"]*)"\s*$', part)
        status_m = re.search(r'(?m)^\s*status\s*=\s*(\d+)\s*$', part)
        if from_m is None or to_m is None or status_m is None:
            raise PreflightError("LOCAL_DEPLOY_REWRITE_ORDER_INVALID")
        blocks.append(
            {
                "from": from_m.group(1),
                "to": to_m.group(1),
                "status": int(status_m.group(1)),
            }
        )
    return blocks


def validate_rewrites(text: str, edge_origin: str) -> None:
    blocks = parse_redirect_blocks(text)
    if [block["from"] for block in blocks] != list(EXPECTED_FROM):
        raise PreflightError("LOCAL_DEPLOY_REWRITE_ORDER_INVALID")
    if blocks[-1]["from"] != "/*" or blocks[-1]["to"] != "/index.html":
        raise PreflightError("LOCAL_DEPLOY_SPA_FALLBACK_NOT_LAST")
    for block in blocks:
        if block["status"] != 200:
            raise PreflightError("LOCAL_DEPLOY_REWRITE_ORDER_INVALID")
    for from_rule, suffix in PROXY_TO_SUFFIX.items():
        match = next(block for block in blocks if block["from"] == from_rule)
        expected_to = f"{edge_origin}{suffix}"
        if match["to"] != expected_to:
            raise PreflightError("LOCAL_DEPLOY_REWRITE_ORDER_INVALID")
    spa = blocks[-1]
    if spa["to"] != "/index.html" or spa["from"] != "/*":
        raise PreflightError("LOCAL_DEPLOY_SPA_FALLBACK_NOT_LAST")


def validate_template_text(text: str) -> None:
    if PLACEHOLDER_EDGE not in text:
        raise PreflightError("LOCAL_DEPLOY_TEMPLATE_INVALID")
    validate_rewrites(text, PLACEHOLDER_EDGE)
    leftovers = [token for token in PLACEHOLDER_RE.findall(text) if token != PLACEHOLDER_EDGE]
    if leftovers:
        raise PreflightError("LOCAL_DEPLOY_PLACEHOLDER_RESIDUAL")
    _reject_secret_text(text)
    if "Content-Security-Policy" in text:
        raise PreflightError("LOCAL_DEPLOY_TEMPLATE_INVALID")


def validate_generated_text(text: str, edge_origin: str) -> None:
    leftovers = PLACEHOLDER_RE.findall(text)
    if leftovers:
        raise PreflightError("LOCAL_DEPLOY_PLACEHOLDER_RESIDUAL")
    validate_rewrites(text, edge_origin)
    _reject_secret_text(text)
    if "src/web" not in text or "npm run build" not in text:
        raise PreflightError("LOCAL_DEPLOY_TEMPLATE_INVALID")
    if "noindex, nofollow" not in text or "nosniff" not in text:
        raise PreflightError("LOCAL_DEPLOY_TEMPLATE_INVALID")
    if "Referrer-Policy" not in text:
        raise PreflightError("LOCAL_DEPLOY_TEMPLATE_INVALID")


def render_netlify_toml(
    config: Mapping[str, str],
    output_path: Path,
    *,
    template_path: Path | None = None,
    repo_root: Path | None = None,
) -> str:
    root = Path(repo_root or REPO_ROOT)
    source = Path(template_path or (DEPLOY_DIR / TEMPLATE_NAME))
    if not source.is_file():
        raise PreflightError("LOCAL_DEPLOY_TEMPLATE_INVALID", "template missing")
    normalized = _validate_config_keys(config)
    netlify_origin = normalize_https_origin(normalized["NETLIFY_ORIGIN"])
    edge_origin = normalize_https_origin(normalized["EDGE_ORIGIN"])
    if netlify_origin == edge_origin:
        raise PreflightError("LOCAL_DEPLOY_SAME_ORIGIN_LOOP")
    template = source.read_text(encoding="utf-8")
    validate_template_text(template)
    rendered = template.replace(PLACEHOLDER_EDGE, edge_origin)
    validate_generated_text(rendered, edge_origin)
    destination = Path(output_path)
    if destination.exists() and destination.is_dir():
        raise PreflightError("LOCAL_DEPLOY_OUTPUT_PATH_INVALID")
    if destination.is_symlink():
        raise PreflightError("LOCAL_DEPLOY_OUTPUT_PATH_INVALID")
    # Resolve the parent even if the output file does not exist yet. This also
    # closes a symlinked-parent escape back into the repository.
    resolved_out = destination.parent.resolve() / destination.name
    if resolved_out == source.resolve():
        raise PreflightError("LOCAL_DEPLOY_OUTPUT_PATH_INVALID")
    if _is_inside(resolved_out, root):
        raise PreflightError("LOCAL_DEPLOY_OUTPUT_PATH_INVALID")
    resolved_out.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=resolved_out.parent,
            prefix=f".{resolved_out.name}.",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, resolved_out)
        resolved_out.chmod(0o600)
    except OSError as exc:
        if temporary is not None and temporary.exists():
            temporary.unlink()
        raise PreflightError("LOCAL_DEPLOY_OUTPUT_PATH_INVALID") from exc
    return rendered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render analysis-report Netlify config to an out-of-repo path."
    )
    parser.add_argument("--netlify-origin", required=True)
    parser.add_argument("--edge-origin", required=True)
    parser.add_argument("--environment-name", default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--template", default="")
    args = parser.parse_args(argv)
    config = {
        "NETLIFY_ORIGIN": args.netlify_origin,
        "EDGE_ORIGIN": args.edge_origin,
    }
    if str(args.environment_name).strip():
        config["ENVIRONMENT_NAME"] = args.environment_name
    template_path = Path(args.template) if str(args.template).strip() else None
    try:
        render_netlify_toml(
            config,
            Path(args.output),
            template_path=template_path,
        )
    except PreflightError as exc:
        print(exc.code, file=sys.stderr)
        return 2
    print("NETLIFY_TOML_RENDERED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
