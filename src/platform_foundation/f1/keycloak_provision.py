"""One-shot fixture identity password provisioning for a clean Keycloak.

The tracked realm contains stable user ids and no credentials.  This module
runs only as a Compose init service, reads one 0600 file per identity, obtains
an in-memory admin token and resets each password through Keycloak Admin REST.
Neither tokens nor request bodies are emitted on failures.
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ContextManager, Protocol


class ProvisionError(RuntimeError):
    """A deliberately body-free provisioning reason code."""


@dataclass(frozen=True, slots=True)
class Identity:
    user_id: str
    username: str
    email: str
    password_file: str
    first_name: str
    last_name: str


IDENTITIES = (
    Identity(
        "d561ffe2-3be8-40cc-a87e-598dd7d84758",
        "admin@anhuan.local",
        "admin@fixture.invalid",
        "oidc_admin_anhuan_local",
        "Local",
        "Administrator",
    ),
    Identity(
        "f1f70ce5-465f-489c-a89d-974a63216ab4",
        "tester",
        "tester@fixture.invalid",
        "oidc_tester",
        "Local",
        "Partner",
    ),
    Identity(
        "db906685-6906-4bc4-9d3a-9011975fd132",
        "tenant-a",
        "tenant-a@fixture.invalid",
        "oidc_tenant_a",
        "Local",
        "Enterprise",
    ),
    Identity(
        "ddc4e27e-ccde-4c89-958f-798fc8f30175",
        "tenant-b",
        "tenant-b@fixture.invalid",
        "oidc_tenant_b",
        "Local",
        "Enterprise",
    ),
    Identity(
        "6f735662-672f-4aeb-9234-9a3390392f33",
        "invitee",
        "invitee@fixture.invalid",
        "oidc_invitee",
        "Local",
        "Invitee",
    ),
    Identity(
        "7e9978c7-106f-4221-a6d7-79e8104a659b",
        "auditor",
        "auditor@fixture.invalid",
        "oidc_auditor",
        "Local",
        "Auditor",
    ),
)

LOCAL_ENGINEERING_IDENTITY = Identity(
    "3247dddb-69bc-4ad1-841c-8fc338b603ce",
    "employee",
    "employee@fixture.invalid",
    "oidc_employee",
    "Local",
    "Employee",
)

_SECRET_NAME = re.compile(r"[a-z0-9_]{1,64}\Z")
_USER_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_WEB_CLIENT_ID = "anhuan-web"


def _configured_identities() -> tuple[Identity, ...]:
    mode = os.environ.get("F1_LOCAL_ENGINEERING", "").strip()
    if mode == "1":
        return (*IDENTITIES, LOCAL_ENGINEERING_IDENTITY)
    if mode:
        raise ProvisionError("LOCAL_ENGINEERING_MODE_INVALID")
    return IDENTITIES


class _Response(Protocol):
    status: int

    def read(self, amount: int = -1) -> bytes: ...


Open = Callable[..., ContextManager[_Response]]


def _read_secret(directory: Path, name: str) -> str:
    if not directory.is_absolute() or not _SECRET_NAME.fullmatch(name):
        raise ProvisionError("SECRET_FILE_INVALID")
    try:
        directory_info = directory.lstat()
        path = directory / name
        info = path.lstat()
        if (
            not stat.S_ISDIR(directory_info.st_mode)
            or stat.S_ISLNK(directory_info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid()
            or info.st_size < 1
            or info.st_size > 4096
        ):
            raise ProvisionError("SECRET_FILE_INVALID")
        raw = path.read_bytes()
    except (OSError, ProvisionError):
        raise ProvisionError("SECRET_FILE_INVALID") from None
    try:
        value = raw.decode("utf-8").rstrip("\r\n")
    except UnicodeDecodeError:
        raise ProvisionError("SECRET_FILE_INVALID") from None
    if not value or "\x00" in value or "\n" in value or "\r" in value:
        raise ProvisionError("SECRET_FILE_INVALID")
    return value


def _endpoint() -> str:
    value = os.environ.get("KEYCLOAK_URL", "http://keycloak:8080").rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ProvisionError("IDP_ENDPOINT_INVALID")
    return value


def _web_public_origin() -> str:
    raw = os.environ.get("F1_WEB_PUBLIC_ORIGIN", "").strip().rstrip("/")
    if not raw:
        raise ProvisionError("WEB_PUBLIC_ORIGIN_REQUIRED")
    try:
        parsed = urllib.parse.urlsplit(raw)
        parsed.port
    except ValueError:
        raise ProvisionError("WEB_PUBLIC_ORIGIN_INVALID") from None
    if (
        not raw.isascii()
        or any(character.isspace() for character in raw)
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ProvisionError("WEB_PUBLIC_ORIGIN_INVALID")
    return raw


def _call(
    opener: Open,
    url: str,
    *,
    method: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    expected: frozenset[int],
) -> bytes:
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers=headers or {},
    )
    try:
        with opener(request, timeout=15) as response:
            if response.status not in expected:
                raise ProvisionError("IDP_HTTP_REJECTED")
            return response.read(262145)
    except ProvisionError:
        raise
    except (OSError, urllib.error.HTTPError, urllib.error.URLError):
        raise ProvisionError("IDP_UNAVAILABLE") from None


def _admin_token(
    base_url: str,
    admin_password: str,
    *,
    opener: Open,
) -> str:
    body = urllib.parse.urlencode(
        {
            "client_id": "admin-cli",
            "grant_type": "password",
            "username": os.environ.get("KEYCLOAK_ADMIN", "admin"),
            "password": admin_password,
        }
    ).encode("utf-8")
    raw = _call(
        opener,
        base_url + "/realms/master/protocol/openid-connect/token",
        method="POST",
        body=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        expected=frozenset({200}),
    )
    try:
        payload = json.loads(raw)
        token = payload.get("access_token")
    except (json.JSONDecodeError, AttributeError):
        raise ProvisionError("IDP_TOKEN_INVALID") from None
    if not isinstance(token, str) or not token or len(token) > 16384:
        raise ProvisionError("IDP_TOKEN_INVALID")
    return token


def _verify_and_set_password(
    base_url: str,
    realm: str,
    identity: Identity,
    password: str,
    token: str,
    *,
    opener: Open,
    ensure_profile: bool = False,
) -> None:
    if not _USER_ID.fullmatch(identity.user_id):
        raise ProvisionError("IDP_IDENTITY_CONFIG_INVALID")
    user_url = (
        base_url
        + "/admin/realms/"
        + urllib.parse.quote(realm, safe="")
        + "/users/"
        + identity.user_id
    )
    auth = {"Authorization": "Bearer " + token}
    raw = _call(
        opener,
        user_url,
        method="GET",
        headers=auth,
        expected=frozenset({200}),
    )
    try:
        user = json.loads(raw)
    except json.JSONDecodeError:
        raise ProvisionError("IDP_USER_RESPONSE_INVALID") from None
    if not isinstance(user, dict) or any(
        user.get(field) != expected
        for field, expected in (
            ("id", identity.user_id),
            ("username", identity.username),
            ("email", identity.email),
        )
    ):
        raise ProvisionError("IDP_USER_MISMATCH")
    if ensure_profile and (
        user.get("firstName") != identity.first_name
        or user.get("lastName") != identity.last_name
    ):
        update = dict(user)
        update["firstName"] = identity.first_name
        update["lastName"] = identity.last_name
        _call(
            opener,
            user_url,
            method="PUT",
            body=json.dumps(
                update, separators=(",", ":"), sort_keys=True
            ).encode("utf-8"),
            headers={**auth, "Content-Type": "application/json"},
            expected=frozenset({204}),
        )
    credential = json.dumps(
        {"type": "password", "value": password, "temporary": False},
        separators=(",", ":"),
    ).encode("utf-8")
    _call(
        opener,
        user_url + "/reset-password",
        method="PUT",
        body=credential,
        headers={**auth, "Content-Type": "application/json"},
        expected=frozenset({204}),
    )


def _configure_web_client(
    base_url: str,
    realm: str,
    origin: str,
    token: str,
    *,
    opener: Open,
) -> None:
    realm_url = base_url + "/admin/realms/" + urllib.parse.quote(realm, safe="")
    auth = {"Authorization": "Bearer " + token}
    raw = _call(
        opener,
        realm_url + "/clients?clientId=" + _WEB_CLIENT_ID,
        method="GET",
        headers=auth,
        expected=frozenset({200}),
    )
    try:
        matches = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ProvisionError("IDP_WEB_CLIENT_RESPONSE_INVALID") from None
    if (
        not isinstance(matches, list)
        or len(matches) != 1
        or not isinstance(matches[0], dict)
    ):
        raise ProvisionError("IDP_WEB_CLIENT_MISMATCH")
    client = matches[0]
    client_id = client.get("id")
    expected = (
        ("clientId", _WEB_CLIENT_ID),
        ("enabled", True),
        ("publicClient", True),
        ("bearerOnly", False),
        ("standardFlowEnabled", True),
        ("protocol", "openid-connect"),
    )
    if (
        not isinstance(client_id, str)
        or not _USER_ID.fullmatch(client_id)
        or any(client.get(field) != value for field, value in expected)
        or bool(client.get("secret"))
    ):
        raise ProvisionError("IDP_WEB_CLIENT_MISMATCH")

    update = dict(client)
    update["redirectUris"] = [origin, origin + "/*"]
    update["webOrigins"] = [origin]
    body = json.dumps(update, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    _call(
        opener,
        realm_url + "/clients/" + client_id,
        method="PUT",
        body=body,
        headers={**auth, "Content-Type": "application/json"},
        expected=frozenset({204}),
    )


def provision(*, opener: Open = urllib.request.urlopen) -> None:
    raw_directory = os.environ.get("F1_SECRETS_DIR", "")
    if not raw_directory:
        raise ProvisionError("SECRET_DIRECTORY_REQUIRED")
    directory = Path(raw_directory)
    admin_password = _read_secret(directory, "keycloak_admin_password")
    identities = _configured_identities()
    passwords = {
        identity.user_id: _read_secret(directory, identity.password_file)
        for identity in identities
    }
    if (
        any(len(password) < 24 for password in passwords.values())
        or len(set(passwords.values())) != len(passwords)
        or admin_password in passwords.values()
    ):
        raise ProvisionError("IDENTITY_PASSWORD_POLICY_REJECTED")

    base_url = _endpoint()
    web_origin = _web_public_origin()
    realm = os.environ.get("F1_KEYCLOAK_REALM", "anhuan")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", realm):
        raise ProvisionError("IDP_REALM_INVALID")

    token: str | None = None
    for attempt in range(60):
        try:
            token = _admin_token(base_url, admin_password, opener=opener)
            break
        except ProvisionError as error:
            if str(error) != "IDP_UNAVAILABLE" or attempt == 59:
                raise
            time.sleep(1)
    if token is None:
        raise ProvisionError("IDP_UNAVAILABLE")

    _configure_web_client(
        base_url,
        realm,
        web_origin,
        token,
        opener=opener,
    )

    ensure_profiles = identities != IDENTITIES
    for identity in identities:
        _verify_and_set_password(
            base_url,
            realm,
            identity,
            passwords[identity.user_id],
            token,
            opener=opener,
            ensure_profile=ensure_profiles,
        )


def main() -> int:
    try:
        provision()
    except ProvisionError as error:
        print(str(error), file=sys.stderr)
        return 1
    print("KEYCLOAK_FIXTURE_IDENTITIES_READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
