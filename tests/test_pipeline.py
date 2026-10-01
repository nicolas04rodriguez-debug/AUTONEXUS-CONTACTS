from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from urbex_crm.importers import import_csv, normalize_domain
from urbex_crm.models import Company, Contact, EnrollmentStatus, Message, MessageStatus, Suppression, Tier, utcnow
from urbex_crm.sequences import (approve, check_replies, draft_due_followups, enroll, pending_drafts, send_due, stats,
                                 stop_contact)

CSV = Path(__file__).resolve().parent.parent / "data" / "companies_example.csv"


def test_normalize_domain():
    assert normalize_domain("https://www.Foo.ES/contacto") == "foo.es"
    assert normalize_domain("foo.es") == "foo.es"
    assert normalize_domain("") is None


def test_import_and_classify(session):
    res = import_csv(session, CSV.read_text(encoding="utf-8"))
    assert res["created"] == 6 and res["contacts"] == 3
    autos = session.scalar(select(Company).where(Company.name == "Autos Premium Madrid"))
    assert autos.tier == Tier.HIGH and autos.sector.value == "automotive_dealer"
    lev = session.scalar(select(Company).where(Company.name == "Construcciones Levante SA"))
    assert lev.tier == Tier.HIGH
    # idempotent
    res2 = import_csv(session, CSV.read_text(encoding="utf-8"))
    assert res2["created"] == 0 and res2["updated"] == 6


def _seed_contact(session, tier=Tier.LOW):
    import_csv(session, CSV.read_text(encoding="utf-8"))
    contact = session.scalar(select(Contact).where(Contact.email == "laura@ocasionrapida.example"))
    assert contact is not None
    return contact


def test_full_sequence_flow(session, mailer):
    contact = _seed_contact(session)
    out = enroll(session, contact)
    assert out.message.status == MessageStatus.DRAFT and out.message.step == 1
    assert "Hola Laura," in out.message.body
    assert "baja" in out.message.body  # opt-out line present
    assert out.message.subject.startswith("Leads que no llegan")

    # Not approved -> nothing sent
    assert send_due(session, mailer) == []
    approve(session, out.message)
    sent = send_due(session, mailer)
    assert len(sent) == 1 and mailer.sent[0]["to"] == "laura@ocasionrapida.example"
    enr = out.enrollment
    assert enr.current_step == 1 and enr.gmail_thread_id == "thread-1"
    assert enr.next_send_at > utcnow() + timedelta(days=2)

    # Follow-up not due yet
    assert draft_due_followups(session) == []
    enr.next_send_at = utcnow() - timedelta(minutes=1)
    session.flush()
    drafted = draft_due_followups(session)
    assert len(drafted) == 1 and drafted[0].step == 2 and drafted[0].status == MessageStatus.APPROVED  # auto-approved
    assert drafted[0].subject.startswith("Re: ")
    # idempotent: no duplicate draft
    assert draft_due_followups(session) == []
    sent = send_due(session, mailer)
    assert len(sent) == 1 and mailer.sent[1]["thread_id"] == "thread-1" and mailer.sent[1]["in_reply_to"] == "<msg1@urbex.test>"

    # Reply stops the sequence and marks company replied
    mailer.replies.add("thread-1")
    changed = check_replies(session, mailer)
    assert changed and enr.status == EnrollmentStatus.REPLIED
    assert contact.company.status.value == "replied"
    enr.next_send_at = utcnow() - timedelta(minutes=1)
    assert draft_due_followups(session) == []


def test_sequence_completes_after_last_step(session, mailer):
    contact = _seed_contact(session)
    out = enroll(session, contact, auto_approve=True)
    enr = out.enrollment
    for step in range(1, 5):
        if step > 1:
            enr.next_send_at = utcnow() - timedelta(minutes=1)
            session.flush()
            draft_due_followups(session)
        assert len(send_due(session, mailer)) == 1
    assert enr.status == EnrollmentStatus.COMPLETED and enr.next_send_at is None
    assert len(mailer.sent) == 4


def test_daily_cap(session, mailer):
    import_csv(session, CSV.read_text(encoding="utf-8"))
    comp = session.scalar(select(Company).where(Company.name == "Ocasión Rápida"))
    for i in range(7):
        session.add(Contact(company_id=comp.id, first_name=f"P{i}", email=f"p{i}@ocasionrapida.example"))
    session.flush()
    for c in comp.contacts:
        enroll(session, c, auto_approve=True)
    sent = send_due(session, mailer)
    assert len(sent) == 5  # DAILY_SEND_CAP in conftest
    assert send_due(session, mailer) == []


def test_bounce_suppresses_and_stop_contact(session, mailer):
    contact = _seed_contact(session)
    out = enroll(session, contact, auto_approve=True)
    send_due(session, mailer)
    mailer.bounces.add("thread-1")
    check_replies(session, mailer)
    assert out.enrollment.status == EnrollmentStatus.BOUNCED
    assert session.scalar(select(Suppression).where(Suppression.value == contact.email))

    other = session.scalar(select(Contact).where(Contact.email == "jose.perez@reformasperez.example")) or None
    assert other is None  # CSV had no email for José -> not enrolled
    # enrolling a suppressed contact again is refused
    again = enroll(session, contact)
    assert again.skipped_reason in ("already enrolled", "suppressed", "bounced before")
    stop_contact(session, contact, reason="manual")
    assert contact.do_not_contact


def test_stats_and_queue(session, mailer):
    contact = _seed_contact(session)
    enroll(session, contact)
    assert len(pending_drafts(session)) == 1
    st = stats(session)
    assert st["messages_draft"] == 1 and st["companies"] == 6
