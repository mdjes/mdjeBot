"""Shared builder test fixtures' building blocks (plain helpers, not tests)."""

from __future__ import annotations

from builder.models import Plan
from builder.notify import Notifier
from builder.security import SecretBox
from builder.services.provisioner import CmdResult, Provisioner
from builder.settings import Settings, derive_secret_key


def make_settings(tmp_path, **overrides) -> Settings:
    values = {
        "api_id": 1,
        "api_hash": "hash",
        "bot_token": "1:builder",
        "admin_ids": (100,),
        "database_url": "sqlite+aiosqlite:///:memory:",
        "secret_key": derive_secret_key("test"),
        "tenant_image": "ghcr.io/mdjes/mdjebot:latest",
        "config_dir": str(tmp_path),
        "mariadb_root_password": "rootpw",
    }
    values.update(overrides)
    return Settings(**values)


def make_box(settings: Settings) -> SecretBox:
    return SecretBox(settings.secret_key)


class FakeRunner:
    """Records docker calls; ``responses`` maps the docker verb to a CmdResult."""

    def __init__(self, **responses: CmdResult):
        self.calls: list[tuple[str, ...]] = []
        self.responses = responses

    async def __call__(self, *args: str, timeout: float = 0) -> CmdResult:  # noqa: ASYNC109
        self.calls.append(args)
        verb = args[1] if len(args) > 1 else ""
        if verb == "inspect":
            return self.responses.get("inspect", CmdResult(0, "running\n"))
        return self.responses.get(verb, CmdResult(0, ""))

    def verbs(self) -> list[str]:
        return [call[1] for call in self.calls]


class FakeDbAdmin:
    def __init__(self):
        self.created: list[tuple[str, str, str]] = []
        self.dropped: list[tuple[str, str]] = []

    async def create(self, name: str, user: str, password: str) -> None:
        self.created.append((name, user, password))

    async def drop(self, name: str, user: str) -> None:
        self.dropped.append((name, user))


class FakeNotifier(Notifier):
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send(self, chat_id, text, buttons=None) -> bool:
        self.sent.append((chat_id, text))
        return True

    def to(self, chat_id: int) -> list[str]:
        return [text for cid, text in self.sent if cid == chat_id]


def make_provisioner(settings: Settings, runner: FakeRunner | None = None, db_admin: FakeDbAdmin | None = None):
    return Provisioner(settings, runner=runner or FakeRunner(), db_admin=db_admin or FakeDbAdmin(), startup_wait=0)


def make_plan(**overrides) -> Plan:
    values = {"name": "ماهانه", "price": 100_000, "duration_days": 30, "active": True}
    values.update(overrides)
    return Plan(**values)


class FakeCtx:
    """A Telegram update stand-in: records everything the handlers send back."""

    def __init__(
        self, user_id: int = 5, *, text: str = "", data: str = "", media: bool = False, first_name: str = "Ali"
    ):
        self.user_id = user_id
        self.first_name = first_name
        self.username = "ali"
        self.text = text
        self.data = data
        self.has_media = media
        self.is_callback = bool(data)
        self.out: list[tuple[str, str, list | None]] = []  # (kind, text, buttons)
        self.alerts: list[str] = []
        self.forwarded: list[int] = []
        self.deleted = False

    async def reply(self, text, buttons=None):
        self.out.append(("reply", text, buttons))

    async def edit(self, text, buttons=None):
        self.out.append(("edit", text, buttons))

    async def answer(self, text="", alert=False):
        if text and alert:
            self.alerts.append(text)

    async def forward_to(self, chat_id):
        self.forwarded.append(chat_id)
        return True

    async def delete_incoming(self):
        self.deleted = True

    @property
    def last(self) -> str:
        return self.out[-1][1] if self.out else ""

    @property
    def buttons(self) -> list:
        return self.out[-1][2] if self.out and self.out[-1][2] else []

    def button_data(self) -> list[str]:
        return [b.data for row in self.buttons for b in row if b.data]
