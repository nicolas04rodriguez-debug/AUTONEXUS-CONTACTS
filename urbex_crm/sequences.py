"""Sequence engine: enroll -> draft step -> approve -> send -> schedule next.

All times stored naive UTC. Sending windows are evaluated in SEND_TIMEZONE.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .drafting import draft_for
from .models import (
    Company, CompanyStatus, Contact, EmailStatus, Enrollment, EnrollmentStatus,
    Message, MessageStatus, Sequence, Sector, Suppression, Tier, log_event, utcnow,
)

log = logging.getLogger(__name__)

DEFAULT_DELAYS = [3, 4, 7]  # step1 -> +3d step2 -> +4d step3 -> +7d step4 (breakup)


# ----------------------------------------------------------------- windows --

def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def in_send_window(now_utc: datetime | None = None) -> bool:
    tz = ZoneInfo(settings.send_timezone)
    now = (now_utc or utcnow()).replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    if settings.send_weekdays_only and now.weekday() >= 5:
        return False
    return _parse_hhmm(settings.send_window_start) <= now.time() <= _parse_hhmm(settings.send_window_end)


def next_window_start(after_utc: datetime) -> datetime:
    """Earliest UTC datetime >= after_utc inside the sending window."""
    tz = ZoneInfo(settings.send_timezone)
    start, end = _parse_hhmm(settings.send_window_start), _parse_hhmm(settings.send_window_end)
    local = after_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    for _ in range(14):
        weekday_ok = not settings.send_weekdays_only or local.weekday() < 5
        if weekday_ok and local.time() < start:
            local = local.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
            break
        if weekday_ok and start <= local.time() <= end:
            break
        local = (local + timedelta(days=1)).replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    # Jitter so sends don't all land at 09:00:00
    local = local + timedelta(minutes=random.randint(0, 45))
    return local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def sent_today_count(session: Session, now_utc: datetime | None = None) -> int:
    tz = ZoneInfo(settings.send_timezone)
    now_local = (now_utc or utcnow()).replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    day_start_utc = now_local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return session.scalar(select(func.count(Message.id)).where(Message.status == MessageStatus.SENT, Message.sent_at >= day_start_utc)) or 0


# -------------------------------------------------------------- sequences --

def ensure_default_sequences(session: Session) -> list[Sequence]:
    out = []
    for family in ("automotive_dealer", "construction"):
        for tier in (Tier.HIGH, Tier.MID, Tier.LOW):
            name = f"{family}-{tier.value}"
            seq = session.scalar(select(Sequence).where(Sequence.name == name))
            if not seq:
                seq = Sequence(name=name, sector=Sector(family), tier=tier, step_delays_days=list(DEFAULT_DELAYS))
                session.add(seq)
            out.append(seq)
    session.flush()
    return out


def sequence_for(session: Session, company: Company) -> Sequence:
    from .drafting import TEMPLATE_FAMILY
    family = TEMPLATE_FAMILY.get(company.sector, "construction")
    tier = company.tier if company.tier != Tier.UNKNOWN else Tier.MID
    seq = session.scalar(select(Sequence).where(Sequence.name == f"{family}-{tier.value}", Sequence.active.is_(True)))
    if not seq:
        ensure_default_sequences(session)
        seq = session.scalar(select(Sequence).where(Sequence.name == f"{family}-{tier.value}"))
    return seq


def is_suppressed(session: Session, email: str) -> bool:
    email = email.lower()
    domain = "@" + email.split("@")[-1]
    return session.scalar(select(func.count(Suppression.id)).where(Suppression.value.in_([email, domain]))) > 0


# ------------------------------------------------------------- enrollment --

@dataclass
class EnrollOutcome:
    enrollment: Enrollment | None
    message: Message | None
    skipped_reason: str | None = None


def enroll(session: Session, contact: Contact, sequence: Sequence | None = None, personalize: bool = False,
           auto_approve: bool | None = None) -> EnrollOutcome:
    """Create the enrollment and draft step 1. Returns the draft for approval."""
    company = contact.company
    sequence = sequence or sequence_for(session, company)
    if not contact.email:
        return EnrollOutcome(None, None, "no email")
    if contact.do_not_contact or is_suppressed(session, contact.email) or company.status == CompanyStatus.DO_NOT_CONTACT:
        return EnrollOutcome(None, None, "suppressed")
    if contact.email_status == EmailStatus.BOUNCED:
        return EnrollOutcome(None, None, "bounced before")
    existing = session.scalar(select(Enrollment).where(Enrollment.contact_id == contact.id, Enrollment.sequence_id == sequence.id))
    if existing:
        return EnrollOutcome(existing, None, "already enrolled")
    enr = Enrollment(contact_id=contact.id, sequence_id=sequence.id, status=EnrollmentStatus.ACTIVE, current_step=0,
                     next_send_at=utcnow())
    session.add(enr)
    session.flush()
    msg = draft_step(session, enr, 1, personalize=personalize, auto_approve=auto_approve)
    company.status = CompanyStatus.IN_SEQUENCE
    log_event(session, "enrollment", enr.id, "enrolled", {"contact_id": contact.id, "sequence": sequence.name})
    return EnrollOutcome(enr, msg)


def draft_step(session: Session, enr: Enrollment, step: int, personalize: bool = False,
               auto_approve: bool | None = None) -> Message:
    contact = enr.contact
    company = contact.company
    previous = session.scalar(select(Message).where(Message.enrollment_id == enr.id, Message.step == 1))
    d = draft_for(session, company, contact, step, personalize=personalize,
                  previous_subject=previous.subject if previous else None)
    if auto_approve is None:
        auto_approve = settings.auto_approve_first_touch if step == 1 else settings.auto_approve_followups
    msg = Message(enrollment_id=enr.id, contact_id=contact.id, step=step, subject=d.subject, body=d.body,
                  status=MessageStatus.APPROVED if auto_approve else MessageStatus.DRAFT,
                  scheduled_at=enr.next_send_at or utcnow())
    session.add(msg)
    session.flush()
    log_event(session, "message", msg.id, "drafted", {"step": step, "auto_approved": auto_approve})
    return msg


def approve(session: Session, message: Message, send_at: datetime | None = None) -> Message:
    message.status = MessageStatus.APPROVED
    message.scheduled_at = send_at or message.scheduled_at or utcnow()
    log_event(session, "message", message.id, "approved", {})
    return message


def skip(session: Session, message: Message, reason: str = "skipped by user") -> None:
    message.status = MessageStatus.SKIPPED
    message.error = reason
    enr = message.enrollment
    enr.status = EnrollmentStatus.STOPPED
    enr.stopped_reason = reason
    log_event(session, "message", message.id, "skipped", {"reason": reason})


def stop_contact(session: Session, contact: Contact, reason: str = "manual", suppress: bool = True) -> None:
    for enr in contact.enrollments:
        if enr.status in (EnrollmentStatus.ACTIVE, EnrollmentStatus.PAUSED):
            enr.status = EnrollmentStatus.UNSUBSCRIBED if reason == "unsubscribe" else EnrollmentStatus.STOPPED
            enr.stopped_reason = reason
            for m in enr.messages:
                if m.status in (MessageStatus.DRAFT, MessageStatus.APPROVED):
                    m.status = MessageStatus.SKIPPED
    contact.do_not_contact = True
    if suppress and contact.email and not is_suppressed(session, contact.email):
        session.add(Suppression(value=contact.email.lower(), reason=reason))
    log_event(session, "contact", contact.id, "stopped", {"reason": reason})


# ---------------------------------------------------------------- sending --

def pending_drafts(session: Session, limit: int = 20) -> list[Message]:
    return list(session.scalars(select(Message).where(Message.status == MessageStatus.DRAFT).order_by(Message.created_at).limit(limit)))


def due_messages(session: Session, now: datetime | None = None) -> list[Message]:
    now = now or utcnow()
    q = (select(Message).join(Enrollment).where(Message.status == MessageStatus.APPROVED, Message.scheduled_at <= now,
                                                Enrollment.status == EnrollmentStatus.ACTIVE).order_by(Message.scheduled_at))
    return list(session.scalars(q))


def send_message(session: Session, message: Message, mailer, now: datetime | None = None) -> bool:
    """Send one approved message and schedule the next step. Returns True on success."""
    now = now or utcnow()
    enr = message.enrollment
    contact = message.contact
    if not contact.email or contact.do_not_contact or is_suppressed(session, contact.email):
        message.status, message.error = MessageStatus.SKIPPED, "suppressed at send time"
        enr.status, enr.stopped_reason = EnrollmentStatus.STOPPED, "suppressed"
        return False
    try:
        res = mailer.send(contact.email, message.subject, message.body,
                          in_reply_to=enr.last_message_id_header if message.step > 1 else None,
                          thread_id=enr.gmail_thread_id if message.step > 1 else None,
                          to_name=contact.full_name or None)
    except Exception as e:  # noqa: BLE001
        message.status, message.error = MessageStatus.FAILED, str(e)[:1000]
        log_event(session, "message", message.id, "send_failed", {"error": str(e)[:300]})
        log.exception("send failed for message %s", message.id)
        return False
    message.status, message.sent_at = MessageStatus.SENT, now
    message.message_id_header, message.gmail_message_id, message.gmail_thread_id = res.message_id_header, res.provider_message_id, res.thread_id
    enr.current_step, enr.last_sent_at, enr.last_message_id_header = message.step, now, res.message_id_header
    if res.thread_id:
        enr.gmail_thread_id = res.thread_id
    delays = enr.sequence.step_delays_days or []
    if message.step - 1 < len(delays):
        enr.next_send_at = next_window_start(now + timedelta(days=int(delays[message.step - 1])))
    else:
        enr.next_send_at, enr.status = None, EnrollmentStatus.COMPLETED
    log_event(session, "message", message.id, "sent", {"step": message.step, "to": contact.email})
    return True


def send_due(session: Session, mailer, now: datetime | None = None, respect_window: bool = True, limit: int | None = None) -> list[Message]:
    """Send everything approved & due, honoring window and daily cap. Returns sent messages."""
    now = now or utcnow()
    if respect_window and not in_send_window(now):
        return []
    budget = settings.daily_send_cap - sent_today_count(session, now)
    if limit is not None:
        budget = min(budget, limit)
    sent: list[Message] = []
    for msg in due_messages(session, now):
        if budget <= 0:
            break
        if send_message(session, msg, mailer, now):
            sent.append(msg)
            budget -= 1
            session.flush()
    return sent


def draft_due_followups(session: Session, now: datetime | None = None) -> list[Message]:
    """For active enrollments whose next_send_at has passed and no message exists for the next step, draft it."""
    now = now or utcnow()
    out: list[Message] = []
    q = select(Enrollment).where(Enrollment.status == EnrollmentStatus.ACTIVE, Enrollment.next_send_at.is_not(None),
                                 Enrollment.next_send_at <= now, Enrollment.current_step >= 1)
    for enr in session.scalars(q):
        next_step = enr.current_step + 1
        if next_step > enr.sequence.total_steps:
            enr.status, enr.next_send_at = EnrollmentStatus.COMPLETED, None
            continue
        exists = session.scalar(select(Message).where(Message.enrollment_id == enr.id, Message.step == next_step))
        if exists:
            continue
        try:
            out.append(draft_step(session, enr, next_step))
        except LookupError as e:
            log.warning("no template: %s", e)
    return out


def check_replies(session: Session, mailer) -> list[Enrollment]:
    """Poll threads of active enrollments; stop on reply or bounce. Returns enrollments that changed."""
    changed: list[Enrollment] = []
    q = select(Enrollment).where(Enrollment.status == EnrollmentStatus.ACTIVE, Enrollment.gmail_thread_id.is_not(None))
    for enr in session.scalars(q):
        try:
            state = mailer.thread_state(enr.gmail_thread_id)
        except Exception as e:  # noqa: BLE001
            log.warning("thread check failed for %s: %s", enr.id, e)
            continue
        if state.bounced:
            enr.status, enr.stopped_reason = EnrollmentStatus.BOUNCED, "bounce"
            enr.contact.email_status = EmailStatus.BOUNCED
            session.add(Suppression(value=enr.contact.email.lower(), reason="bounce"))
            changed.append(enr)
        elif state.replied:
            enr.status, enr.replied_at = EnrollmentStatus.REPLIED, utcnow()
            enr.contact.company.status = CompanyStatus.REPLIED
            if state.reply_snippet and "baja" in state.reply_snippet.lower()[:200]:
                stop_contact(session, enr.contact, reason="unsubscribe")
            changed.append(enr)
        if enr in changed:
            for m in enr.messages:
                if m.status in (MessageStatus.DRAFT, MessageStatus.APPROVED):
                    m.status = MessageStatus.SKIPPED
            log_event(session, "enrollment", enr.id, enr.status.value, {"snippet": state.reply_snippet})
    return changed


def stats(session: Session) -> dict:
    def count(model, *conds):
        return session.scalar(select(func.count(model.id)).where(*conds)) or 0
    return {
        "companies": count(Company),
        "companies_by_sector": {s.value: count(Company, Company.sector == s) for s in Sector},
        "companies_by_tier": {t.value: count(Company, Company.tier == t) for t in Tier},
        "contacts": count(Contact),
        "contacts_with_email": count(Contact, Contact.email.is_not(None)),
        "enrollments_active": count(Enrollment, Enrollment.status == EnrollmentStatus.ACTIVE),
        "enrollments_replied": count(Enrollment, Enrollment.status == EnrollmentStatus.REPLIED),
        "enrollments_bounced": count(Enrollment, Enrollment.status == EnrollmentStatus.BOUNCED),
        "enrollments_completed": count(Enrollment, Enrollment.status == EnrollmentStatus.COMPLETED),
        "messages_draft": count(Message, Message.status == MessageStatus.DRAFT),
        "messages_approved": count(Message, Message.status == MessageStatus.APPROVED),
        "messages_sent": count(Message, Message.status == MessageStatus.SENT),
        "messages_failed": count(Message, Message.status == MessageStatus.FAILED),
        "sent_today": sent_today_count(session),
        "daily_cap": settings.daily_send_cap,
    }
