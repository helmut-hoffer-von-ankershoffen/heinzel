# Reopened WebKit navigation has not been explained

The failure in [run 41733702006, job 126257740165](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/actions/runs/41733702006/job/126257740165)
occurs before document commit. It is not evidence that the application's load handler or stale-token recovery failed.

The `stale-webkit` case paired successfully, reopened successfully while the first server ran, then failed on its first
navigation after the server restarted. A new persistent browser launched in 814 ms and reported `GET /` at 834 ms.
The 30-second navigation deadline expired with that request pending. An independent Node fetch then received 200 in
46 ms. The still-evaluable initial document was `about:blank`, complete, without a controlling worker.

Those observations do not establish whether the browser's request reached the socket, the server accepted it,
response headers arrived, or a registered worker intercepted the provisional navigation. A controller on the old
`about:blank` document cannot establish the worker state of an uncommitted destination. The server publishes its link
after `server.started`; the trace does not identify an announcement-before-listen race. Nor does a later successful
independent request prove that the earlier browser request was accepted or answered.

## Next observation

`HZ_REOPEN_DIAGNOSTICS=1` opts this one fixture into synthetic-server receipts. They record only the progress of
`GET /`: time, a Fetch Metadata navigation boolean, client port, response status and send completion. They exclude
request bodies, header text, URL queries, fragments, arbitrary paths and credentials. Receipt output is limited to
the failed driver invocation's last 30 events. A response send completion means the ASGI send returned; it does not
prove the browser received or committed the document.

The browser now reports navigation response headers separately from request completion and prints the pending
navigation's Playwright timing fields on failure. The original persistent profile, allowed service workers, stale
session injection, assertions and all deadlines remain unchanged. No retry or browser preference is added.

Run the existing `stale-webkit` case once with that environment variable when a diagnostic run is warranted. A
passing run is a diagnostic observation, never a fix or evidence that the incident is resolved. If the navigation
has no server receipt, investigate the owned browser's worker/network initialization. If server receipts show a
request without a send completion, investigate the owned server. A send completion without a browser response
requires transport or browser process evidence. Sample only processes whose relationship to that test's browser
has been established; a matching WebKit process name alone does not establish ownership.

## Upstream comparisons

The installed package for the incident is Playwright Core 1.64.1. Its
[versioned WebKit backend](https://github.com/microsoft/playwright/blob/v1.64.1/packages/playwright-core/src/server/webkit/wkBrowser.ts)
identifies browser version 27.2.

- [Issue 42273](https://github.com/microsoft/playwright/issues/42273) describes navigation requests without responses
  around service-worker cache writes, but its old-version reload reproducer and recovery through a new context do
  not establish the cause of a fresh persistent-browser launch in 1.64.1.
- [Issue 42385](https://github.com/microsoft/playwright/issues/42385) reports a different symptom: no request event.
- [Issue 41870](https://github.com/microsoft/playwright/issues/41870) includes a cookie-storage/network initialization
  deadlock hypothesis on older WebKit builds. Maintainers could not reproduce it. No native stack from this incident
  supports that hypothesis, and no sandbox or preference modification is justified.

These changes supply diagnostics only. No cause or product fix is claimed.

## Local observations

One isolated macOS run of the original `stale-webkit` case with receipts enabled passed in 14.16 seconds. The server
recorded four document requests and one non-document shell request, each with a 200 response and send completion.
This does not reproduce or resolve the CI incident.

A separate controlled experiment withheld the first browser document response while answering the independent
health probe. The unchanged driver reproduced the incident's observable signature: a navigation request, a
30-second timeout, an independent 200 response and a complete `about:blank` document. No navigation response event
was observed. Playwright's pending request timing reported `startTime: 0` and every phase as `-1`, even though this
experiment's server had received the browser request. Missing timing phases therefore do not establish that the
request was never sent.

Process observation was limited to descendants of this experiment's gate process. A one-second sample of its owned
browser showed inspector/run-loop/display-link frames; it was a control observation of a deliberately unanswered
request, not a sample of the CI failure. Auxiliary WebKit processes were not proven descendants and were not sampled.
Five focused receipt tests pass, covering privacy, navigation/probe classification, streaming completion and
unrelated-path/protocol pass-through.
