# Cloud Scheduler for the weekly Telegram digest

## Goal

Reliably publish the approved business-news digest every Monday at 09:30 in
`Asia/Almaty`, even when Cloud Run has scaled to zero.

## Architecture

Google Cloud Scheduler sends an HTTP POST request to
`/internal/weekly-digest`. The request includes the existing
`INTERNAL_TICK_SECRET` in an `X-Internal-Secret` header. The Cloud Run handler
compares the supplied and configured secrets with a constant-time comparison
and rejects missing or incorrect credentials with HTTP 401.

After authentication, the handler invokes the existing weekly publication
service for chat `-1002318310296`, topic `1`. It derives the publication key
from the current date in `Asia/Almaty`. The existing atomic Firestore claim
continues to prevent duplicate publication if Cloud Scheduler retries a
request or an operator also triggers a live preview command.

The in-process APScheduler weekly job will not be registered in production.
This removes two competing scheduling mechanisms. The unrelated six-hour news
cache refresh remains unchanged.

## HTTP behaviour

- Method: `POST`
- Path: `/internal/weekly-digest`
- Authentication: `X-Internal-Secret` header
- Success: HTTP 200 with a small JSON response
- Invalid credentials or missing server secret: HTTP 401
- Publication failure: HTTP 500; Cloud Scheduler may retry

The endpoint does not return news text, credentials, or Telegram identifiers.
Exceptions are logged and the existing publication code notifies the admin
where applicable.

## Cloud Scheduler job

- Name: `weekly-business-digest`
- Region: `europe-west1`
- Schedule: `30 9 * * 1`
- Time zone: `Asia/Almaty`
- Target: the canonical Cloud Run URL plus `/internal/weekly-digest`
- Method: POST
- Retry policy: Google Cloud Scheduler defaults
- Header: `X-Internal-Secret` sourced from the project's existing deployment
  configuration

Cloud Scheduler is responsible only for waking and calling the service. All
selection, formatting, idempotency, and Telegram delivery remain inside the
application.

## Testing and verification

Automated tests cover rejection of missing/incorrect secrets, successful
invocation, publication parameters, and the absence of the weekly in-process
job. Tests must fail before production code is added and pass afterward.

Deployment verification includes:

1. Full local test suite.
2. New Cloud Run revision reaches Ready and receives 100% traffic.
3. Scheduler job reports the correct cron expression, time zone, URL, and
   enabled state.
4. A manual Scheduler execution succeeds without causing duplicate delivery;
   because today is Monday after 09:30, this verification may publish today's
   digest if one has not already been published.

## Scope

This change does not alter the approved digest content, source selection,
Telegram destination, or admin preview command. It does not keep a minimum
Cloud Run instance running.
