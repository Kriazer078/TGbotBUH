# Telegram sources and resilient digest generation

## Goal

Repair the failed 18 August digest and make the weekly publication reliable by
using fresh public posts from `@prg_jur` and `@commentariuskz` as primary news
sources.

## Source collection

The service fetches `https://t.me/s/prg_jur` and
`https://t.me/s/commentariuskz` with a normal browser user agent. It parses
each `.tgme_widget_message`, reading:

- the exact UTC publication time from `time[datetime]`;
- text from `.tgme_widget_message_text`;
- the post identifier from `data-post`;
- the canonical source URL `https://t.me/<channel>/<message-id>`.

Posts without an exact timestamp, stable post URL, or meaningful text are
discarded. The existing normalization layer enforces the strict 24-hour
window and deduplication. Telegram posts are marked as Kazakhstan sources;
the selection prompt decides whether an individual `@commentariuskz` post is
a world story.

## Selection policy

Telegram is the primary input. The digest selects five economy/business
stories, normally three or four Kazakhstan stories and no more than two major
world stories. Legal posts from `@prg_jur` are eligible only when they affect
businesses, employers, taxes, finance, trade, prices, or regulation. General
politics, crime, entertainment, promotions, and posts without substantive
text are excluded.

Existing verified websites remain a fallback when the Telegram feeds provide
too few relevant candidates. Every selected Telegram item links to its exact
post and names the channel as the source.

## Reliability repairs

Ranking uses Gemini structured JSON output because it does not invoke a search
tool. The response schema constrains `items`, `url`, `summary`, `importance`,
and `overview`. JSON parsing also tolerates raw control characters for
defensive compatibility.

The fallback Google Search path retains its existing verification boundary,
but its parser tolerates control characters and rejects truncated or malformed
payloads without crashing the whole collector. The invalid reference to
`aiohttp.ClientConnectorDNSError`, which is unavailable in the deployed
aiohttp version, is replaced with supported `aiohttp.ClientConnectionError`
handling. Client sessions must always be closed.

## Failure behavior

If one Telegram channel is unavailable, the other channel and verified web
fallback are used. If no verified candidates remain, the endpoint returns 500
and does not publish an empty or fabricated digest. The atomic Firestore claim
continues to prevent duplicate delivery.

## Testing

Tests are written before implementation and cover:

1. Parsing multiple Telegram posts with exact dates and canonical links.
2. Rejecting incomplete or stale posts through normalization.
3. Combining both Telegram channels as primary candidates.
4. Structured Gemini ranking and malformed JSON handling.
5. Supported aiohttp connection errors and session cleanup.
6. Existing publication, idempotency, formatting, and scheduler tests.

## Deployment and today's recovery

The failing one-off Cloud Task remains paused during repair. After all tests
pass, the fixed revision is deployed to Cloud Run. The retrying task is deleted
to eliminate duplicate attempts. A single authenticated request then triggers
the 18 August publication. Success is verified from HTTP status, Cloud Run
logs, Firestore idempotency behavior, and Telegram delivery. The regular
Monday Cloud Scheduler job remains enabled and unchanged.
