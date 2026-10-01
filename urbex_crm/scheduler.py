"""Background jobs: reply polling, follow-up drafting, sending, daily digest.

Run with `urbex run-bot` (jobs run inside the bot process) or `urbex run-scheduler`.
"""
from __future__ import annotations

import logging
from typing import Callable

from .config import settings
from .db import session_scope
from .mailer import get_mailer
from .sequences import check_replies, draft_due_followups, send_due, stats

log = logging.getLogger(__name__)

Notifier = Callable[[str], None]


def tick(notify: Notifier | None = None) -> dict:
    """One pass: replies -> draft follow-ups -> send approved. Safe to run every few minutes."""
    mailer = get_mailer()
    summary = {"replies": 0, "bounces": 0, "drafted": 0, "sent": 0, "pending_approval": 0}
    with session_scope() as s:
        changed = check_replies(s, mailer)
        for enr in changed:
            if enr.status.value == "replied":
                summary["replies"] += 1
                if notify:
                    notify(f"📬 Reply from {enr.contact.full_name or enr.contact.email} ({enr.contact.company.name}). Sequence stopped.")
            elif enr.status.value == "bounced":
                summary["bounces"] += 1
        drafted = draft_due_followups(s)
        summary["drafted"] = len(drafted)
        summary["pending_approval"] = sum(1 for m in drafted if m.status.value == "draft")
        if summary["pending_approval"] and notify:
            notify(f"✍️ {summary['pending_approval']} follow-up draft(s) waiting for approval. Send /queue.")
        sent = send_due(s, mailer)
        summary["sent"] = len(sent)
    log.info("tick: %s", summary)
    return summary


def daily_digest() -> str:
    with session_scope() as s:
        st = stats(s)
    return (
        f"📊 Urbex CRM digest\n"
        f"Companies: {st['companies']} | Contacts w/ email: {st['contacts_with_email']}\n"
        f"Active sequences: {st['enrollments_active']} | Replied: {st['enrollments_replied']} | Bounced: {st['enrollments_bounced']}\n"
        f"Sent today: {st['sent_today']}/{st['daily_cap']} | Drafts awaiting approval: {st['messages_draft']}"
    )


def build_scheduler(notify: Notifier | None = None, tick_minutes: int = 10):
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    sched = BackgroundScheduler(timezone=settings.send_timezone)
    sched.add_job(tick, "interval", minutes=tick_minutes, kwargs={"notify": notify}, id="tick", max_instances=1, coalesce=True)
    if notify:
        sched.add_job(lambda: notify(daily_digest()), CronTrigger(hour=8, minute=45), id="digest")
    return sched
