---
status: proposed
date: 2026-10-24
decision-makers: maintainer
amends: 0039
amended-by: 0084
related: 0019, 0029
---

# 0048: A terminal in the web head

## Context and Problem Statement

The web head (ADR 0039) shows what `hz` knows and lets you chat with the agent runner, but much of the work around
it still happens in a terminal: signing a vendor CLI in (`claude` then `/login`, `gws auth login`), running a setup
step, trying a command the app suggests. Switching to another window for that breaks the flow the app is meant to
keep, and desktop coding agents put a terminal next to their chat for the same reason. Should the web head offer one?

## Decision Drivers

- The terminal is a real shell on the user's own machine, so it must be no easier to reach than the user's account:
  only the app `hz ui` opened, never another web page or another user.
- Nothing new to install or run; it works on macOS and Linux, where `hz ui` runs.
- It looks and behaves like a terminal: colours, full-screen programs, resizing, copy and paste.

## Considered Options

- **A pseudo-terminal behind a WebSocket on the web head, rendered with xterm.js** (chosen)
- **No terminal; copy the command and switch windows**: what the app does today; the friction this record removes.
- **Run individual `hz` commands from the app**: covers only `hz` itself, not vendor CLIs' interactive sign-in, and
  would need a second, weaker sandbox story for each command.

## Decision Outcome

Chosen option: **a pseudo-terminal behind a WebSocket**, because it is the user's own shell, gated by the same checks
as the rest of the web head, with no new process to manage.

- `/api/terminal` is a WebSocket on the web head. Before it is accepted, the handshake must pass the API's checks: a
  loopback Host on our port, our own Origin (a browser always sends one with a WebSocket, so none is refused too), no
  cross-site `Sec-Fetch-Site`. A browser can't set headers on a WebSocket, so the session token arrives as the first
  message within five seconds and is compared in constant time.
- Each connection starts the user's login shell (`$SHELL` when absolute and executable, else `sh`) with `-l` in their
  home directory, in a session of its own whose controlling terminal is a new pseudo-terminal. Binary frames carry
  keystrokes and output; text frames carry `{"resize": [cols, rows]}`. At most eight terminals run at once; one more
  is closed with code 1013 (try again later).
- The forked child runs no Python, because `preexec_fn` is unsafe once the server has threads: `start_new_session`
  makes it a session leader, and it execs a fresh `python -I -S` that takes the pseudo-terminal as its controlling
  terminal (`TIOCSCTTY`, which macOS and Linux both need) and then execs the shell. The cost is one interpreter
  start per terminal, tens of milliseconds.
- The shell's environment is the user's, as a desktop terminal gives it: hz's virtual environment is taken out
  (`VIRTUAL_ENV`, `UV_*`, and hz's `bin` directory on `PATH`, so `python` and `pip` are the user's), `NO_COLOR` is
  dropped, and `TERM=xterm-256color`, `COLORTERM=truecolor`, `CLICOLOR=1` and `TERM_PROGRAM=heinzel` are set.
- Both directions are bounded. The shell's output waits in a queue of 64 reads; when it is full, the server stops
  reading the pseudo-terminal, so a shell printing faster than the browser reads (`yes`) blocks instead of growing
  memory. Keystrokes the pseudo-terminal can't take yet wait in a buffer of up to 1 MiB; beyond that the server
  stops reading the socket until the shell has caught up.
- Closing the socket, the panel or the app sends SIGHUP to the shell's process group, closes the pseudo-terminal,
  then sends SIGKILL to the group after two seconds; a server shutting down kills it at once. Jobs the shell runs
  in their own process groups get SIGHUP as when a terminal window closes: the kernel hangs up the terminal's
  foreground job, and shells like bash and zsh pass SIGHUP on to their other jobs. A job started with `nohup` or
  disowned survives, as it does there.
- The app renders it with xterm.js in a panel under the chat, opened from the header's terminal button. On Windows
  the socket closes with a reason the panel shows.
- uvicorn needs a WebSocket implementation; `wsproto` is the smallest (pure Python, on `h11`, which uvicorn uses).
- The Content Security Policy allows inline styles (`style-src 'self' 'unsafe-inline'`), because xterm.js writes its
  measurements into `<style>` elements. Scripts stay this origin's only, so injected markup still can't run code.

### Consequences

- Good: sign-ins, setup steps and suggested commands happen next to the app; the shell is the user's own, with their
  configuration and tools.
- Bad: the session token now opens a shell, not just `hz` operations. It already had to stay secret (it is kept in
  `sessionStorage`, sent only as a header or this first message, and dies with `hz ui`); a leak is now worse, which is
  why the handshake checks run before the socket is accepted.
- Bad: inline styles are allowed. Style injection can restyle the page but not run code or send data elsewhere
  (`connect-src`, `img-src` and `font-src` stay this origin's).

### Confirmation

Integration tests open the socket through the web head: a wrong Origin or a missing Origin is refused before
accepting, a wrong token closes it, the right token runs a command with a controlling terminal and returns its
output, and a resize reaches the terminal (`stty size`). `yes` against a browser that doesn't read leaves the output
queue bounded and reading paused, and the session recovers once the browser reads; 100 KiB of keystrokes all reach
`wc -c`; the shell is gone once the socket closes; a terminal beyond the cap is closed with 1013; and the shell's
environment has hz's virtual environment taken out and a desktop terminal's colour variables set.
