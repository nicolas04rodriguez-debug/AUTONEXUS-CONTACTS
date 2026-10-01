"""Telegram control surface. Only TELEGRAM_ALLOWED_USER_IDS can talk to it.

Commands:
  /add Name | domain.com | sector     add a company (classified automatically)
  /companies [sector] [tier]          list companies
  /find <company id|name>             find >= 3 emails for the company
  /draft <company id|name>            enroll the best contacts and draft step 1
  /queue                              review drafts with Approve / Skip buttons
  /send                               send approved messages now (respects cap + window)
  /stop <email>                       stop + suppress a contact
  /stats, /pause, /resume, /help
  Any other text -> LLM router -> one of the commands above.
  Send a CSV file -> import companies/contacts.
"""
from __future__ import annotations

import asyncio
import logging
from functools import wraps

from sqlalchemy import select

from .config import settings
from .db import init_db, session_scope
from .enrichment.finder import find_contacts
from .importers import import_csv, upsert_company
from .mailer import get_mailer
from .models import Company, Contact, Message, MessageStatus, Sector, Tier
from .scheduler import build_scheduler, daily_digest
from .sequences import approve, enroll, pending_drafts, send_due, skip, stats, stop_contact

log = logging.getLogger(__name__)
PAUSED = {"value": False}


def _resolve_company(session, ref: str) -> Company | None:
    ref = ref.strip()
    if ref.isdigit():
        return session.get(Company, int(ref))
    return session.scalar(select(Company).where(Company.name.ilike(f"%{ref}%")))


def _fmt_company(c: Company) -> str:
    emails = sum(1 for k in c.contacts if k.email)
    return f"#{c.id} {c.name} — {c.sector.value}/{c.tier.value} — {emails} emails — {c.status.value}"


def _fmt_draft(m: Message) -> str:
    c = m.contact
    return (f"✉️ Draft #{m.id} (step {m.step})\nTo: {c.full_name or '-'} <{c.email}> — {c.company.name} "
            f"[{c.company.sector.value}/{c.company.tier.value}]\nSubject: {m.subject}\n\n{m.body}")


# ------------------------------------------------------------- handlers --

def guarded(fn):
    @wraps(fn)
    async def wrapper(update, context):
        uid = update.effective_user.id if update.effective_user else None
        if not settings.telegram_allowed_user_ids or uid not in settings.telegram_allowed_user_ids:
            log.warning("rejected telegram user %s", uid)
            return
        try:
            return await fn(update, context)
        except Exception as e:  # noqa: BLE001
            log.exception("handler failed")
            await update.effective_message.reply_text(f"⚠️ {type(e).__name__}: {e}")
    return wrapper


HELP = __doc__.split("Commands:")[1]


@guarded
async def cmd_help(update, context):
    await update.message.reply_text("Urbex CRM bot. Commands:" + HELP)


@guarded
async def cmd_add(update, context):
    parts = [p.strip() for p in " ".join(context.args).split("|")]
    if not parts or not parts[0]:
        await update.message.reply_text("Usage: /add Name | domain.com | sector")
        return
    name = parts[0]
    domain = parts[1] if len(parts) > 1 else None
    sector = parts[2] if len(parts) > 2 else None
    with session_scope() as s:
        res = upsert_company(s, name, domain, source="telegram", sector=sector)
        c = res.company
        txt = f"{'Added' if res.created else 'Updated'} {_fmt_company(c)}\nReason: {c.tier_reason or '-'}"
    await update.message.reply_text(txt)


@guarded
async def cmd_companies(update, context):
    sector = tier = None
    for a in context.args:
        a = a.lower()
        if a in {s.value for s in Sector}:
            sector = Sector(a)
        elif a in {t.value for t in Tier}:
            tier = Tier(a)
    with session_scope() as s:
        q = select(Company).order_by(Company.id.desc()).limit(30)
        if sector:
            q = q.where(Company.sector == sector)
        if tier:
            q = q.where(Company.tier == tier)
        rows = [_fmt_company(c) for c in s.scalars(q)]
    await update.message.reply_text("\n".join(rows) or "No companies yet. /add Name | domain")


@guarded
async def cmd_find(update, context):
    ref = " ".join(context.args)
    with session_scope() as s:
        c = _resolve_company(s, ref)
        if not c:
            await update.message.reply_text("Company not found")
            return
        await update.message.reply_text(f"🔎 Finding contacts for {c.name}…")
        res = await asyncio.to_thread(find_contacts, s, c)
        lines = [f"{fc.first_name} {fc.last_name} — {fc.title} — {fc.email} ({fc.source}, {fc.email_status.value})" for fc in res.added]
        txt = f"Found {res.count} new contact(s) for {c.name}" + (f" (pattern: {res.pattern})" if res.pattern else "")
        if lines:
            txt += "\n" + "\n".join(lines)
        if res.errors:
            txt += "\n⚠️ " + "; ".join(res.errors)
    await update.message.reply_text(txt)


@guarded
async def cmd_draft(update, context):
    ref = " ".join(context.args)
    with session_scope() as s:
        c = _resolve_company(s, ref)
        if not c:
            await update.message.reply_text("Company not found")
            return
        contacts = sorted([k for k in c.contacts if k.email and not k.do_not_contact], key=lambda k: k.seniority_rank)
        if not contacts:
            await update.message.reply_text("No contacts with email. Run /find first.")
            return
        made = []
        for k in contacts[: settings.min_contacts_per_company]:
            out = enroll(s, k, personalize=True)
            if out.message:
                made.append(out.message)
        txt = f"Drafted {len(made)} message(s) for {c.name}. " + ("Send /queue to review." if any(m.status == MessageStatus.DRAFT for m in made) else "Auto-approved; will go out in the next window.")
    await update.message.reply_text(txt)


def _draft_keyboard(message_id: int):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"approve:{message_id}"),
        InlineKeyboardButton("⏭ Skip", callback_data=f"skip:{message_id}"),
    ]])


@guarded
async def cmd_queue(update, context):
    with session_scope() as s:
        drafts = pending_drafts(s, limit=5)
        if not drafts:
            await update.message.reply_text("Queue empty ✅")
            return
        for m in drafts:
            await update.message.reply_text(_fmt_draft(m), reply_markup=_draft_keyboard(m.id))


@guarded
async def on_button(update, context):
    q = update.callback_query
    await q.answer()
    action, _, mid = q.data.partition(":")
    with session_scope() as s:
        m = s.get(Message, int(mid))
        if not m:
            await q.edit_message_text("Message no longer exists")
            return
        if action == "approve":
            approve(s, m)
            await q.edit_message_text(f"✅ Approved #{m.id} → {m.contact.email}. Will send in the next window.")
        elif action == "skip":
            skip(s, m)
            await q.edit_message_text(f"⏭ Skipped #{m.id}")


@guarded
async def cmd_send(update, context):
    if PAUSED["value"]:
        await update.message.reply_text("Paused. /resume first.")
        return
    with session_scope() as s:
        sent = await asyncio.to_thread(send_due, s, get_mailer(), None, False)
        txt = f"Sent {len(sent)} message(s)." + ("" if sent else " Nothing due, or daily cap reached.")
    await update.message.reply_text(txt)


@guarded
async def cmd_stop(update, context):
    email = (context.args or [""])[0].lower()
    with session_scope() as s:
        k = s.scalar(select(Contact).where(Contact.email == email))
        if not k:
            await update.message.reply_text("Contact not found")
            return
        stop_contact(s, k, reason="manual")
    await update.message.reply_text(f"🛑 Stopped and suppressed {email}")


@guarded
async def cmd_stats(update, context):
    await update.message.reply_text(daily_digest())


@guarded
async def cmd_pause(update, context):
    PAUSED["value"] = True
    await update.message.reply_text("⏸ Sending paused (scheduler keeps drafting and checking replies).")


@guarded
async def cmd_resume(update, context):
    PAUSED["value"] = False
    await update.message.reply_text("▶️ Sending resumed.")


@guarded
async def on_document(update, context):
    doc = update.message.document
    if not doc.file_name.lower().endswith(".csv"):
        await update.message.reply_text("Send a .csv file (columns: name, domain, sector, city, brands, employees, revenue, description, contact_name, title, email)")
        return
    f = await doc.get_file()
    data = await f.download_as_bytearray()
    with session_scope() as s:
        res = import_csv(s, bytes(data).decode("utf-8-sig"), source="telegram_csv")
    await update.message.reply_text(f"📥 Imported: {res['created']} new, {res['updated']} updated, {res['contacts']} contacts.")


@guarded
async def on_text(update, context):
    from .llm import route_natural_language
    try:
        route = await asyncio.to_thread(route_natural_language, update.message.text)
    except Exception as e:  # noqa: BLE001
        await update.message.reply_text(f"Couldn't parse that ({e}). Try /help")
        return
    cmd, args = route.get("command", "help"), route.get("args", {}) or {}
    mapping = {
        "add_company": (cmd_add, [" | ".join(str(args.get(k, "")) for k in ("name", "domain", "sector"))]),
        "list_companies": (cmd_companies, [str(v) for v in (args.get("sector"), args.get("tier")) if v]),
        "find_contacts": (cmd_find, [str(args.get("company", ""))]),
        "draft": (cmd_draft, [str(args.get("company", ""))]),
        "queue": (cmd_queue, []), "send_now": (cmd_send, []), "stats": (cmd_stats, []),
        "pause": (cmd_pause, []), "resume": (cmd_resume, []),
        "stop_contact": (cmd_stop, [str(args.get("email", ""))]), "help": (cmd_help, []),
    }
    handler, argv = mapping.get(cmd, (cmd_help, []))
    context.args = [a for a in " ".join(argv).split(" ") if a] if cmd != "add_company" else argv[0].split(" ")
    await update.message.reply_text(f"→ {cmd} {args if args else ''}".strip())
    await handler(update, context)


def run() -> None:
    from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, filters

    if not settings.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN not set")
    if not settings.telegram_allowed_user_ids:
        raise SystemExit("TELEGRAM_ALLOWED_USER_IDS not set — refusing to start an open bot")
    init_db()
    state: dict = {"loop": None}

    async def post_init(application):
        state["loop"] = asyncio.get_running_loop()

    app = ApplicationBuilder().token(settings.telegram_bot_token).post_init(post_init).build()
    for name, fn in [("start", cmd_help), ("help", cmd_help), ("add", cmd_add), ("companies", cmd_companies),
                     ("find", cmd_find), ("draft", cmd_draft), ("queue", cmd_queue), ("send", cmd_send),
                     ("stop", cmd_stop), ("stats", cmd_stats), ("pause", cmd_pause), ("resume", cmd_resume)]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    owner = next(iter(settings.telegram_allowed_user_ids))

    def notify(text: str) -> None:
        if (PAUSED["value"] and text.startswith("✍️")) or state["loop"] is None:
            return
        asyncio.run_coroutine_threadsafe(app.bot.send_message(chat_id=owner, text=text), state["loop"])

    from .scheduler import tick as _tick

    def guarded_tick():
        if PAUSED["value"]:
            return
        _tick(notify)

    sched = build_scheduler(notify)
    sched.modify_job("tick", func=guarded_tick, kwargs={})
    sched.start()
    log.info("bot started")
    app.run_polling(allowed_updates=["message", "callback_query"])
