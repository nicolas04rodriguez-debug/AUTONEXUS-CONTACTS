# Urbex Outreach CRM

Cold-email CRM for Urbex, run from your phone. Buckets Spanish **automotive
dealerships** and **construction companies** by sector and income tier, finds at
least three decision-maker emails per company, drafts tier-specific Spanish
emails plus three follow-ups, sends them from your Gmail / Google Workspace
account, and stops the moment someone replies. Controlled through a **Telegram
bot**; an LLM (Claude by default, ChatGPT / Grok / Hermes by config) classifies
edge cases, writes one-line hooks and routes natural-language commands.

Start with **`docs/PLAN.md`** — the full plan, decisions and roadmap.

```
docs/PLAN.md            the brain: goals, decisions, data model, pipeline, roadmap
docs/EMAIL_PLAYBOOK.md  angles per bucket, sending rules, Spanish/EU compliance
docs/DEPLOY.md          local + Docker/VPS setup, Gmail OAuth, switching LLM/DB
docs/AGENT_TOOLS.md     CLI commands as tools for Hermes / Claude Code / ChatGPT
docs/HANDOFF_PROMPT.md  prompt to hand development to another model
schema.sql              SQL DDL (SQLite; `urbex schema --dialect postgresql` for Postgres)
urbex_crm/              the package (see below)
templates/              Jinja2 email templates per family/tier/step
config/pitch.yaml       the Urbex value proposition per bucket (edit this first)
data/companies_example.csv
tests/                  18 offline tests
```

## Quick start

```bash
pip install -e ".[dev]"
cp .env.example .env         # Telegram token + your user id, LLM key, Apollo key, FROM_EMAIL
urbex init-db
urbex gmail-auth             # one-time OAuth; needs config/gmail_credentials.json (see docs/DEPLOY.md)
urbex import-csv data/companies_example.csv
urbex find-contacts --ref 1  # Apollo → Hunter → pattern guess, until ≥ 3 emails
urbex draft 1 --personalize  # enroll top 3 contacts, draft step 1
urbex queue                  # review; urbex approve 1 2 3
urbex run-bot                # Telegram bot + scheduler (replies, follow-ups, sends)
```

From Telegram: `/add Name | domain | sector`, `/find 1`, `/draft 1`, `/queue`
(✅ Approve / ⏭ Skip buttons), `/send`, `/stats`, or just type
"busca contactos de Motor Centro Sur".

## Package map

| Module | Role |
|---|---|
| `models.py` | SQLAlchemy schema: companies, contacts, templates, sequences, enrollments, messages, events, suppressions |
| `buckets.py` | Sector + tier rules (brand portfolio, revenue, headcount) with reasons |
| `importers.py` | CSV import and company upsert + auto-classification |
| `enrichment/finder.py` | Email waterfall: Apollo → Hunter → pattern guess + MX → generic mailboxes |
| `drafting.py` | Template selection, Jinja2 rendering, pitch.yaml, LLM hook |
| `sequences.py` | Enroll, approve, send window + daily cap, threading, follow-ups, reply/bounce stop |
| `mailer.py` | Gmail API (OAuth) sender + thread reader; SMTP fallback |
| `llm.py` | Anthropic SDK + OpenAI-compatible adapter (OpenAI, xAI Grok, Hermes) |
| `scheduler.py` | APScheduler tick + daily digest |
| `telegram_bot.py` | Phone UI with user allowlist and inline approval |
| `cli.py` | `urbex ...` commands, JSON output (the agent tool surface) |

## Safety defaults

Telegram user allowlist required; 40 sends/day; 09:00–18:00 Europe/Madrid
weekdays; first-touch emails need your approval; replies, bounces and "baja"
stop a contact and add it to `suppressions`; secrets and tokens are git-ignored.
