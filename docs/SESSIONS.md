# Durable session storage

`SessionStore` is the kernel's provider-independent checkpoint store ([ADR 0049](adr/0049-heinzelmaennchen-are-durable-agents-one-session-each-owned-by-hz.md)).
It stores events and opaque turn bodies; the calling module owns event kinds, state transitions, retention and model
messages. The kernel imports no agent SDK and assigns no business meaning to an event.

```python
from pathlib import Path

from heinzel import SessionStore

sessions = SessionStore(Path("/path/to/private/session-root"))
with sessions.writer("session-1") as writer:
    writer.append("turn.started", turn="turn-1", key="request-1", data={"version": 1})
    digest = writer.append_body("turn-1", {"messages": []})
    writer.append("step.finished", turn="turn-1", key="gather-1", data={"body_hash": digest})
```

The writer lock lasts for the context, so another process cannot start a concurrent turn in the same session. A
checkpoint takes a short global index lock, appends one JSON line, flushes and fsyncs it, then commits the index.
Different sessions can hold their own turn locks concurrently. Repeating a checkpoint's `(kind, key)` returns the
original event; using that identity with different data or another turn is refused. This deduplicates checkpoints,
not external effects: an adapter still needs the effect ledger and idempotency key described by ADR 0049.

`writer(session, wait=True)` waits for a short catalog or control journal writer; ordinary turn writers retain the
nonblocking default. The durable conversation repository separates creation catalogs, main turns and inbox/control
journals into private namespaces scoped by the authenticated principal. Its lock order is reservation (released),
then main writer, then short control writer, then the store's short index lock. Profile changes and message
acceptance take only the control writer and never wait for a main turn. Every visible message receives its control
journal sequence, including an assistant reply whose immutable body is stored in the main session. Profile and
control events may leave gaps in that message sequence; cursors preserve the committed order across those gaps.

Each session has `session.jsonl`, `lock` and `turns/<turn>.jsonl`. The root and subdirectories require mode 0700;
files require mode 0600 and the current user's ownership. File opens reject symlinks, nonregular files and hardlinks.
SQLite lives in the private root. Its inode is checked around opening; malicious concurrent directory replacement
by the same user is outside this boundary. Installed plugins already execute with that user's rights.

`read()` verifies every envelope and the previous-line hash before filtering kinds. Unknown event kinds remain in
the chain and callers can skip them; an unknown envelope version is reported as damage. A damaged session exposes
its verified prefix with a problem and refuses further appends; other sessions continue. No read truncates or repairs
a spine. Turn bodies are separate: expired content is a missing file, not a break in the event chain. Retention is
an explicit module operation, never a read side effect.

Before resuming a saved turn, read its body with `writer.body(turn, expected_hash=checkpoint_hash)`, using the hash
recorded in the corresponding spine checkpoint. This rejects edits even when the replacement remains valid JSON.
Reading without an expected hash is only for inspecting an active, not-yet-checkpointed body.

For a view that must stay readable during an active turn, use
`sessions.body(session_id, body_id, expected_hash=checkpoint_hash)`. It takes no writer lock and requires the saved
hash. The application should use immutable body ids per committed snapshot, so writing the next snapshot cannot
change the body a reader is viewing. Owner-only file and path validation is identical to the writer's reader; a
missing body is expired content and a changed or incomplete body is refused.

The derived `index.sqlite` contains event records and a watermark per session: sequence, tail hash and file
identity. `heads()` checks file metadata and reads no unchanged spine. Changed files are verified and their rows
rebuilt. A caller may supply a pure `project(previous, event)` reducer for its indexed metadata; change
`projection_version` when that reducer changes. `reindex()` rebuilds the projection from the spines and preserves
damage reports. Missing or corrupt indexes are reconstructed automatically; corrupt indexes are quarantined.

The chain detects broken links and incomplete writes. The retained index watermark also detects a shortened or
edited final checkpoint. This is an unkeyed accident detector: the user can recompute hashes, and complete final-line
deletion cannot be detected after both the original watermark and that line are lost. Restoring an index from scratch
cannot invent that missing evidence.

Crash tests kill a writer after the spine is durable but before indexing, kill it midway through a line, and check
process lock contention. A performance test lists 500 indexed sessions in under 50 ms; it runs with the `perf` marker.
