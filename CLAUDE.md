# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Главбух ИИ** — a Telegram bot for accounting/tax assistance in Kazakhstan. It answers questions about the RK Tax Code (НК РК), calculates payroll (ЗП, ИПН, ОПВ, ВОСМС, СО), VAT (НДС 16%), and depreciation. Uses Google Gemini for AI + Firebase Firestore for vector RAG.

## Running the Bot

**Locally (from project root):**
```bash
python -m bot.main
```

**Via Docker:**
```bash
docker-compose up -d        # start
docker-compose logs -f bot  # tail logs
docker-compose down         # stop
```

The bot uses **long polling**, not a webhook server — no HTTP port is exposed.

## Environment Variables (.env)

Required:
- `BOT_TOKEN` — Telegram bot token
- `GOOGLE_API_KEY` — Google Gemini API key
- `GEMINI_MODEL` — model name (default: `gemini-2.5-flash`)
- `FIREBASE_CREDENTIALS_PATH` — path to Firebase JSON key file
- `FIREBASE_CREDENTIALS_JSON` — alternative: full JSON string (used on Render/cloud)
- `ALLOWED_THREAD_ID` — comma-separated Telegram thread IDs the bot responds in (empty = respond everywhere)
- `ADMIN_ID` — Telegram user ID with admin rights (`/update_laws`, `/learn`, `/review`)

## Architecture

```
bot/
├── main.py                    # Entry point: Bot + Dispatcher + polling
├── handlers/
│   └── user_handlers.py       # All command/message handlers, single Router
├── services/
│   └── ai_service.py          # Gemini client, RAG pipeline, built-in calculator
└── rag/
    ├── firebase_db.py         # Firebase init, Firestore CRUD, cosine vector search
    ├── parser.py              # HTML parser for Tax Code (adilet.zan.kz)
    └── news_parser.py         # News aggregator (uchet.kz + adilet.zan.kz)
```

### Request Flow

1. User message → `user_handlers.py` → `get_ai_response()` in `ai_service.py`
2. Built-in calculator checked first (regex patterns for ЗП/НДС/амортизация) — no AI call
3. If not a calculator query: embed the user text via `gemini-embedding-2`
4. Parallel Firebase lookups: `knowledge_base` (vector similarity), `dialogs` (good-rated past dialogs), `news_feed` (recent news)
5. Context injected into Gemini prompt with system instruction containing hardcoded 2026 tax rates
6. Response saved to `dialogs` collection with rating buttons (👍/👎)

### Firebase Collections

| Collection | Purpose |
|---|---|
| `knowledge_base` | Tax code articles with embeddings (cosine search) |
| `dialogs` | All Q&A pairs with user ratings |
| `dialogs_review` | Bad-rated dialogs pending admin correction |
| `news_feed` | News articles from uchet.kz / adilet.zan.kz |
| `feedbacks` | User feedback from `/feedback` |
| `user_tasks` | Personal task list per user (`/task`, `/tasks`) |

Vector search uses in-memory cache (`_vector_cache` in `firebase_db.py`) loaded once at startup — no external vector DB.

### Tax Rates

All 2026 tax rates are hardcoded in `RATES_2026` dict in `bot/services/ai_service.py` and also embedded in the system prompt. Gemini is explicitly forbidden from using rates from its training data. When updating rates, change both `RATES_2026` and `_SYSTEM_INSTRUCTION`.

### Admin Commands

- `/update_laws` — manually trigger news parser → saves to `news_feed`
- `/learn [text]` — embed text and save to `knowledge_base`
- `/review` — show bad-rated dialogs from `dialogs_review`

### Thread Filtering

All handlers call `_is_allowed_thread(message)` first. If `ALLOWED_THREAD_ID` is set, the bot only responds in those Telegram forum thread IDs.

## Dependencies

Key packages (see `requirements.txt`):
- `aiogram==3.4.1` — Telegram bot framework (async)
- `google-genai>=1.0.0` — Google Gemini SDK (native, not LangChain)
- `firebase-admin==6.4.0` — Firestore
- `beautifulsoup4` + `lxml` — HTML parsing
- `apscheduler==3.10.4` — task scheduler (defined but wired in `scripts/`)
- `numpy` — cosine similarity for vector search
