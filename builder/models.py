"""Builder database schema (separate from the bot's own database)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Naive UTC timestamp (all builder datetimes are stored as naive UTC)."""
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class TenantStatus:
    PENDING = "pending"  # slot created, waiting for the payment to be approved
    AWAITING_TOKEN = "awaiting_token"  # paid (or trial): the owner still has to send the bot token
    RUNNING = "running"
    STOPPED = "stopped"  # stopped by an admin; not touched by the expiry job
    EXPIRED = "expired"  # subscription ended, container stopped, waiting for renewal
    ERROR = "error"  # paid, but the deploy failed; an admin can retry
    DELETED = "deleted"

    LIVE = (PENDING, AWAITING_TOKEN, RUNNING, STOPPED, EXPIRED, ERROR)


class PaymentKind:
    TOPUP = "topup"
    ORDER = "order"
    RENEW = "renew"
    PANEL = "panel"  # a new panel (admin on a master panel)
    PANEL_RENEW = "panel_renew"
    PANEL_VOLUME = "panel_volume"  # extra traffic for an existing panel

    PANEL_KINDS = (PANEL, PANEL_RENEW, PANEL_VOLUME)


class PaymentStatus:
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class BuilderUser(Base):
    __tablename__ = "builder_users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    first_name: Mapped[str] = mapped_column(String(255), default="")
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    balance: Mapped[int] = mapped_column(BigInteger, default=0)
    banned: Mapped[bool] = mapped_column(Boolean, default=False)
    trial_used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Plan(Base):
    __tablename__ = "builder_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))
    price: Mapped[int] = mapped_column(BigInteger, default=0)  # Toman
    duration_days: Mapped[int] = mapped_column(Integer, default=30)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Tenant(Base):
    __tablename__ = "builder_tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("builder_plans.id"), nullable=True)
    # Unknown until the owner sends the bot token (a purchase creates the tenant first).
    bot_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    bot_username: Mapped[str] = mapped_column(String(64), default="")
    token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Paid days not yet applied: the subscription clock starts when the bot is actually running.
    pending_days: Mapped[int] = mapped_column(Integer, default=0)
    db_password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    admin_ids: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default=TenantStatus.PENDING, index=True)
    is_trial: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reminded_for: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    host_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def container_name(self) -> str:
        return f"mdjebot-t{self.id}"

    @property
    def db_name(self) -> str:
        return f"mdjebot_t{self.id}"

    @property
    def db_user(self) -> str:
        return f"t{self.id}"

    @property
    def redis_prefix(self) -> str:
        return f"mdjebot:t{self.id}"

    @property
    def admin_id_list(self) -> list[int]:
        return [int(x) for x in self.admin_ids.split(",") if x.strip()]


class Payment(Base):
    __tablename__ = "builder_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    kind: Mapped[str] = mapped_column(String(16))
    amount: Mapped[int] = mapped_column(BigInteger)
    method: Mapped[str] = mapped_column(String(16))  # card | wallet | admin
    status: Mapped[str] = mapped_column(String(16), default=PaymentStatus.PENDING, index=True)
    plan_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tenant_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    panel_plan_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    panel_sub_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    discount_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class Card(Base):
    __tablename__ = "builder_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    number: Mapped[str] = mapped_column(String(32))
    holder: Mapped[str] = mapped_column(String(100), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class DiscountCode(Base):
    __tablename__ = "builder_discount_codes"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    percent: Mapped[int] = mapped_column(Integer)
    max_uses: Mapped[int] = mapped_column(Integer, default=0)  # 0 = unlimited
    used: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Setting(Base):
    __tablename__ = "builder_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class PanelAuth:
    PASSWORD = "password"
    API_KEY = "api_key"


class PanelSubStatus:
    PENDING = "pending"  # created, waiting for the payment to be approved
    ACTIVE = "active"
    LIMITED = "limited"  # the traffic is used up; the customer can buy extra volume
    EXPIRED = "expired"  # the monthly period ended; the panel admin is disabled until renewal
    DISABLED = "disabled"  # disabled by a builder admin
    ERROR = "error"  # paid, but the admin could not be created on the panel yet; an admin can retry
    DELETED = "deleted"

    LIVE = (PENDING, ACTIVE, LIMITED, EXPIRED, DISABLED, ERROR)


class MasterPanel(Base):
    """A Pasarguard panel owned by the builder operator; customers get admins on it."""

    __tablename__ = "builder_master_panels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))
    base_url: Mapped[str] = mapped_column(String(255))
    auth_type: Mapped[str] = mapped_column(String(16), default=PanelAuth.PASSWORD)
    username: Mapped[str] = mapped_column(String(100), default="")
    secret_enc: Mapped[str] = mapped_column(Text)  # password or API key, encrypted
    role_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # role given to customer admins
    group_ids: Mapped[str] = mapped_column(String(255), default="")  # groups customer bots hand to their users
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_check_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def group_id_list(self) -> list[int]:
        return [int(x) for x in self.group_ids.split(",") if x.strip().isdigit()]


class PanelPlan(Base):
    """A monthly panel plan (traffic + days) or an extra-volume pack, sold on one master panel."""

    __tablename__ = "builder_panel_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("builder_master_panels.id"))
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(8), default="plan")  # plan | addon
    data_gb: Mapped[int] = mapped_column(Integer, default=0)  # traffic of the plan / size of the pack
    duration_days: Mapped[int] = mapped_column(Integer, default=30)  # 0 for add-on packs
    max_users: Mapped[int] = mapped_column(Integer, default=0)  # 0 = unlimited
    price: Mapped[int] = mapped_column(BigInteger, default=0)  # Toman
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    @property
    def data_bytes(self) -> int:
        return int(self.data_gb) * 1024**3


class PanelSub(Base):
    """One admin account a customer bought on a master panel, and the bot it is connected to."""

    __tablename__ = "builder_panel_subs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    tenant_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # the customer's bot
    master_id: Mapped[int] = mapped_column(ForeignKey("builder_master_panels.id"))
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("builder_panel_plans.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=PanelSubStatus.PENDING, index=True)
    admin_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # id of the admin on the master panel
    admin_username: Mapped[str] = mapped_column(String(64), default="")
    admin_password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_limit: Mapped[int] = mapped_column(BigInteger, default=0)  # bytes granted (plan + extra packs)
    used_bytes: Mapped[int] = mapped_column(BigInteger, default=0)  # last metered usage
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    linked: Mapped[bool] = mapped_column(Boolean, default=False)  # connected to the customer's bot
    warned_usage: Mapped[bool] = mapped_column(Boolean, default=False)
    warned_expiry: Mapped[bool] = mapped_column(Boolean, default=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
