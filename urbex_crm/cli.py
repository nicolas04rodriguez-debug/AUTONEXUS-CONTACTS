"""Command-line interface. Every command is also a tool an agent (Hermes, Claude
Code, ChatGPT with shell access) can call: deterministic args in, JSON out.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import typer
from sqlalchemy import select

from . import buckets
from .config import settings
from .db import init_db, schema_sql, session_scope
from .drafting import seed_templates
from .enrichment.finder import find_contacts
from .importers import import_csv, upsert_company
from .models import Company, Contact, Message, MessageStatus, Sector, Tier
from .sequences import (approve, check_replies, draft_due_followups, enroll, ensure_default_sequences,
                        pending_drafts, send_due, skip, stats, stop_contact)

app = typer.Typer(help="Urbex outreach CRM", no_args_is_help=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _out(obj) -> None:
    typer.echo(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _company(session, ref: str) -> Company:
    c = session.get(Company, int(ref)) if ref.isdigit() else session.scalar(select(Company).where(Company.name.ilike(f"%{ref}%")))
    if not c:
        raise typer.BadParameter(f"company not found: {ref}")
    return c


@app.command("init-db")
def init_db_cmd():
    """Create tables, seed templates and default sequences."""
    init_db()
    with session_scope() as s:
        n = seed_templates(s)
        seqs = ensure_default_sequences(s)
    _out({"ok": True, "templates": n, "sequences": [q.name for q in seqs], "database": settings.database_url})


@app.command("schema")
def schema_cmd(dialect: str = "sqlite", out: Path | None = None):
    """Print (or write) the SQL schema."""
    sql = schema_sql(dialect)
    if out:
        out.write_text(sql)
        _out({"written": str(out)})
    else:
        typer.echo(sql)


@app.command("add-company")
def add_company_cmd(name: str, domain: str = "", sector: str = "", tier: str = "", city: str = "", brands: str = "",
                    employees: int | None = None, revenue: float | None = None, description: str = ""):
    with session_scope() as s:
        r = upsert_company(s, name, domain or None, source="cli", sector=sector or None, tier=tier or None, city=city or None,
                           brands=brands or None, employee_count=employees, est_revenue_eur=revenue, description=description or None)
        c = r.company
        _out({"id": c.id, "name": c.name, "domain": c.domain, "sector": c.sector.value, "sub_sector": c.sub_sector,
              "tier": c.tier.value, "confidence": c.tier_confidence, "reason": c.tier_reason, "created": r.created})


@app.command("import-csv")
def import_csv_cmd(path: Path):
    with session_scope() as s:
        _out(import_csv(s, path.read_text(encoding="utf-8-sig"), source=f"csv:{path.name}"))


@app.command("classify")
def classify_cmd(ref: str = "", use_llm: bool = False, all_unknown: bool = False):
    """Re-run bucketing. --use-llm asks the model when rules are unsure."""
    with session_scope() as s:
        targets = [_company(s, ref)] if ref else list(s.scalars(select(Company).where((Company.tier == Tier.UNKNOWN) | (Company.sector == Sector.OTHER)))) if all_unknown else []
        out = []
        for c in targets:
            cl = buckets.classify(c.name, c.description, c.website, c.brands, c.employee_count, c.est_revenue_eur)
            if use_llm and buckets.needs_llm(cl):
                from .llm import classify_with_llm
                r = classify_with_llm(c.name, c.description, c.website)
                cl = buckets.Classification(Sector(r.get("sector", "other")), r.get("sub_sector"), Tier(r.get("tier", "unknown")), float(r.get("confidence", 0.5)), "llm: " + str(r.get("reason", "")))
            c.sector, c.sub_sector, c.tier, c.tier_confidence, c.tier_reason = cl.sector, cl.sub_sector, cl.tier, cl.confidence, cl.reason
            out.append({"id": c.id, "name": c.name, "sector": cl.sector.value, "tier": cl.tier.value, "confidence": cl.confidence, "reason": cl.reason})
        _out(out)


@app.command("companies")
def companies_cmd(sector: str = "", tier: str = "", status: str = "", limit: int = 50):
    with session_scope() as s:
        q = select(Company).order_by(Company.id.desc()).limit(limit)
        if sector:
            q = q.where(Company.sector == Sector(sector))
        if tier:
            q = q.where(Company.tier == Tier(tier))
        if status:
            q = q.where(Company.status == status)
        _out([{"id": c.id, "name": c.name, "domain": c.domain, "sector": c.sector.value, "tier": c.tier.value,
               "status": c.status.value, "emails": sum(1 for k in c.contacts if k.email)} for c in s.scalars(q)])


@app.command("find-contacts")
def find_contacts_cmd(ref: str = "", all_new: bool = False, target: int | None = None):
    """Find >= MIN_CONTACTS_PER_COMPANY emails per company (Apollo -> Hunter -> pattern guess)."""
    with session_scope() as s:
        targets = [_company(s, ref)] if ref else list(s.scalars(select(Company).where(Company.status == "new"))) if all_new else []
        out = []
        for c in targets:
            r = find_contacts(s, c, target=target)
            out.append({"company": c.name, "added": r.count, "pattern": r.pattern, "errors": r.errors,
                        "contacts": [{"name": f"{f.first_name} {f.last_name}".strip(), "title": f.title, "email": f.email, "status": f.email_status.value, "source": f.source} for f in r.added]})
        _out(out)


@app.command("contacts")
def contacts_cmd(ref: str):
    with session_scope() as s:
        c = _company(s, ref)
        _out([{"id": k.id, "name": k.full_name, "title": k.title, "role": k.role, "email": k.email, "status": k.email_status.value,
               "source": k.email_source, "confidence": k.email_confidence, "dnc": k.do_not_contact} for k in sorted(c.contacts, key=lambda k: k.seniority_rank)])


@app.command("draft")
def draft_cmd(ref: str, max_contacts: int | None = None, personalize: bool = False, auto_approve: bool | None = None):
    """Enroll the top contacts of a company and draft step 1."""
    with session_scope() as s:
        c = _company(s, ref)
        contacts = sorted([k for k in c.contacts if k.email and not k.do_not_contact], key=lambda k: k.seniority_rank)
        out = []
        for k in contacts[: max_contacts or settings.min_contacts_per_company]:
            r = enroll(s, k, personalize=personalize, auto_approve=auto_approve)
            out.append({"contact": k.email, "message_id": r.message.id if r.message else None, "status": r.message.status.value if r.message else None, "skipped": r.skipped_reason})
        _out(out)


@app.command("queue")
def queue_cmd(limit: int = 20):
    with session_scope() as s:
        _out([{"id": m.id, "step": m.step, "to": m.contact.email, "company": m.contact.company.name, "subject": m.subject, "body": m.body} for m in pending_drafts(s, limit)])


@app.command("approve")
def approve_cmd(message_ids: list[int]):
    with session_scope() as s:
        for mid in message_ids:
            m = s.get(Message, mid)
            if m and m.status == MessageStatus.DRAFT:
                approve(s, m)
        _out({"approved": message_ids})


@app.command("skip")
def skip_cmd(message_ids: list[int], reason: str = "skipped by user"):
    with session_scope() as s:
        for mid in message_ids:
            m = s.get(Message, mid)
            if m:
                skip(s, m, reason)
        _out({"skipped": message_ids})


@app.command("send-due")
def send_due_cmd(ignore_window: bool = False, limit: int | None = None):
    from .mailer import get_mailer
    with session_scope() as s:
        sent = send_due(s, get_mailer(), respect_window=not ignore_window, limit=limit)
        _out({"sent": [{"id": m.id, "to": m.contact.email, "step": m.step} for m in sent]})


@app.command("tick")
def tick_cmd():
    """One scheduler pass: check replies, draft due follow-ups, send approved."""
    from .scheduler import tick
    _out(tick())


@app.command("check-replies")
def check_replies_cmd():
    from .mailer import get_mailer
    with session_scope() as s:
        changed = check_replies(s, get_mailer())
        _out([{"enrollment": e.id, "contact": e.contact.email, "status": e.status.value} for e in changed])


@app.command("draft-followups")
def draft_followups_cmd():
    with session_scope() as s:
        _out([{"id": m.id, "step": m.step, "to": m.contact.email, "status": m.status.value} for m in draft_due_followups(s)])


@app.command("stop")
def stop_cmd(email: str, reason: str = "manual"):
    with session_scope() as s:
        k = s.scalar(select(Contact).where(Contact.email == email.lower()))
        if not k:
            raise typer.BadParameter("contact not found")
        stop_contact(s, k, reason=reason)
        _out({"stopped": email})


@app.command("stats")
def stats_cmd():
    with session_scope() as s:
        _out(stats(s))


@app.command("gmail-auth")
def gmail_auth_cmd():
    """Run the Gmail OAuth flow once; stores config/gmail_token.json."""
    from .mailer import GmailAPIMailer
    m = GmailAPIMailer()
    m.authorize(headless=False)
    _out({"authorized_as": m.profile_email()})


@app.command("run-bot")
def run_bot_cmd():
    """Start the Telegram bot + background scheduler."""
    from .telegram_bot import run
    run()


@app.command("run-scheduler")
def run_scheduler_cmd(minutes: int = 10):
    """Headless scheduler (no Telegram)."""
    import time
    from .scheduler import build_scheduler
    init_db()
    sched = build_scheduler(None, tick_minutes=minutes)
    sched.start()
    typer.echo(f"scheduler running every {minutes} min; Ctrl-C to stop")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        sched.shutdown()


if __name__ == "__main__":
    app()
