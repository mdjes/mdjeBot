from __future__ import annotations

import string
from types import SimpleNamespace

import pytest

from builder.models import MasterPanel, PanelAuth
from builder.security import SecretBox
from builder.services.panels.backend import PanelError, PasarguardBackend
from builder.services.panels.credentials import USERNAME_RE, generate_password, generate_username
from builder.settings import derive_secret_key
from builder.util import fmt_bytes, usage_bar


@pytest.mark.parametrize("length", [12, 16, 40])
def test_generated_passwords_meet_the_panel_policy(length):
    for _ in range(50):
        password = generate_password(length, username="p5_ab12")
        assert len(password) >= length
        assert any(c in string.ascii_uppercase for c in password)
        assert any(c in string.ascii_lowercase for c in password)
        assert any(c in string.digits for c in password)
        assert any(c in "!@#$%^&*()-_=+" for c in password)
        assert '"' not in password and "'" not in password and "<" not in password
        assert "p5_ab12" not in password.lower()


def test_passwords_are_not_repeated():
    assert len({generate_password() for _ in range(200)}) == 200


def test_generated_usernames_fit_the_panel_rules():
    names = {generate_username(123456789) for _ in range(100)}
    assert len(names) > 90  # random suffix
    for name in names:
        assert USERNAME_RE.match(name) and name.startswith("p123456789_") and len(name) <= 32
    assert generate_username(5, prefix="we!rd prefix")[:6] == "werdpr"  # unsafe characters are stripped


def test_fmt_bytes_and_usage_bar():
    assert fmt_bytes(0) == "0 KB" and fmt_bytes(1536 * 1024) == "2 MB"
    assert fmt_bytes(50 * 1024**3) == "50.00 GB" and fmt_bytes(2 * 1024**4) == "2.00 TB"
    assert usage_bar(0, 100) == "░░░░░░░░░░ 0٪"
    assert usage_bar(30, 100) == "███░░░░░░░ 30٪"
    assert usage_bar(250, 100) == "██████████ 100٪"  # never overflows the bar
    assert usage_bar(5, 0) == "∞"


def test_backend_maps_admin_details_without_trusting_missing_fields():
    admin = SimpleNamespace(
        id=7, username="p5_x", status="active", used_traffic=None, data_limit=10 * 1024**3, is_limited=False
    )
    info = PasarguardBackend._info(admin)
    assert (info.id, info.used_bytes, info.data_limit, info.total_users) == (7, 0, 10 * 1024**3, 0)
    assert info.is_disabled is False


def test_backend_translates_http_errors_into_readable_panel_errors():
    import httpx

    def error(status, text=""):
        request = httpx.Request("POST", "https://panel.example.com/api/admin")
        return httpx.HTTPStatusError(
            "boom", request=request, response=httpx.Response(status, text=text, request=request)
        )

    assert "unauthorized" in str(PasarguardBackend._error(error(401)))
    assert "conflict" in str(PasarguardBackend._error(error(409)))
    assert "HTTP 500" in str(PasarguardBackend._error(error(500, "internal")))
    assert "did not answer" in str(PasarguardBackend._error(httpx.ReadTimeout("slow")))
    assert "could not connect" in str(PasarguardBackend._error(httpx.ConnectError("refused")))
    assert isinstance(PasarguardBackend._error(RuntimeError("x")), PanelError)


def test_backend_uses_the_api_key_directly_and_decrypts_the_password_secret():
    box = SecretBox(derive_secret_key("t"))
    backend = PasarguardBackend(box)
    key_master = MasterPanel(
        id=1, name="K", base_url="https://p", auth_type=PanelAuth.API_KEY, secret_enc=box.encrypt("pg_key_1")
    )
    assert backend._secret(key_master) == "pg_key_1"
