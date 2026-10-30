"""Gift codes: wallet credit that a code gives to each user who redeems it, once."""

from sqlalchemy import BigInteger, Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class GiftCode(Base):
    __tablename__ = "gift_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    times_used: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger)


class GiftRedemption(Base):
    __tablename__ = "gift_redemptions"
    __table_args__ = (UniqueConstraint("gift_id", "user_id", name="uq_gift_redemption_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gift_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    amount: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[int] = mapped_column(BigInteger)


class GiftSetting(Base):
    """Key/value settings of the gift feature (currently only the on/off switch)."""

    __tablename__ = "gift_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
