# Telegram News Sources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reliable five-item digest from fresh `@prg_jur` and `@commentariuskz` posts, repair Gemini parsing, and publish the missed 18 August edition once.

**Architecture:** A focused Telegram public-page fetcher produces the existing raw candidate dictionaries. Existing normalization, ranking, formatting, Firestore idempotency, and delivery remain the trust boundary. Gemini ranking uses a JSON schema; package constraints ensure the deployed Google SDK has a compatible aiohttp API.

**Tech Stack:** Python, requests, BeautifulSoup, Google GenAI SDK, aiohttp, unittest, Cloud Run.

---

### Task 1: Parse public Telegram feeds

**Files:**
- Modify: `tests/test_news_digest_service.py`
- Modify: `bot/services/news_digest_service.py`

- [ ] Write failing tests with realistic Telegram HTML for exact timestamps, canonical post URLs, text/title extraction, two-channel aggregation, and invalid post rejection.
- [ ] Run `python -m unittest tests.test_news_digest_service.TelegramFeedTests -v`; expect import/assertion failures because the fetcher does not exist.
- [ ] Implement `parse_telegram_feed` and asynchronous `fetch_telegram_news` using `requests.get` in worker threads, the configured channel list, timeouts, and independent channel error handling.
- [ ] Run the targeted tests; expect all Telegram feed tests to pass.
- [ ] Commit with `feat: collect public Telegram news sources`.

### Task 2: Integrate Telegram as primary input

**Files:**
- Modify: `tests/test_news_digest_service.py`
- Modify: `bot/services/news_digest_service.py`

- [ ] Write a failing orchestration test proving `build_digest` combines Telegram candidates with the existing parser, normalizes the 24-hour window, and calls web fallback only when fewer than five usable candidates remain.
- [ ] Run the targeted orchestration test; expect failure because Telegram is not called.
- [ ] Add an injectable `telegram_fetcher` to `build_digest`, merge Telegram candidates before normalization, and retain verified web fallback.
- [ ] Run all digest service tests; expect zero failures.
- [ ] Commit with `feat: prioritize Telegram digest candidates`.

### Task 3: Make Gemini output and networking resilient

**Files:**
- Modify: `tests/test_news_digest_service.py`
- Modify: `bot/services/news_digest_service.py`
- Modify: `requirements.txt`

- [ ] Write failing tests proving control-character JSON is accepted and ranking sends `response_mime_type=application/json` plus a response schema.
- [ ] Run the targeted tests; expect failures under strict JSON parsing and absent schema configuration.
- [ ] Change JSON loading to `strict=False`, define the ranking schema, and pass it through the Generate Content configuration.
- [ ] Pin `aiohttp>=3.11.0,<4` so the deployed Google SDK has `ClientConnectorDNSError`; retain the existing request timeout.
- [ ] Run the complete test suite and syntax compilation; expect zero failures/errors.
- [ ] Commit with `fix: harden digest AI responses`.

### Task 4: Deploy and recover today's digest

**Files:**
- Modify: `docs/weekly-news-digest.md`

- [ ] Document the two public Telegram sources and fallback behavior.
- [ ] Commit documentation with `docs: describe Telegram news sources`.
- [ ] Merge the verified branch to `main`, rerun the complete suite, deploy Cloud Run, and confirm the new revision is Ready.
- [ ] Delete the paused retrying task `weekly-business-digest-20260818` before any valid trigger.
- [ ] Send one authenticated POST to `/internal/weekly-digest`, verify HTTP 200, publication success logs, Telegram webhook health, and Firestore duplicate protection.
- [ ] Keep the regular Monday Scheduler enabled, push `main`, and report the actual outcome.
