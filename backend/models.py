from __future__ import annotations

import uuid
from datetime import datetime

from flask_login import UserMixin
from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from backend.db import Base


def generate_uuid() -> str:
    return str(uuid.uuid4())


class Contract(Base):
    __tablename__ = "contracts"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    order_index = Column(Integer, nullable=False, default=0)
    force_done = Column(Boolean, default=False)
    date = Column(Date, nullable=True)
    deadline = Column(Date, nullable=True)
    supplier = Column(String, default="")
    org = Column(String, default="")
    date_fact = Column(Date, nullable=True)
    docs_sent = Column(Boolean, default=False)
    number = Column(String, default="")
    link_url = Column(String, default="")
    item = Column(String, default="")
    qty = Column(Float, default=0)
    plan_qty = Column(Float, default=0)
    plan_date = Column(Date, nullable=True)
    delivered = Column(Float, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    items = relationship(
        "ContractItem",
        back_populates="contract",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ContractItem.position",
    )


class ContractItem(Base):
    __tablename__ = "contract_items"

    id = Column(Integer, primary_key=True)
    contract_id = Column(String, ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False)
    position = Column(Integer, default=0)
    item = Column(String, default="")
    qty = Column(Float, default=0)
    plan_qty = Column(Float, default=0)
    plan_date = Column(Date, nullable=True)
    date_fact = Column(Date, nullable=True)
    delivered = Column(Float, default=0)

    contract = relationship("Contract", back_populates="items")


class WarehouseItem(Base):
    __tablename__ = "warehouse_items"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    unit = Column(String, default="")
    active = Column(Boolean, default=True)


class WarehouseIncome(Base):
    __tablename__ = "warehouse_incomes"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    item = Column(String, nullable=False)
    invoice_number = Column(String, default="")
    date = Column(Date, nullable=True)
    qty = Column(Float, default=0)
    unit = Column(String, default="")
    in_stock = Column(Boolean, default=True)


class WarehouseExpense(Base):
    __tablename__ = "warehouse_expenses"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    org = Column(String, default="")
    date = Column(Date, nullable=True)
    item = Column(String, default="")
    qty = Column(Float, default=0)
    contract_number = Column(String, default="")


class PriceItem(Base):
    __tablename__ = "price_items"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    code = Column(String, default="")
    name = Column(String, default="")
    price_no_vat = Column(Float, default=0)
    price_with_vat = Column(Float, default=0)
    note = Column(String, default="")


class DriveFile(Base):
    __tablename__ = "drive_files"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, nullable=False, default="default")
    name = Column(String, nullable=False)
    storage_name = Column(String, nullable=False)
    mime_type = Column(String, default="application/octet-stream")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class CommercialsState(Base):
    __tablename__ = "commercials_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    headers = Column(Text, default="")
    main_rows = Column(Text, default="")
    params = Column(Text, default="")
    templates = Column(Text, default="")
    kp_tables = Column(Text, default="")
    organization = Column(String, default="")
    organization_options = Column(Text, default="")


class ArchiveEntry(Base):
    __tablename__ = "archive_entries"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    filename = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class User(Base, UserMixin):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=True)
    password_hash = Column(String, nullable=True)
    is_email_verified = Column(Boolean, default=False)
    is_admin = Column(Boolean, default=False)
    telegram_user_id = Column(String, unique=True, nullable=True)
    telegram_chat_id = Column(String, nullable=True)
    is_telegram_verified = Column(Boolean, default=False)
    preferred_channel = Column(String, default="email")
    email_otp_trusted_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    profile = relationship(
        "Profile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    otp_challenges = relationship(
        "OTPChallenge",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    trusted_devices = relationship(
        "TrustedDevice",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    telegram_pending = relationship(
        "TelegramPending",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def set_password(self, password: str) -> None:
        from werkzeug.security import generate_password_hash

        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        from werkzeug.security import check_password_hash

        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)


class Profile(Base):
    __tablename__ = "profiles"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    full_name = Column(String, nullable=True)
    age = Column(Integer, nullable=True)
    activity = Column(String, nullable=True)

    user = relationship("User", back_populates="profile")


class OTPChallenge(Base):
    __tablename__ = "otp_challenges"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    channel = Column(String, nullable=False)
    purpose = Column(String, nullable=False)
    code_hash = Column(String, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    consumed_at = Column(DateTime, nullable=True)
    attempts = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="otp_challenges")


class TrustedDevice(Base):
    __tablename__ = "trusted_devices"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String, nullable=False, unique=True)
    user_agent = Column(String, nullable=True)
    ip_address = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)

    user = relationship("User", back_populates="trusted_devices")


class TelegramPending(Base):
    __tablename__ = "telegram_pending"

    id = Column(Integer, primary_key=True)
    token = Column(String, unique=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    purpose = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    consumed_at = Column(DateTime, nullable=True)
    telegram_user_id = Column(String, nullable=True)
    telegram_chat_id = Column(String, nullable=True)

    user = relationship("User", back_populates="telegram_pending")
