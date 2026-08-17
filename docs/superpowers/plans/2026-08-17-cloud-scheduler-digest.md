# Cloud Scheduler Digest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reliably trigger the Telegram business-news digest every Monday at 09:30 Asia/Almaty while Cloud Run may scale to zero.

**Architecture:** An authenticated aiohttp POST endpoint calls the existing idempotent publication service. Google Cloud Scheduler is the only weekly clock; the unrelated six-hour cache refresh remains in APScheduler.

**Tech Stack:** Python, aiohttp, aiogram, unittest, Cloud Run, Cloud Scheduler, Firestore.

---

## File structure

- Modify `bot/main.py`: authenticated handler, route registration, single scheduling mechanism.
- Modify `tests/test_news_digest_wiring.py`: endpoint and startup behavior.
- Modify `docs/weekly-news-digest.md`: production trigger documentation.

### Task 1: Authentication boundary

**Files:**
- Modify: `tests/test_news_digest_wiring.py`
- Modify: `bot/main.py`

- [ ] **Step 1: Write failing tests** for missing configured secret, missing header, and incorrect header. Each calls `weekly_digest_endpoint(request, bot, now=fixed_now)`, expects HTTP 401, and asserts `publish_digest` was not awaited.
- [ ] **Step 2: Verify RED** with `python -m unittest tests.test_news_digest_wiring.CloudSchedulerEndpointTests -v`; expect import failure because the handler does not exist.
- [ ] **Step 3: Implement minimal authentication** using `hmac.compare_digest` on `X-Internal-Secret` and `INTERNAL_TICK_SECRET`; return `web.json_response({"ok": False}, status=401)` for all authentication failures.
- [ ] **Step 4: Verify GREEN** with the same test command; expect all authentication tests to pass.
- [ ] **Step 5: Commit** `bot/main.py` and the test with message `feat: authenticate weekly digest trigger`.

### Task 2: Publication and route wiring

**Files:**
- Modify: `tests/test_news_digest_wiring.py`
- Modify: `bot/main.py`

- [ ] **Step 1: Write failing tests** proving a valid request awaits `publish_digest` once with chat `-1002318310296`, thread `1`, Almaty date key, and `test_mode=False`; success returns 200, exceptions return 500, and POST `/internal/weekly-digest` is registered by `create_app(bot, dispatcher)`.
- [ ] **Step 2: Verify RED** with `python -m unittest tests.test_news_digest_wiring.CloudSchedulerEndpointTests -v`; expect publication and route assertions to fail.
- [ ] **Step 3: Implement the minimal handler and app factory.** Read target IDs from environment, derive the key with `ZoneInfo("Asia/Almaty")`, log exceptions without response details, and register the route through the factory used by `main()`.
- [ ] **Step 4: Verify GREEN** with the same targeted command; expect all endpoint tests to pass.
- [ ] **Step 5: Commit** with message `feat: expose weekly digest scheduler endpoint`.

### Task 3: Remove the competing weekly clock

**Files:**
- Modify: `tests/test_news_digest_wiring.py`
- Modify: `bot/main.py`

- [ ] **Step 1: Write a failing startup test** that patches startup dependencies, invokes `on_startup`, and proves `news_update` is registered while `weekly_business_digest` is not.
- [ ] **Step 2: Verify RED** with `python -m unittest tests.test_news_digest_wiring.SchedulerWiringTests -v`; expect failure because startup still registers the weekly job.
- [ ] **Step 3: Remove only the weekly registration** from `on_startup` and correct the startup log. Remove the obsolete helper/test if nothing uses it.
- [ ] **Step 4: Verify GREEN** with `python -m unittest tests.test_news_digest_wiring -v`; expect all wiring tests to pass.
- [ ] **Step 5: Commit** with message `fix: use one weekly digest scheduler`.

### Task 4: Documentation and local verification

**Files:**
- Modify: `docs/weekly-news-digest.md`

- [ ] **Step 1: Document** the endpoint, header, cron, time zone, retries, idempotency, and removal of weekly APScheduler registration.
- [ ] **Step 2: Run full tests** with `python -m unittest discover -s tests -v`; expect zero failures/errors.
- [ ] **Step 3: Run syntax validation** with `python -m compileall -q bot tests`; expect exit 0.
- [ ] **Step 4: Run `git diff --check`**; expect no whitespace errors.
- [ ] **Step 5: Commit** documentation with message `docs: explain Cloud Scheduler digest trigger`.

### Task 5: Deploy and configure Google Cloud

**Files:** none.

- [ ] **Step 1: Deploy** source to service `glavbuh-bot`, project `glavbuh-bot`, region `europe-west1`, preserving environment and public Telegram webhook access; expect a Ready revision with 100% traffic.
- [ ] **Step 2: Enable** `cloudscheduler.googleapis.com` if needed; expect success.
- [ ] **Step 3: Create or update** job `weekly-business-digest` in `europe-west1`, cron `30 9 * * 1`, time zone `Asia/Almaty`, POST URL ending `/internal/weekly-digest`, and secret header sourced without printing its value.
- [ ] **Step 4: Verify without publishing:** inspect only nonsecret job fields and send an invalid-secret request expecting HTTP 401. Do not manually run the valid job.
- [ ] **Step 5: Verify production health:** Cloud Run Ready, correct Telegram webhook, zero pending updates, no startup errors; then push commits to `origin/main`.
