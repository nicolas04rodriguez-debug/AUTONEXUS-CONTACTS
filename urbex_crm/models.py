"""Relational model for the Urbex outreach CRM.

companies -> contacts -> enrollments (contact x sequence) -> messages (one per step)
templates feed drafting; events is an append-only audit log; suppressions is the
do-not-contact list (unsubscribes + hard bounces).
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Sector(str, enum.Enum):
    AUTOMOTIVE_DEALER = "automotive_dealer"      # concesionarios, car dealerships
    AUTOMOTIVE_SERVICES = "automotive_services"  # talleres, rent-a-car, fleet, parts
    CONSTRUCTION = "construction"                # constructoras, contractors
    CONSTRUCTION_DEVELOPER = "construction_developer"  # promotoras, real-estate developers
    CONSTRUCTION_MATERIALS = "construction_materials"  # suppliers, cement, prefab
    OTHER = "other"


class Tier(str, enum.Enum):
    HIGH = "high"   # premium / large revenue
    MID = "mid"
    LOW = "low"     # budget / small revenue
    UNKNOWN = "unknown"


class CompanyStatus(str, enum.Enum):
    NEW = "new"
    ENRICHED = "enriched"        # >= min contacts with emails
    IN_SEQUENCE = "in_sequence"
    REPLIED = "replied"
    WON = "won"
    LOST = "lost"
    DO_NOT_CONTACT = "do_not_contact"


class EmailStatus(str, enum.Enum):
    VERIFIED = "verified"   # provider says valid / deliverable
    LIKELY = "likely"       # provider found it but not verified, or pattern + MX ok
    GUESSED = "guessed"     # pattern guess only
    BOUNCED = "bounced"
    UNKNOWN = "unknown"


class EnrollmentStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    REPLIED = "replied"
    BOUNCED = "bounced"
    COMPLETED = "completed"      # all steps sent, no reply
    UNSUBSCRIBED = "unsubscribed"
    STOPPED = "stopped"


class MessageStatus(str, enum.Enum):
    DRAFT = "draft"              # generated, waiting for approval
    APPROVED = "approved"        # cleared to send at scheduled_at
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("domain", name="uq_company_domain"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    domain: Mapped[str | None] = mapped_column(String(255))
    website: Mapped[str | None] = mapped_column(String(512))
    sector: Mapped[Sector] = mapped_column(Enum(Sector), default=Sector.OTHER, index=True)
    sub_sector: Mapped[str | None] = mapped_column(String(100))
    tier: Mapped[Tier] = mapped_column(Enum(Tier), default=Tier.UNKNOWN, index=True)
    tier_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    tier_reason: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(String(64), default="ES")
    city: Mapped[str | None] = mapped_column(String(128))
    region: Mapped[str | None] = mapped_column(String(128))
    employee_count: Mapped[int | None] = mapped_column(Integer)
    est_revenue_eur: Mapped[float | None] = mapped_column(Float)
    brands: Mapped[str | None] = mapped_column(String(512))  # comma separated, dealers
    description: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(64))  # csv, telegram, apollo, manual
    status: Mapped[CompanyStatus] = mapped_column(Enum(CompanyStatus), default=CompanyStatus.NEW, index=True)
    extra: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    contacts: Mapped[list["Contact"]] = relationship(back_populates="company", cascade="all, delete-orphan")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Company {self.id} {self.name} {self.sector.value}/{self.tier.value}>"


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("email", name="uq_contact_email"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    title: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str | None] = mapped_column(String(64))   # ceo, owner, gm, sales, marketing, ops, other
    seniority_rank: Mapped[int] = mapped_column(Integer, default=99)  # 1 = CEO/owner, lower is better
    email: Mapped[str | None] = mapped_column(String(255))
    email_status: Mapped[EmailStatus] = mapped_column(Enum(EmailStatus), default=EmailStatus.UNKNOWN)
    email_source: Mapped[str | None] = mapped_column(String(64))  # apollo, hunter, guess, manual
    email_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    linkedin_url: Mapped[str | None] = mapped_column(String(512))
    phone: Mapped[str | None] = mapped_column(String(64))
    language: Mapped[str] = mapped_column(String(8), default="es")
    do_not_contact: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    company: Mapped[Company] = relationship(back_populates="contacts")
    enrollments: Mapped[list["Enrollment"]] = relationship(back_populates="contact", cascade="all, delete-orphan")

    @property
    def full_name(self) -> str:
        return " ".join(p for p in [self.first_name, self.last_name] if p) or ""


class Template(Base):
    __tablename__ = "templates"
    __table_args__ = (UniqueConstraint("sector", "tier", "step", "language", name="uq_template_slot"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    sector: Mapped[Sector] = mapped_column(Enum(Sector))
    tier: Mapped[Tier] = mapped_column(Enum(Tier))
    step: Mapped[int] = mapped_column(Integer)  # 1 = first touch, 2.. = follow-ups
    language: Mapped[str] = mapped_column(String(8), default="es")
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)   # Jinja2
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Sequence(Base):
    __tablename__ = "sequences"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    sector: Mapped[Sector] = mapped_column(Enum(Sector))
    tier: Mapped[Tier] = mapped_column(Enum(Tier))
    language: Mapped[str] = mapped_column(String(8), default="es")
    # Days to wait after step N before sending step N+1. len == number of follow-ups.
    step_delays_days: Mapped[list] = mapped_column(JSON, default=lambda: [3, 4, 7])
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def total_steps(self) -> int:
        return len(self.step_delays_days or []) + 1


class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (UniqueConstraint("contact_id", "sequence_id", name="uq_enrollment"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), index=True)
    sequence_id: Mapped[int] = mapped_column(ForeignKey("sequences.id"), index=True)
    status: Mapped[EnrollmentStatus] = mapped_column(Enum(EnrollmentStatus), default=EnrollmentStatus.ACTIVE, index=True)
    current_step: Mapped[int] = mapped_column(Integer, default=0)  # last step SENT (0 = nothing yet)
    next_send_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    gmail_thread_id: Mapped[str | None] = mapped_column(String(64))
    last_message_id_header: Mapped[str | None] = mapped_column(String(255))
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime)
    stopped_reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    contact: Mapped[Contact] = relationship(back_populates="enrollments")
    sequence: Mapped[Sequence] = relationship()
    messages: Mapped[list["Message"]] = relationship(back_populates="enrollment", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    enrollment_id: Mapped[int] = mapped_column(ForeignKey("enrollments.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), index=True)
    step: Mapped[int] = mapped_column(Integer)
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[MessageStatus] = mapped_column(Enum(MessageStatus), default=MessageStatus.DRAFT, index=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    gmail_message_id: Mapped[str | None] = mapped_column(String(64))
    gmail_thread_id: Mapped[str | None] = mapped_column(String(64))
    message_id_header: Mapped[str | None] = mapped_column(String(255))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    enrollment: Mapped[Enrollment] = relationship(back_populates="messages")
    contact: Mapped[Contact] = relationship()


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Suppression(Base):
    __tablename__ = "suppressions"

    id: Mapped[int] = mapped_column(primary_key=True)
    value: Mapped[str] = mapped_column(String(255), unique=True)  # email or @domain
    reason: Mapped[str] = mapped_column(String(64))  # unsubscribe, bounce, manual
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


def log_event(session, entity_type: str, entity_id: int | None, kind: str, payload: dict | None = None) -> None:
    session.add(Event(entity_type=entity_type, entity_id=entity_id, kind=kind, payload=payload or {}))
