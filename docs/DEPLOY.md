# Deploy

## 0. Prerequisites

- Python 3.11+ (or Docker).
- A Telegram bot token from @BotFather and your numeric Telegram user id (ask
  @userinfobot).
- Gmail: in Google Cloud Console create a project → enable **Gmail API** →
  OAuth consent screen (External, add your address as test user) → Credentials
  → **OAuth client ID, Desktop app** → download JSON to
  `config/gmail_credentials.json`.
- API keys: Anthropic (or OpenAI / xAI / an OpenAI-compatible Hermes endpoint),
  Apollo, optionally Hunter.

## 1. Local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # fill it in
urbex init-db                   # tables + templates + sequences
urbex gmail-auth                # opens a browser once, stores config/gmail_token.json
urbex import-csv data/companies_example.csv
urbex find-contacts 1
urbex draft 1 --personalize
urbex queue && urbex approve 1 2 3
urbex send-due                  # or let the scheduler do it
urbex run-bot                   # Telegram bot + scheduler, foreground
pytest                          # 18 tests, no network
```

## 2. VPS with Docker (recommended)

```bash
git clone <repo> && cd <repo>
cp .env.example .env && nano .env
# do the Gmail OAuth on your laptop first, then copy config/gmail_token.json to the server
docker compose up -d --build
docker compose logs -f
```

`docker-compose.yml` mounts `data/` (SQLite file) and `config/` (OAuth token,
pitch.yaml). Back up by copying `data/urbex_crm.db`.

## 3. Switching to Postgres

Set `DATABASE_URL=postgresql+psycopg://user:pass@host:5432/urbex` and
`pip install psycopg[binary]`. `urbex init-db` creates the tables.
`urbex schema --dialect postgresql` prints the DDL.

## 4. Switching the LLM

| Provider | `.env` |
|---|---|
| Claude (default) | `LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL=claude-opus-5-5` |
| ChatGPT | `LLM_PROVIDER=openai`, `OPENAI_API_KEY`, `OPENAI_MODEL=gpt-5` |
| Grok | `LLM_PROVIDER=xai`, `XAI_API_KEY`, `XAI_MODEL=grok-4` |
| Hermes / any OpenAI-compatible server | `LLM_PROVIDER=hermes`, `OPENAI_COMPATIBLE_BASE_URL=https://.../v1`, `OPENAI_COMPATIBLE_API_KEY`, `OPENAI_COMPATIBLE_MODEL` |

## 5. Running inside Hermes Agent instead of the built-in bot

Hermes Agent already has a Telegram gateway and can run shell tools. Install
this package on the same box, give Hermes the tool list in
`docs/AGENT_TOOLS.md`, and run only the scheduler here (`urbex run-scheduler`)
so sends and reply checks keep happening. Hermes then calls `urbex ...`
commands on your behalf and you keep the same database.
