"""Chat sessions on disk: one private directory per session in `<state_dir>/chat`, bounded and pruned.

`session.json` holds the conversation and the person's decisions; only the process answering or confirming writes
it, under the session's lock. `proposals.jsonl` holds what the agent proposed; the chat's MCP server, a separate
process, appends to it under its own lock while a message is being answered (ADR 0045).
"""

import contextlib
import fcntl
import json
import os
import secrets
import shlex
import shutil
import stat
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import JsonValue, ValidationError

from heinzel import ActionGate, HeinzelError, Kernel, ProposedCall, Settings
from heinzel.kernel import McpChatSessions
from heinzel.modules.system.runner._models import RunnerSettings
from heinzel.modules.system.runner.chat._models import (
    ChatAction,
    ChatContext,
    ChatCosts,
    ChatMessage,
    ChatSession,
    ChatSettings,
    ChatSummary,
)
from heinzel.modules.system.runner.chat._policy import guarded_settings, refusal

PRIVATE_DIR = 0o700
PRIVATE_FILE = 0o600
MAX_PROPOSALS = 100
"""Actions an agent may propose in one session; more is refused, so a looping agent can't fill the disk."""
MAX_MESSAGES = 200
"""Messages a session may hold; then it is full and the person opens a new one."""
SESSION = "session.json"
PROPOSALS = "proposals.jsonl"
LOCK = "session.lock"


def now() -> datetime:
    """The current time, in UTC."""
    return datetime.now(UTC)


def _private_dir(path: Path) -> Path:
    path.mkdir(mode=PRIVATE_DIR, parents=True, exist_ok=True)
    if not stat.S_ISDIR(path.lstat().st_mode):
        msg = "The private chat store is unavailable"
        raise HeinzelError(msg)
    path.chmod(PRIVATE_DIR)
    return path


def _write_private(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(12)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, PRIVATE_FILE)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(PRIVATE_FILE)
        temporary.replace(path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


class ChatStore(McpChatSessions):
    """Reads, writes and prunes chat sessions."""

    def __init__(self, settings: Settings, chat: ChatSettings, runner: RunnerSettings) -> None:
        """Keep sessions under the state directory, within the chat settings' bounds."""
        self.root = settings.state_dir / "chat"
        self._chat = chat
        self._runner = runner

    def directory(self, session: str) -> Path:
        """The directory of `session`.

        Raises:
            HeinzelError: If `session` is not a session id, or no such session exists.
        """
        if len(session) != 16 or any(c not in "0123456789abcdef" for c in session):  # noqa: PLR2004 - token_hex(8)
            msg = f"{session!r} is not a chat session id; `hz chat list` shows them."
            raise HeinzelError(msg)
        path = self.root / session
        try:
            root_is_directory = stat.S_ISDIR(self.root.lstat().st_mode)
            session_is_directory = stat.S_ISDIR(path.lstat().st_mode)
            session_file_is_regular = stat.S_ISREG((path / SESSION).lstat().st_mode)
        except OSError:
            root_is_directory = session_is_directory = session_file_is_regular = False
        if not root_is_directory or not session_is_directory or not session_file_is_regular:
            msg = f"There is no chat session {session}; `hz chat list` shows them."
            raise HeinzelError(msg)
        return path

    def create(self, context: ChatContext | None) -> ChatSession:
        """A new, empty session, saved; then sessions beyond the retention are removed."""
        session = secrets.token_hex(8)
        _private_dir(self.root)
        _private_dir(self.root / session)
        created = now()
        title = f"{context.kind} {context.ref}".strip() if context else "New chat"
        chat = ChatSession(
            id=session, title=title, created=created, updated=created, context=context, costs=self.costs([])
        )
        self.save(chat)
        self.prune()
        return chat

    def load(self, session: str) -> ChatSession:
        """A session with every action its agent proposed so far.

        Raises:
            HeinzelError: If there is no such session, or its file is unreadable.
        """
        path = self.directory(session)
        try:
            chat = ChatSession.model_validate_json((path / SESSION).read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            msg = f"Chat session {session} can't be read ({exc.__class__.__name__}); `hz chat remove {session}`."
            raise HeinzelError(msg) from None
        if chat.id != session:
            msg = f"Chat session {session} holds another session's file; `hz chat remove {session}`."
            raise HeinzelError(msg)
        known = {action.id for action in chat.actions}
        chat.actions += [action for action in self.proposals(session) if action.id not in known]
        for action in chat.actions:  # what the person reads is derived from what runs, never stored text
            action.command = describe(action.module, action.operation, action.arguments)
        chat.costs = self.costs(chat.messages)
        return chat

    def require(self, session_id: str) -> None:
        """Validate existence without exposing this head's private chat model."""
        self.load(session_id)

    def is_check_in(self, session_id: str) -> bool:
        """Whether a local chat is bound to a durable personal check-in."""
        context = self.load(session_id).context
        return context is not None and context.kind == "check-in" and context.timezone is not None

    def save(self, chat: ChatSession) -> None:
        """Write `chat` atomically, readable by this user only.

        Raises:
            HeinzelError: If the session was removed meanwhile; it is never recreated half.
        """
        folder = self.root / chat.id
        if not folder.is_dir():
            msg = f"Chat session {chat.id} was removed meanwhile; open a new one."
            raise HeinzelError(msg)
        chat.updated = now()
        chat.costs = self.costs(chat.messages)
        _write_private(folder / SESSION, chat.model_dump_json(indent=1) + "\n")

    def save_mcp_approval(self, session: str, action: str, proof: str, expires_at: datetime) -> None:
        """Keep a one-use approval proof private for the MCP consumer; never return it to a head or model."""
        folder = self._mcp_approval_directory(session)
        _write_private(folder / f"{action}.proof", f"{proof}\n{expires_at.isoformat()}")

    def prepare_mcp_approval(self, session: str) -> None:
        """Validate the private handoff location before asking the issuer to create a proof."""
        self._mcp_approval_directory(session)

    def take_mcp_approval(self, session: str, action: str) -> tuple[str, datetime] | None:
        """Atomically consume the private proof for one action while its session lock is held."""
        path = self._mcp_approval_directory(session) / f"{action}.proof"
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            descriptor = os.open(path, flags)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                os.close(descriptor)
                return None
            with os.fdopen(descriptor, "r", encoding="ascii") as handle:
                proof, expiry = handle.read(512).splitlines()
            expires_at = datetime.fromisoformat(expiry)
        except FileNotFoundError:
            return None
        except OSError:
            return None
        except ValueError:
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
            return None
        path.unlink()
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            return None
        return proof, expires_at

    def remove_mcp_approval(self, session: str, action: str) -> None:
        """Discard any unconsumed handoff proof when an action is declined or resolved."""
        with contextlib.suppress(FileNotFoundError):
            (self._mcp_approval_directory(session) / f"{action}.proof").unlink()

    def _mcp_approval_directory(self, session: str) -> Path:
        """The private handoff directory; reject symlinks so a model cannot redirect bearer storage.

        Raises:
            HeinzelError: If the handoff directory is a symlink or cannot be resolved safely.
        """
        folder = self.directory(session) / "mcp-approvals"
        folder.mkdir(mode=PRIVATE_DIR, exist_ok=True)
        if not stat.S_ISDIR(folder.lstat().st_mode):
            msg = "The private MCP approval store is unavailable"
            raise HeinzelError(msg)
        folder.chmod(PRIVATE_DIR)
        return folder

    def costs(self, messages: list[ChatMessage]) -> ChatCosts:
        """What `messages` cost, the session's cap, and the most the next message may cost."""
        # An answer whose cost wasn't reported (stopped at its timeout, or a runner that doesn't say) counts at its cap.
        spent = round(sum(m.cost_usd if m.cost_usd is not None else (m.budget_usd or 0.0) for m in messages), 6)
        cap = self._chat.session_max_usd
        return ChatCosts(
            spent_usd=spent,
            session_max_usd=cap,
            message_max_usd=round(max(0.0, min(self._runner.ask_max_usd, cap - spent)), 6),
        )

    @contextlib.contextmanager
    def held(self, folder: Path) -> Generator[bool]:
        """Hold a session's lock while the block runs.

        Yields:
            True while holding it; False, without waiting, when another process holds it.
        """
        descriptor = os.open(folder / LOCK, os.O_WRONLY | os.O_CREAT, PRIVATE_FILE)
        with os.fdopen(descriptor, "w") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    @contextlib.contextmanager
    def locked(self, session: str) -> Generator[ChatSession]:
        """The session, held exclusively while the block runs, so one process at a time answers or confirms.

        Yields:
            The session, as it is on disk now.

        Raises:
            HeinzelError: If there is no such session, or another process holds it.
        """
        with self.held(self.directory(session)) as holding:
            if not holding:
                msg = f"Chat session {session} is busy right now; try again when the current operation is done."
                raise HeinzelError(msg)
            yield self.load(session)

    def proposals(self, session: str) -> list[ChatAction]:
        """What the agent proposed in `session`, oldest first; unreadable lines are skipped."""
        path = self.root / session / PROPOSALS
        if not path.is_file():
            return []
        actions: list[ChatAction] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            with contextlib.suppress(ValidationError):
                actions.append(ChatAction.model_validate_json(line))
        return actions

    def propose(self, call: ProposedCall) -> str:
        """Append `call` to the session's proposals, as pending, and return its id.

        Raises:
            HeinzelError: If there is no such session, or it has too many proposals.
        """
        path = self.directory(call.session) / PROPOSALS
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, PRIVATE_FILE)
        with os.fdopen(descriptor, "r+", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)  # the agent may call tools in parallel: one id each
            try:
                chat = self.load(call.session)
                if (
                    chat.context is not None
                    and chat.context.kind == "check-in"
                    and any(
                        action.module == "heinzel"
                        and action.operation == "propose-check-in"
                        and action.status in {"pending", "running"}
                        for action in chat.actions
                    )
                ):
                    msg = "This check-in already has an action awaiting a decision or recovery"
                    raise HeinzelError(msg)
                handle.seek(0)
                count = sum(1 for line in handle if line.strip())
                if count >= MAX_PROPOSALS:
                    msg = f"This chat has {count} proposed actions, the most it takes; open a new chat."
                    raise HeinzelError(msg)
                action = ChatAction(
                    id=f"a{count + 1}",
                    tool=call.tool,
                    module=call.module,
                    operation=call.operation,
                    command=call.command,
                    arguments=call.arguments,
                    proposed=now(),
                )
                handle.write(action.model_dump_json() + "\n")
                handle.flush()
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
        return action.id

    @contextlib.contextmanager
    def proposal_request_lock(self) -> Generator[None]:
        """Serialize idempotent schedule proposal lookup and creation across processes."""
        _private_dir(self.root)
        descriptor = os.open(self.root / ".proposal-requests.lock", os.O_WRONLY | os.O_CREAT, PRIVATE_FILE)
        with os.fdopen(descriptor, "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def find_proposal(
        self, module: str, operation: str, session_id: str, request_id: str
    ) -> tuple[ChatSession, ChatAction] | None:
        """Find an existing proposal by its operation request id, if any."""
        paths = sorted(self.root.glob(f"*/{SESSION}")) if self.root.is_dir() else []
        for path in paths:
            with contextlib.suppress(HeinzelError):
                chat = self.load(path.parent.name)
                found = next(
                    (
                        action
                        for action in chat.actions
                        if action.module == module
                        and action.operation == operation
                        and action.arguments.get("session_id") == session_id
                        and action.arguments.get("request_id") == request_id
                    ),
                    None,
                )
                if found is not None:
                    return chat, found
        return None

    def find_context(self, kind: str, ref: str, request_id: str) -> ChatSession | None:
        """Find a scoped chat opened by one idempotency key, if any.

        Raises:
            HeinzelError: If duplicate contexts already exist for this key.
        """
        found: ChatSession | None = None
        for summary in self.sessions():
            context = summary.context
            if context is None or (context.kind, context.ref, context.request_id) != (kind, ref, request_id):
                continue
            if found is not None:
                msg = "More than one chat has this durable-session request id; inspect the chat list before continuing"
                raise HeinzelError(msg)
            found = self.load(summary.id)
        return found

    def sessions(self) -> list[ChatSummary]:
        """Every session, most recently changed first; unreadable ones are left out."""
        summaries: list[ChatSummary] = []
        found: list[Path] = sorted(self.root.glob(f"*/{SESSION}")) if self.root.is_dir() else []
        for path in found:
            with contextlib.suppress(HeinzelError):
                chat = self.load(path.parent.name)
                summaries.append(
                    ChatSummary(
                        id=chat.id,
                        title=chat.title,
                        updated=chat.updated,
                        messages=len(chat.messages),
                        pending=len(chat.pending),
                        spent_usd=chat.costs.spent_usd,
                        context=chat.context,
                    )
                )
        return sorted(summaries, key=lambda summary: summary.updated, reverse=True)

    def remove(self, session: str) -> None:
        """Delete a session and everything it holds.

        Raises:
            HeinzelError: If there is no such session.
        """
        with self.locked(session):
            shutil.rmtree(self.directory(session))

    def prune(self) -> list[str]:
        """Remove sessions unchanged for longer than `keep_days`, and the oldest beyond `keep_sessions`.

        Returns:
            The ids removed.
        """
        if not self.root.is_dir():
            return []
        cutoff = now() - timedelta(days=self._chat.keep_days)
        found: list[tuple[float, Path]] = []
        for path in self.root.iterdir():
            marker = path / SESSION
            if path.is_dir() and marker.is_file():
                found.append((marker.stat().st_mtime, path))
            elif path.is_dir() and not path.name.startswith("."):
                with contextlib.suppress(OSError):
                    if datetime.fromtimestamp(path.stat().st_mtime, UTC) < cutoff:
                        shutil.rmtree(path)  # a session that never got its file
        found.sort(reverse=True)
        removed: list[str] = []
        for index, (modified, path) in enumerate(found):
            if index >= self._chat.keep_sessions or datetime.fromtimestamp(modified, UTC) < cutoff:
                with contextlib.suppress(OSError), self.held(path) as holding:
                    if holding:  # one that is answering or confirming right now stays, until the next prune
                        shutil.rmtree(path)
                        removed.append(path.name)
        return removed


class StoreGate(ActionGate):
    """The kernel's `ActionGate`: proposals go into the session's store, nothing runs (ADR 0045)."""

    def __init__(self, store: ChatStore, kernel: Kernel) -> None:
        """Record proposals in `store`; `kernel` names the settings a chat may not change."""
        self._store = store
        self._kernel = kernel

    def propose(self, call: ProposedCall) -> str:
        """Record `call` as pending in its session and return its id.

        Raises:
            HeinzelError: If a chat may never run it, there is no such session, or it has too many proposals.
        """
        chat = self._store.load(call.session)
        if chat.context is not None and chat.context.kind == "check-in":
            arguments = dict(call.arguments)
            arguments.setdefault("timezone", chat.context.timezone)
            if (
                call.module != "heinzel"
                or call.operation != "propose-check-in"
                or chat.context.timezone is None
                or arguments.get("session_id") != chat.context.ref
                or arguments.get("timezone") != chat.context.timezone
            ):
                msg = "This check-in conversation may only propose its bound five-minute schedule"
                raise HeinzelError(msg)
            if any(
                action.module == "heinzel"
                and action.operation == "propose-check-in"
                and action.status in {"pending", "running"}
                for action in chat.actions
            ):
                msg = "This check-in already has an action awaiting a decision or recovery"
                raise HeinzelError(msg)
            call = ProposedCall(
                session=call.session,
                module=call.module,
                operation=call.operation,
                tool=call.tool,
                arguments=arguments,
                command=describe(call.module, call.operation, arguments),
            )
        if reason := refusal(call.module, call.operation, call.arguments, guarded_settings(self._kernel)):
            raise HeinzelError(reason)
        return self._store.propose(call)


def describe(module: str, operation: str, arguments: dict[str, JsonValue]) -> str:
    """An action as a command line, built from what runs: `hz system config set --key org.role --value lead`."""
    words = ["hz", *module.split(".")]
    if words[-1] != operation:  # a module that is one operation of its own name reads as that command
        words.append(operation)
    for name, value in arguments.items():
        if value is not None:
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            words += [f"--{name.replace('_', '-')}", shlex.quote(text)]
    return " ".join(words)


def dumps(value: object) -> str:
    """`value` as compact JSON, for results shown in a chat."""
    return json.dumps(value, ensure_ascii=False, default=str)
