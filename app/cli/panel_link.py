"""Connect or disconnect a Pasarguard panel on this bot instance.

The Bot Builder runs this inside a customer's bot container after it has created an admin for the
customer on the master panel, so the bot is connected to that panel without the customer typing
anything::

    echo '{"action": "link", ...}' | docker exec -i <container> uv run python -m app.cli.panel_link

The request is one JSON object on stdin (credentials never appear in a process list). The answer is
the last stdout line, ``RESULT {json}``; anything printed before it is log noise.

Actions
    ping                                         readiness check
    link     name, base_url, auth_type, username, secret, group_ids, user_limit
    enable / disable   base_url, username        toggle the panel without deleting it
    unlink             base_url, username        remove the panel
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

RESULT_PREFIX = "RESULT "


class CliError(Exception):
    """A problem with the request itself; the message is returned to the caller."""


def _normalise_url(value: Any) -> str:
    url = str(value or "").strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise CliError("base_url must start with http:// or https://")
    return url


async def _find_panel(base_url: str, username: str):
    from app.db.crud.panels import PanelsManager

    wanted = (base_url, username[:50])
    for panel in await PanelsManager().get_all_panels():
        if (str(panel.base_url).rstrip("/"), str(panel.username)) == wanted:
            return panel
    return None


async def _link(payload: dict[str, Any]) -> dict[str, Any]:
    from app.db.crud.panels import PanelsManager
    from app.services.panels.auth import verify_panel_api_key, verify_panel_password
    from app.utils.security.crypto import encrypt_data

    base_url = _normalise_url(payload.get("base_url"))
    auth_type = "api_key" if payload.get("auth_type") == "api_key" else "password"
    username = str(payload.get("username") or "").strip()
    secret = str(payload.get("secret") or "")
    name = str(payload.get("name") or "Panel").strip()[:50] or "Panel"
    if not secret or (auth_type == "password" and not username):
        raise CliError("username and secret are required")
    group_ids = [int(g) for g in payload.get("group_ids") or []]
    user_limit = int(payload["user_limit"]) if payload.get("user_limit") else None

    if auth_type == "api_key":
        _, groups = await verify_panel_api_key(base_url, secret)
        cookie, stored_username = secret.strip(), "-"
    else:
        _, cookie, groups = await verify_panel_password(base_url, username, secret)
        stored_username = username[:50]

    if not group_ids:  # no explicit choice: the customer's bot may use every group of the panel
        group_ids = [int(g.id) for g in getattr(groups, "groups", None) or []]

    existing = await _find_panel(base_url, stored_username)
    manager = PanelsManager()
    if existing is not None:
        await manager.update_panel(
            code=existing.code,
            name=name,
            enable=True,
            password=encrypt_data(secret),
            cookie=cookie,
            auth_type=auth_type,
        )
        return {"ok": True, "panel_code": int(existing.code), "created": False}

    codes = [int(p.code) for p in await manager.get_all_panels()]
    code = max(codes, default=0) + 1
    await manager.add_panel(
        code=code,
        name=name,
        enable=True,
        base_url=base_url,
        username=stored_username,
        password=encrypt_data(secret),
        cookie=cookie,
        default_group_ids=group_ids or None,
        user_limit=user_limit,
        auth_type=auth_type,
    )
    return {"ok": True, "panel_code": code, "created": True}


async def _toggle(payload: dict[str, Any], *, enable: bool) -> dict[str, Any]:
    from app.db.crud.panels import PanelsManager

    panel = await _find_panel(
        _normalise_url(payload.get("base_url")), str(payload.get("username") or "").strip() or "-"
    )
    if panel is None:
        return {"ok": False, "error": "panel not found on this bot"}
    await PanelsManager().update_panel(code=panel.code, enable=enable)
    return {"ok": True, "panel_code": int(panel.code)}


async def _unlink(payload: dict[str, Any]) -> dict[str, Any]:
    from app.db.crud.panels import PanelsManager

    panel = await _find_panel(
        _normalise_url(payload.get("base_url")), str(payload.get("username") or "").strip() or "-"
    )
    if panel is None:
        return {"ok": True, "panel_code": None}  # nothing to remove is not a failure
    await PanelsManager().delete_panel(panel.code)
    return {"ok": True, "panel_code": int(panel.code)}


async def run(payload: dict[str, Any]) -> dict[str, Any]:
    action = payload.get("action")
    if action == "ping":
        return {"ok": True}

    from app.db.crud.secrets import ensure_secrets

    await ensure_secrets()  # loads CRYPTO_KEY, needed to encrypt the panel password
    if action == "link":
        return await _link(payload)
    if action == "enable":
        return await _toggle(payload, enable=True)
    if action == "disable":
        return await _toggle(payload, enable=False)
    if action == "unlink":
        return await _unlink(payload)
    raise CliError(f"unknown action: {action!r}")


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            raise CliError("the request must be a JSON object")
        result = asyncio.run(run(payload))
    except CliError as exc:
        result = {"ok": False, "error": str(exc)}
    except Exception as exc:  # panel unreachable, wrong credentials, database problem...
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:400]}
    print(RESULT_PREFIX + json.dumps(result), flush=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
