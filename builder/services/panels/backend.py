"""Talking to a Pasarguard master panel: admin lifecycle for the accounts the builder sells.

``PanelBackend`` is the seam: the builder logic depends on it, tests use a fake, and
``PasarguardBackend`` is the real implementation built on the ``pasarguard`` client (the same
library the bot itself uses for its reseller feature).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

from builder.models import MasterPanel, PanelAuth
from builder.security import SecretBox


class PanelError(RuntimeError):
    """The master panel refused or could not be reached; the message is safe to show to admins."""


@dataclass(frozen=True)
class AdminInfo:
    id: int
    username: str
    status: str = "active"
    used_bytes: int = 0
    data_limit: int = 0
    is_limited: bool = False
    is_disabled: bool = False
    total_users: int = 0


@dataclass(frozen=True)
class PanelCheck:
    """What the panel offers when a builder admin connects it: roles and groups to choose from."""

    roles: list[tuple[int, str]] = field(default_factory=list)
    groups: list[tuple[int, str]] = field(default_factory=list)


class PanelBackend(Protocol):
    async def check(self, master: MasterPanel) -> PanelCheck: ...

    async def create_admin(
        self,
        master: MasterPanel,
        *,
        username: str,
        password: str,
        role_id: int,
        data_limit: int,
        max_users: int,
        note: str,
    ) -> AdminInfo: ...

    async def get_admin(self, master: MasterPanel, admin_id: int) -> AdminInfo | None: ...

    async def find_admin(self, master: MasterPanel, username: str) -> AdminInfo | None: ...

    async def modify_admin(
        self,
        master: MasterPanel,
        admin_id: int,
        *,
        status: str | None = None,
        data_limit: int | None = None,
        password: str | None = None,
        max_users: int | None = None,
    ) -> AdminInfo: ...

    async def reset_usage(self, master: MasterPanel, admin_id: int) -> AdminInfo: ...

    async def remove_admin(self, master: MasterPanel, admin_id: int) -> None: ...


_TOKEN_TTL = 20 * 60  # re-login before a typical JWT lifetime runs out


class PasarguardBackend:
    def __init__(self, box: SecretBox):
        self._box = box
        self._tokens: dict[int, tuple[str, float]] = {}

    # ── plumbing ──────────────────────────────────────────────────────────────
    def _secret(self, master: MasterPanel) -> str:
        return self._box.decrypt(master.secret_enc)

    async def _token(self, master: MasterPanel, *, refresh: bool = False) -> str:
        from pasarguard import PasarguardAPI

        if master.auth_type == PanelAuth.API_KEY:
            return self._secret(master)
        cached = self._tokens.get(master.id)
        if cached and not refresh and cached[1] > time.monotonic():
            return cached[0]
        api = PasarguardAPI(base_url=master.base_url)
        token = await api.get_token(username=master.username, password=self._secret(master))
        self._tokens[master.id] = (token.access_token, time.monotonic() + _TOKEN_TTL)
        return token.access_token

    @staticmethod
    def _api(master: MasterPanel, token: str):
        from pasarguard import PasarguardAPI

        if master.auth_type == PanelAuth.API_KEY:
            return PasarguardAPI(base_url=master.base_url, api_key=token)
        return PasarguardAPI(base_url=master.base_url, token=token)

    @staticmethod
    def _error(exc: Exception) -> PanelError:
        import httpx
        from pydantic import ValidationError

        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            known = {
                401: "unauthorized: check the panel username/password or API key",
                403: "forbidden: the panel account lacks permission",
                404: "not found: check the panel URL",
                409: "conflict: the username already exists on the panel",
                422: "the panel rejected the data (password policy / limits?)",
            }
            detail = (exc.response.text or "").strip()[:160]
            return PanelError(known.get(status) or f"panel returned HTTP {status}: {detail}")
        if isinstance(exc, httpx.TimeoutException):
            return PanelError("the panel did not answer in time")
        if isinstance(exc, httpx.HTTPError):
            return PanelError(f"could not connect to the panel ({type(exc).__name__})")
        if isinstance(exc, ValidationError):
            return PanelError("unexpected answer from the panel (version mismatch?)")
        return PanelError(str(exc) or type(exc).__name__)

    async def _run(self, master: MasterPanel, operation):
        import httpx
        from pydantic import ValidationError

        try:
            token = await self._token(master)
            try:
                return await operation(self._api(master, token), token)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 401 and master.auth_type == PanelAuth.PASSWORD:
                    token = await self._token(master, refresh=True)
                    return await operation(self._api(master, token), token)
                raise
        except (httpx.HTTPError, ValidationError) as exc:
            raise self._error(exc) from exc

    @staticmethod
    def _info(admin) -> AdminInfo:
        return AdminInfo(
            id=int(admin.id),
            username=str(admin.username),
            status=str(getattr(admin, "status", "") or "active"),
            used_bytes=int(getattr(admin, "used_traffic", 0) or 0),
            data_limit=int(getattr(admin, "data_limit", 0) or 0),
            is_limited=bool(getattr(admin, "is_limited", False)),
            is_disabled=bool(getattr(admin, "is_disabled", False)),
            total_users=int(getattr(admin, "total_users", 0) or 0),
        )

    # ── operations ────────────────────────────────────────────────────────────
    async def check(self, master: MasterPanel) -> PanelCheck:
        async def operation(api, token):
            roles = await api.get_roles_simple(token=token)
            groups = await api.get_groups_simple(token=token, all=True)
            return PanelCheck(
                roles=[(int(r.id), str(r.name)) for r in roles.roles or []],
                groups=[(int(g.id), str(g.name)) for g in groups.groups or []],
            )

        return await self._run(master, operation)

    async def create_admin(
        self,
        master: MasterPanel,
        *,
        username: str,
        password: str,
        role_id: int,
        data_limit: int,
        max_users: int,
        note: str,
    ) -> AdminInfo:
        from pasarguard import AdminCreate, RoleLimits

        kwargs: dict = {"username": username, "password": password, "role_id": role_id, "note": note}
        if data_limit > 0:
            kwargs["data_limit"] = data_limit
        if max_users > 0:
            kwargs["permission_overrides"] = RoleLimits(max_users=max_users)

        async def operation(api, token):
            return self._info(await api.create_admin(admin=AdminCreate(**kwargs), token=token))

        return await self._run(master, operation)

    async def get_admin(self, master: MasterPanel, admin_id: int) -> AdminInfo | None:
        async def operation(api, token):
            response = await api.get_admins(token=token, ids=[admin_id], limit=1)
            return next((a for a in response.admins or [] if int(a.id) == admin_id), None)

        found = await self._run(master, operation)
        return self._info(found) if found is not None else None

    async def find_admin(self, master: MasterPanel, username: str) -> AdminInfo | None:
        async def operation(api, token):
            response = await api.get_admins(token=token, usernames=[username], limit=10)
            return next((a for a in response.admins or [] if str(a.username).lower() == username.lower()), None)

        found = await self._run(master, operation)
        return self._info(found) if found is not None else None

    async def modify_admin(
        self,
        master: MasterPanel,
        admin_id: int,
        *,
        status: str | None = None,
        data_limit: int | None = None,
        password: str | None = None,
        max_users: int | None = None,
    ) -> AdminInfo:
        from pasarguard import AdminModify, RoleLimits

        changes: dict = {
            key: value
            for key, value in {"status": status, "data_limit": data_limit, "password": password}.items()
            if value is not None
        }
        if max_users is not None:
            changes["permission_overrides"] = RoleLimits(max_users=max_users if max_users > 0 else None)

        async def operation(api, token):
            return self._info(
                await api.modify_admin_by_id(admin_id=admin_id, admin=AdminModify(**changes), token=token)
            )

        return await self._run(master, operation)

    async def reset_usage(self, master: MasterPanel, admin_id: int) -> AdminInfo:
        async def operation(api, token):
            return self._info(await api.reset_admin_usage_by_id(admin_id=admin_id, token=token))

        return await self._run(master, operation)

    async def remove_admin(self, master: MasterPanel, admin_id: int) -> None:
        async def operation(api, token):
            await api.remove_admin_by_id(admin_id=admin_id, token=token)

        await self._run(master, operation)
