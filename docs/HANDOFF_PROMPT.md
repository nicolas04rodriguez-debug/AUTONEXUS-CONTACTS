# Handoff prompt (paste into Hermes / Claude / ChatGPT / Grok)

You are continuing development of **Urbex Outreach CRM**, a Python 3.11 project
in this repository. Read `docs/PLAN.md` first; it is the source of truth for
goals and decisions. Then skim `urbex_crm/models.py` (schema),
`urbex_crm/sequences.py` (engine) and `urbex_crm/telegram_bot.py` (UI).

Context:
- Owner: Nicolas (student; the university network blocks hosted CRMs, so the
  control surface is a Telegram bot and the engine runs on a VPS).
- Targets: Spanish automotive dealerships (concesionarios) and construction
  companies, bucketed by sector and income tier (high/mid/low).
- Per company we need ≥ 3 decision-maker emails (CEO/gerente first), a
  tier-specific first email and three follow-ups, sent from the Urbex Gmail /
  Workspace account via the Gmail API, with automatic stop on reply or bounce.
- Stack: SQLAlchemy + SQLite (Postgres-ready), Jinja2 templates in
  `templates/`, pitch copy in `config/pitch.yaml`, Apollo + Hunter + pattern
  guessing for emails, provider-agnostic LLM adapter in `urbex_crm/llm.py`,
  APScheduler, python-telegram-bot.
- Run `pytest` before and after every change; tests use an in-memory SQLite
  and a fake mailer, no network.

Rules:
- Keep the CLI (`urbex_crm/cli.py`) as the tool surface: new features get a CLI
  command that prints JSON, then a Telegram command.
- Never weaken the guardrails: Telegram user allowlist, daily cap, send window,
  suppressions, opt-out handling, first-touch approval.
- Spanish copy stays under 120 words, plain text, one CTA, opt-out line.
- Don't add a web framework unless the task explicitly asks for the dashboard
  in Phase 5 of the plan.

Next tasks, in order (see roadmap in `docs/PLAN.md`):
1. Company sourcing: `urbex source-apollo --industry "automotive dealers" --location "Madrid"`
   using Apollo's company search, inserting companies with `source=apollo`.
2. LLM reply triage: classify incoming replies (interested / not now / wrong
   person / unsubscribe) in `check_replies`, notify Telegram with a suggested answer.
3. Deliverability: a `urbex doctor` command that checks SPF/DKIM/DMARC of the
   sending domain and the Gmail token.
