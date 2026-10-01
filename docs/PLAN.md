# Urbex Outreach CRM — Master Plan

This is the "brain" document. It describes what we are building, why each piece
exists, and how it fits together, so that any model (Hermes, Claude, ChatGPT,
Grok) or human can pick it up and keep building without re-deriving decisions.

## 1. Goal

Run B2B cold-email outreach for Urbex to **automotive dealerships
(concesionarios)** and **construction companies** in Spain, controlled entirely
**from a phone via Telegram**, without depending on a hosted CRM that the
university network blocks.

Concretely, for each target company we:

1. **Bucket** it: sector (automotive dealer / automotive services /
   construction / developer / materials) and income tier (high / mid / low).
2. **Find at least three email addresses**: the CEO / owner / gerente plus the
   next decision-makers (director comercial, marketing, operaciones).
3. **Draft a sector- and tier-specific email** (first touch) and up to three
   follow-ups, personalised with one fact about the company.
4. **Send from the Urbex Gmail / Google Workspace account**, threaded, within
   business hours, under a daily cap.
5. **Stop automatically** when someone replies, bounces or asks to be removed.
6. **Report** to Telegram: replies, drafts waiting for approval, daily digest.

## 2. Decisions (and why)

| Decision | Choice | Why |
|---|---|---|
| Phone interface | **Telegram bot** | Free Bot API, inline buttons for approve/skip, works on any network including campus Wi-Fi, trivial to secure with a user-ID allowlist. Hermes Agent also speaks Telegram, so the two can coexist. |
| Brain / LLM | **Provider-agnostic adapter** (`urbex_crm/llm.py`). Default Claude Opus 5.5 via the Anthropic SDK; ChatGPT, Grok (xAI) and Nous Hermes via one OpenAI-compatible HTTP client. | The LLM only does three narrow jobs (classify when rules are unsure, write a one-line hook, route natural language to a command). Any frontier model is good enough; swapping is one env var. Claude is the default for Spanish copy quality and refusal-safe JSON. |
| Database | **SQLite via SQLAlchemy** (file in `data/`). Postgres by changing `DATABASE_URL`. | One operator, < 100k rows, zero infra, trivially backed up (copy one file). The ORM keeps the Postgres path open for free. Schema in `schema.sql`. |
| Email sending | **Gmail API with OAuth** (fallback SMTP app password) | Sends from the real mailbox so replies land in the inbox, threads follow-ups properly, and lets us read threads back to detect replies and bounces. No third-party sender reputation to manage. |
| Email discovery | **Apollo → Hunter → pattern guess + MX → generic mailboxes** | Apollo is already connected to Nicolas's account and has the best Spanish SME coverage; Hunter gives the company pattern; guessing fills gaps for named people; generic mailboxes (ventas@, gerencia@) are the floor so every company reaches 3. |
| Bucketing | **Rules first, LLM second** | Brand portfolio is a near-perfect income proxy for dealers (Mercedes = high, Dacia = low). Size thresholds cover construction. Rules are explainable in Telegram (`tier_reason`); the LLM only resolves the leftovers. |
| Scheduling | **APScheduler inside the bot process** | One process to deploy. `tick` every 10 min: check replies → draft due follow-ups → send approved. |
| Hosting | **Any always-on box outside campus**: a 5 EUR VPS, Railway/Fly, Raspberry Pi, or an old laptop at home. Docker compose included. | The phone never needs to reach the box directly; Telegram is the relay. |

## 3. Data model (SQL)

```
companies ──< contacts ──< enrollments ──< messages
                               │
templates (sector, tier, step) ┘ sequences (delays per step)
events (audit log)   suppressions (do-not-contact: unsubscribes + bounces)
```

- `companies`: name, domain, sector, sub_sector, tier, tier_confidence,
  tier_reason, city, brands, employee_count, est_revenue_eur, status
  (new → enriched → in_sequence → replied / won / lost / do_not_contact).
- `contacts`: person at a company with role (ceo/owner/gm/sales/marketing/ops),
  seniority_rank (1 = best), email, email_status (verified / likely / guessed /
  bounced), email_source (apollo / hunter / guess / generic / csv), confidence.
- `templates`: Jinja2 subject + body per (sector family, tier, step, language).
- `sequences`: one per (family, tier): `step_delays_days = [3, 4, 7]` →
  step 1 day 0, step 2 day +3, step 3 day +7, step 4 (breakup) day +14.
- `enrollments`: contact × sequence; tracks current_step, next_send_at,
  gmail_thread_id, status (active / replied / bounced / completed / stopped).
- `messages`: one row per step; lifecycle draft → approved → sent (or failed /
  skipped). Stores Gmail ids and the RFC Message-ID for threading.

Full DDL: `schema.sql` (regenerate with `urbex schema`).

## 4. Pipeline, step by step

```
 CSV / Telegram /add / Apollo search
            │
            ▼
   importers.upsert_company ──► buckets.classify (rules) ──► llm.classify_with_llm (if unsure)
            │
            ▼
   enrichment.finder.find_contacts  (Apollo → Hunter → guess → generic)  ≥ 3 emails
            │
            ▼
   sequences.enroll ──► drafting.draft_for(step 1)  [+ llm.personalize_hook]
            │                      │
            │                      ▼
            │          Telegram /queue  ✅ Approve  ⏭ Skip      (first touch always approved by you
            ▼                                                      unless AUTO_APPROVE_FIRST_TOUCH)
   sequences.send_due  (window 09–18 Europe/Madrid, weekdays, DAILY_SEND_CAP, jitter)
            │
            ▼
   scheduler.tick every 10 min:
       check_replies (Gmail thread read)  → replied / bounced → stop + notify
       draft_due_followups                → steps 2-4 auto-approved by default
       send_due
```

## 5. Buckets

**Sector families** (templates are shared inside a family):

- `automotive_dealer` family: `automotive_dealer`, `automotive_services`
- `construction` family: `construction`, `construction_developer`, `construction_materials`

**Tier rules** (`urbex_crm/buckets.py`):

- Automotive: any premium brand (Mercedes, BMW, Audi, Porsche, Lexus, Tesla,
  Volvo, Jaguar/Land Rover, Cupra, …) → **high**; mainstream brands (VW, Seat,
  Toyota, Renault, Kia, …) → **mid**; Dacia-only or used/multi-brand lots →
  **low**. Size can only push the tier up.
- Construction and everything else: revenue ≥ 20M EUR or ≥ 100 staff → **high**;
  ≥ 3M EUR or ≥ 20 staff → **mid**; else **low**.
- Anything with sector `other` or tier `unknown` or confidence < 0.6 goes to the
  LLM (`urbex classify --all-unknown --use-llm`).

## 6. Email strategy

See `docs/EMAIL_PLAYBOOK.md` for the angles per bucket and the compliance rules.
Short version: 4 steps, plain text, < 120 words, one CTA, Spanish, opt-out line,
sender postal address, 40/day max from a warmed Workspace mailbox with
SPF/DKIM/DMARC.

## 7. Control surface (Telegram)

```
/add Autos Premium Madrid | autospremiummadrid.es | automotive_dealer
/companies automotive_dealer high
/find 12                → finds ≥ 3 emails, shows source + confidence
/draft 12               → enrolls top 3 contacts, drafts step 1
/queue                  → each draft with ✅ Approve / ⏭ Skip buttons
/send                   → send approved now (still respects cap)
/stats  /pause  /resume  /stop email@domain
<send a .csv>           → bulk import
"busca contactos de Motor Centro Sur"   → natural language, routed by the LLM
```

## 8. Agent integration (Hermes / Claude Code / ChatGPT)

The CLI is the tool surface: every command takes plain arguments and prints
JSON. An agent with shell access can run `urbex find-contacts 12`,
`urbex draft 12 --personalize`, `urbex queue`, `urbex approve 5 6 7`,
`urbex tick`. `docs/AGENT_TOOLS.md` lists them as tool definitions, and
`docs/HANDOFF_PROMPT.md` is a copy-paste prompt to continue development with
another model.

## 9. Build order / roadmap

- [x] Phase 0 — Repo, schema, bucketing rules, templates, sequence engine, tests.
- [x] Phase 1 — CLI, CSV import, email finder waterfall, Gmail API sender,
      Telegram bot with approve/skip, scheduler, reply/bounce detection.
- [ ] Phase 2 — Fill `config/pitch.yaml` with the real Urbex value proposition;
      create the Google Cloud OAuth client; run `urbex gmail-auth`; deploy with
      Docker on a VPS; warm up the mailbox 2 weeks at 10–15/day.
- [ ] Phase 3 — Company sourcing at scale: Apollo company search by
      industry + location (`apollo_mixed_companies_search`), Google Maps
      scrape of "concesionario <ciudad>", Registro Mercantil / eInforma for
      revenue → tier.
- [ ] Phase 4 — Reply triage with the LLM (interested / not now / wrong person /
      unsubscribe) and auto-suggested replies in Telegram.
- [ ] Phase 5 — Simple web dashboard (FastAPI + HTMX) when the phone UI is not
      enough; A/B test subjects per bucket; deliverability monitoring.

## 10. Risks and how we handle them

- **Spam / blacklisting**: cap 40/day, 90 s between sends, business hours,
  threaded follow-ups, plain text, opt-out honoured within one tick, bounce
  suppression. Never send from a personal @gmail.com at volume.
- **GDPR / LSSI (Spain)**: B2B outreach to corporate role addresses on
  legitimate-interest grounds, with clear identity, postal address and opt-out.
  Keep `suppressions` forever. Do not email personal addresses.
- **Wrong tier**: every classification stores a reason; `/companies` shows it;
  fix with `urbex add-company "<name>" --tier mid`.
- **Guessed emails**: marked `guessed`, lower priority than verified ones;
  bounces suppress and never retry.
- **Credential safety**: `.env`, `config/gmail_*.json` are git-ignored; the bot
  refuses to start without a Telegram user allowlist.
