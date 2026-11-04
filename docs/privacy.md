---
title: Privacy notice
description: What data is processed when you visit heinzel.team or use the hz app.
---

# Privacy notice

*Status: 1 November 2026.*

## In short

- Visiting this website sets **no cookies** and uses **no analytics, tracking or advertising services**. It loads
  **no fonts, scripts, images or videos from third parties**: your browser contacts only `heinzel.team`. A real browser
  checks this automatically with every change to the website.
- The website is hosted on **GitHub Pages**. GitHub necessarily processes your IP address when you visit
  (section 3).
- The **hz app** runs on your computer. It sends **no telemetry** to us (section 9).

## 1. Controller

The controller within the meaning of Art. 4(7) GDPR is:

Helmut Hoffer von Ankershoffen<br>
Fangschleusenstrasse 32<br>
15569 Woltersdorf<br>
Germany<br>
Email: [helmut@heinzel.team](mailto:helmut@heinzel.team)

Use the same address for any data protection question.

## 2. What we process ourselves

The website consists of static files. We run no server of our own, keep no access logs of our own and do not analyse
visits. The only data we receive ourselves is what you write to us (section 5), and, in future, reports you explicitly send (section 10).

## 3. Hosting on GitHub Pages

The website is served by GitHub Pages, a service of GitHub Inc., USA (part of Microsoft). According to GitHub, your IP
address is logged and stored for security purposes when you visit a page, whether or not you are signed in to GitHub.
GitHub's privacy statement says further technical details may be collected, such as the time of the request, the page
and the referrer. We have no influence on these logs.

- *Purpose:* secure and stable delivery of the project website.
- *Legal basis:* Art. 6(1)(f) GDPR (legitimate interest in publishing the website).
- *Recipient:* GitHub Inc., as processor or as an independent controller for its own security logs.
- *Third country:* USA. GitHub states that it participates in the EU-U.S. Data Privacy Framework (adequacy decision of
  the European Commission, Implementing Decision (EU) 2023/1795, Art. 45 GDPR) and otherwise relies on standard
  contractual clauses (Art. 46 GDPR).
- *Retention:* determined by GitHub.

We use neither cookies nor scripts for this, and in our checks GitHub sets no cookies when pages are fetched (see
section 6).

## 4. Domain and redirects: Cloudflare

The domain `heinzel.team` uses Cloudflare's name servers (Cloudflare, Inc., USA). When your device or internet provider
resolves the website's address, Cloudflare sees the IP address of the requesting DNS resolver and the name asked for.
The pages themselves are not served through Cloudflare; your browser connects directly to GitHub Pages.

Other domains that forward to this website are operated through Cloudflare. For that, Cloudflare processes the
visitor's IP address and the requested address to carry out the redirect.

- *Purpose:* reachability of the domains and redirects to the website.
- *Legal basis:* Art. 6(1)(f) GDPR.
- *Third country:* USA. Cloudflare states that it participates in the EU-U.S. Data Privacy Framework and relies on
  standard contractual clauses as a fallback.

## 5. Contact by email: Cloudflare Email Routing and Resend

**Incoming email** to addresses on `heinzel.team` is received by Cloudflare (Email Routing) and forwarded to a mailbox
of the controller. Cloudflare processes sender and recipient addresses, metadata and the message content as far as
needed for forwarding. Please do not send sensitive data by ordinary email.

**Outgoing email** from addresses on `heinzel.team` is sent through the Resend service (Resend, USA). Resend processes
recipient address, metadata and message content. In its data processing agreement Resend states that it applies
standard contractual clauses and refers to the EU-U.S. Data Privacy Framework.

- *Purpose:* answering your enquiry and communicating with you.
- *Legal basis:* Art. 6(1)(b) GDPR if your enquiry aims at a contract or pre-contractual measures, otherwise
  Art. 6(1)(f) GDPR (legitimate interest in answering enquiries).
- *Retention:* we delete your message once your enquiry is dealt with and no legal retention duty applies. Cloudflare and
  Resend apply their own periods.

## 6. Cookies and browser storage (§ 25 TDDDG)

The website sets **no cookies**.

The page theme (Material for MkDocs) writes one entry to your browser's local storage when a page opens (`localStorage`,
key ending in `__palette`). It holds the colour scheme, light or dark, so that it stays the same on the next pages.

The entry stays on your device, is not transmitted and holds no identifier. We regard it as strictly necessary to provide
the display switch that is offered (§ 25(2) no. 2 TDDDG), so we do not ask for consent. You can delete the entry in your
browser settings at any time.

Search and the copy buttons run entirely in your browser; search terms are not transmitted.

## 7. Links to other websites

The pages link to GitHub, to YouTube (soundscapes), and to third-party documentation. **Only when
you follow a link** do you leave this website, and the operator of the target site processes your data under its own
privacy notice (for YouTube: Google Ireland Limited or Google LLC). Videos or thumbnails from YouTube are neither
embedded nor preloaded on this website.

Images from other repositories (screenshots, status badges) are downloaded once when the website is built and served
from `heinzel.team`; your browser does not fetch them from third parties. Status badges therefore show the state at the
last build of the website, which is refreshed hourly.

## 8. Disclosure

Apart from the service providers named (GitHub, Cloudflare, Resend) we do not pass personal data on. We use no credit
agencies, no advertising networks and no social plug-ins.

## 9. The hz app (Heinzelmännchen)

The `hz` app is free software that you install and run on your own computer. We operate no service the app sends data
to. Having reviewed the source code (the Python package, the web interface and the desktop shell):

- **No telemetry.** The app contains no code that sends usage data, crash reports or metrics to us or to third parties; this is a
  project rule ([ADR 0027](adr/0027-nothing-is-sent-home.md)). The `hz report` command writes a local log in your state directory and copies it into your own team's repository only
  when you tell it to.
- **Check for new versions (automatic).** When you run `hz` in a terminal, the app asks `github.com` for the latest
  release in the background, at most once an hour (`api.github.com` only as a fallback, see
  [ADR 0031](adr/0031-update-check.md)). GitHub receives your IP address and the identifier `heinzel/<version>`; no
  access token is sent. The check does not run in CI environments. Turn it off with the
  `update_check` setting or `HZ_UPDATE_CHECK=false`.
- **Automatic updates (on; turn them off in the footer's Updates pane or in Settings).** Only while the web app's
  server (`hz ui serve`) runs, and only for an installed hz (never from a source checkout, under tests or in CI). Every 30
  minutes (plus up to 5 at random; the first 1 to 5 minutes after it starts) it asks:
  - `github.com`: one `HEAD` of the repository's `releases/latest` page, for the latest release's version; only if
    that fails, one GET to `api.github.com` for the same.
  - `pypi.org`: one GET for each plugin installed from PyPI (its release list), and, only when a newer hz exists, one
    GET for that hz release (when its files were uploaded).
  - `api.github.com`: only for an hz installed from a release tag and only when a newer one exists, up to three GETs
    (the release, its tag, and the tag object) for the commit the tag names; again up to three right before
    installing.
  - When installing, after a 30-second warning: `uv` downloads exactly the announced versions from PyPI (`pypi.org`,
    `files.pythonhosted.org`) or, for hz from a release tag, from `github.com`. It may also contact the git remote of a
    plugin you installed from git, to find the commit already installed; it installs nothing new from there.
  - What is sent: hz's own requests carry what any HTTP request does (your IP address) and the User-Agent
    `heinzel/<version>`; their addresses name the repository, the packages and the versions. No token, account,
    identifier or data of yours.
  - Turn automatic updates off with the `auto_update` setting or `HZ_AUTO_UPDATE=false`. With `update_check` off
    (`HZ_UPDATE_CHECK=false`), none of these requests is made
    ([ADR 0116](adr/0116-automatic-updates-are-scheduled-by-the-running-app-server.md)).
- **Plugin catalog and search (on your action).** The plugin area queries `api.github.com` for plugin repositories (the
  search term is part of the request); adding a plugin from PyPI sends the package name to `pypi.org`. If the GitHub CLI
  `gh` is installed, the GitHub queries run with your login there. Commands such as `hz system release` and
  `hz system changelog --newer` also query GitHub. If `GH_TOKEN` or `GITHUB_TOKEN` is set, the token goes only to
  `api.github.com`.
- **AI providers (your choice).** By default the app uses the Claude Code CLI on your computer, which talks to
  Anthropic; other providers (for example OpenAI) can be configured. Prompts and content go to the provider you choose
  and are subject to its terms.
- **Soundscapes.** The local web interface embeds YouTube only after you press Play on a streamed soundscape (by default
  through `youtube-nocookie.com`).
- **Local data.** Configuration, profile (handle, GitHub login, name, work email, if you enter them), chat history,
  telemetry logs and caches are files in your user folders; credentials are in the operating system's keychain. The app
  transfers data only if you set a destination (for example `[system.state] personal`) or use team features, and then
  by Git to your own repository.

We have not examined the behaviour of third-party plugins, of programs the app calls (Claude Code, Codex, `gh`, `uv`,
`git`) or of services you connect; their own privacy notices apply. If you use the app for other people, you are
responsible for their data yourself.

## 10. Reports from the app (not available yet)

**This feature does not exist yet.** Today no part of the app or the website processes such a report. This section says
what will apply once it ships. We will change it, with the status above, at that point.

In future you can send a report to the maintainer from the app, the command line, the MCP server or the API, for
example about a bug or a wish. Then:

- *What is sent:* the text of your report and, if you choose, a screenshot or a selected element of the interface.
  You see exactly what will be sent beforehand and approve it explicitly. Nothing leaves without your approval.
- *Nothing automatic:* there are no automatic reports, no crash reports and no telemetry. A report exists only when
  you trigger it.
- *Purpose:* to read and answer the report and to fix the problem or consider the suggestion.
- *Storage:* privately by the maintainer, on their own systems in Germany. Reports are not placed in public issue
  trackers.
- *Legal basis:* your consent, Art. 6(1)(a) GDPR, which you give by sending. You may withdraw it at any time with
  effect for the future (Art. 7(3) GDPR).
- *Retention:* up to 12 months after the report is closed, then we delete it. <!-- LAWYER: retention period is a proposal -->
- *Deletion:* write to [helmut@heinzel.team](mailto:helmut@heinzel.team) naming the report, and we delete it sooner.

The existing `hz report` command is unrelated: it only writes a local log (section 9).

Screenshots can show third parties' data or your work. Check the preview before you send.

## 11. Your rights

You have the right against the controller to:

- access (Art. 15 GDPR)
- rectification (Art. 16 GDPR)
- erasure (Art. 17 GDPR)
- restriction of processing (Art. 18 GDPR)
- data portability (Art. 20 GDPR)
- **object** to processing based on legitimate interests (Art. 21 GDPR)

You may withdraw any consent at any time with effect for the future (Art. 7(3) GDPR). We rely on no consent. Write to us
at [helmut@heinzel.team](mailto:helmut@heinzel.team).

You also have the right to lodge a complaint with a data protection supervisory authority (Art. 77 GDPR), in particular
in the Member State of your habitual residence, place of work or the place of the alleged infringement.

## 12. Obligation to provide data, automated decisions

You are not obliged to provide us with data. A website cannot technically be delivered without an IP address. There is
no automated decision-making, including profiling (Art. 22 GDPR).

## 13. Changes

We update this notice when the website or the law changes. The status at the top of this page applies; the change history
is in the
[Git repository](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/commits/main/docs/privacy.md).
