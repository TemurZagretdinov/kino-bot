# Render Background Worker deployment

This project is configured for a Telegram bot that uses polling with aiogram.
It does not need an HTTP server, public URL, webhook route, or `PORT`.

## Project structure

```text
bot.py
requirements.txt
render.yaml
.env.example
app/
  config.py
  database.py
  handlers/
  keyboards/
  models/
  services/
  states/
```

## Render service

Create a new Render **Background Worker**, not a Web Service.

Use these settings:

```text
Runtime: Python
Build Command: pip install -r requirements.txt
Start Command: python bot.py
Instance Type: Starter or higher
```

Set these environment variables in Render:

```text
BOT_TOKEN=...
ADMIN_IDS=123456789,987654321
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/database
ARCHIVE_CHANNEL_IDS=
```

Do not set these for the worker:

```text
PORT
BOT_MODE
WEBHOOK_BASE_URL
WEBHOOK_PATH
WEBHOOK_SECRET
```

## Database safety

The bot stores long-term data in the external PostgreSQL database named by
`DATABASE_URL`. Redeploying the Render worker rebuilds and restarts only the
application container. It does not delete a Supabase database, an external
PostgreSQL database, or a separate Render Postgres database.

Do not use local SQLite on Render for production data. Render service filesystems
are ephemeral, so local files can disappear on redeploy, restart, or spin-down.

## Before deploy checklist

- The service type is **Background Worker**.
- The start command is exactly `python bot.py`.
- `requirements.txt` does not include FastAPI or uvicorn unless another service
  actually needs them.
- `DATABASE_URL` is set to a PostgreSQL URL with the async driver:
  `postgresql+asyncpg://...`.
- The old Web Service is stopped or deleted after the worker is healthy, so only
  one polling process consumes Telegram updates.
- Render logs show `Bot polling rejimida ishga tushdi.`.
