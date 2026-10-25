"""Read Claude's configuration: pure functions from files to found servers and skills, without network or processes.

Every file is read through `_read`, which resolves links first and refuses any file that ends up outside the directory
it is expected in (a dotfiles link inside your home directory is fine, a link to `/etc` is not), and opens it with
`O_NOFOLLOW`, only as a regular file, and with a size limit. Secrets are never kept: an entry's environment and headers
are reduced to their names, and the models have no field for a value (ADR 0062).
"""

import hashlib
import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import yaml

from heinzel.kernel import clean_text
from heinzel.modules.system.claude._models import (
    Auth,
    FoundServer,
    FoundSkill,
    ServerSource,
    SkillSource,
    Skipped,
    Transport,
)

MAX_JSON = 16 * 1024 * 1024
MAX_SKILL = 256 * 1024
MAX_FRONTMATTER = 64 * 1024
MAX_PROJECTS = 200
MAX_ENTRIES = 300
MAX_FILES = 2000
DESCRIPTION = 400
BUNDLED = ("scripts", "references", "resources", "assets", "templates")
OWN = {"hz", "heinzel"}


LINK = "a link leaves its directory"


class Refused(Exception):  # noqa: N818 - not an error to raise past the scan: it becomes a `Skipped`
    """A file the scan leaves alone; the message is the reason shown."""


def _read(path: Path, root: Path, limit: int) -> bytes:
    """The bytes of `path`, a regular file that resolves inside `root`, at most `limit` long.

    Raises:
        Refused: If it is missing, is no regular file, leaves `root` through a link, or is too large.
    """
    try:
        real = path.resolve(strict=True)
    except OSError as error:
        msg = "not found"
        raise Refused(msg) from error
    if not real.is_relative_to(root.resolve()):
        msg = LINK
        raise Refused(msg)
    try:
        if not stat.S_ISREG(real.stat().st_mode):  # before opening: a named pipe would block the open
            msg = "not a regular file"
            raise Refused(msg)
        descriptor = os.open(real, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError as error:
        msg = "unreadable"
        raise Refused(msg) from error
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            msg = "not a regular file"
            raise Refused(msg)
        if info.st_size > limit:
            msg = "too large"
            raise Refused(msg)
        return handle.read(limit)


def _json(path: Path, root: Path) -> dict[str, Any]:
    """The JSON object in `path` (see `_read`).

    Raises:
        Refused: If `_read` refuses it, or it is not a JSON object.
    """
    try:
        data = json.loads(_read(path, root, MAX_JSON))
    except (ValueError, RecursionError) as error:
        msg = "not valid JSON"
        raise Refused(msg) from error
    if not isinstance(data, dict):
        msg = "not a JSON object"
        raise Refused(msg)
    return cast("dict[str, Any]", data)


def _digest(*parts: object) -> str:
    """A short hash of JSON-able parts."""
    text = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _obj(value: object) -> dict[str, Any]:
    """`value` when it is a JSON object, else an empty one."""
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _arr(value: object) -> list[Any]:
    """`value` when it is a JSON array, else an empty one."""
    return cast("list[Any]", value) if isinstance(value, list) else []  # type: ignore[redundant-cast] # pyright reads list[Unknown]


def _strings(value: object) -> list[str]:
    """The entries of a list that are strings."""
    return [item for item in _arr(value) if isinstance(item, str)]


def _keys(value: object) -> list[str]:
    """The keys of a mapping, as names only."""
    return sorted(_name(key) for key in _obj(value))


def _name(text: str) -> str:
    """A name from a file, safe to show."""
    return clean_text(text).replace("\n", " ").strip()[:120]


@dataclass(slots=True)
class Scanner:
    """Scans one home directory; `scan` returns everything found and everything skipped."""

    home: Path
    servers: list[FoundServer] = field(default_factory=list[FoundServer])
    skills: list[FoundSkill] = field(default_factory=list[FoundSkill])
    skipped: list[Skipped] = field(default_factory=list[Skipped])
    connectors: int = 0
    _enabled: set[str] = field(default_factory=set[str])
    _sensitive: set[str] = field(default_factory=set[str])

    def tilde(self, path: Path) -> str:
        """`path` with the home directory written `~`."""
        try:
            return clean_text("~/" + path.relative_to(self.home).as_posix())
        except ValueError:
            return clean_text(str(path))

    def skip(self, path: Path, reason: str) -> None:
        """Record `path` as left alone."""
        self.skipped.append(Skipped(path=self.tilde(path), reason=reason))

    def scan(self) -> None:
        """Read every source: Claude Desktop, then Claude Code (user, projects, plugins)."""
        self.home = self.home.resolve()
        self._desktop()
        self._extensions()
        projects = self._code()
        for project in projects:
            self._project(project)
        self._user_skills()
        self.servers.sort(key=lambda s: (s.source, s.origin or "", s.name))
        self.skills.sort(key=lambda s: (s.source, s.origin or "", s.name))

    # --- MCP servers

    def _entries(self, doc: dict[str, Any], path: Path, source: ServerSource, origin: str | None) -> None:
        """Add the servers of an `mcpServers` mapping."""
        for name, entry in list(_obj(doc.get("mcpServers")).items())[:MAX_ENTRIES]:
            if isinstance(entry, dict):
                self._server(_name(name), cast("dict[str, Any]", entry), path, source, origin)

    def _server(self, name: str, entry: dict[str, Any], path: Path, source: ServerSource, origin: str | None) -> None:
        """Reduce one entry to a `FoundServer`: names, never values."""
        command = entry.get("command") if isinstance(entry.get("command"), str) else None
        url = entry.get("url") if isinstance(entry.get("url"), str) else None
        arguments = _strings(entry.get("args"))
        env, headers = _keys(entry.get("env")), _keys(entry.get("headers"))
        kind = str(entry.get("type", "")).lower()
        transport: Transport
        if kind == "sse":
            transport = "sse"
        elif kind in {"http", "streamable-http", "streamablehttp"} or (url and not command):
            transport = "http"
        elif kind == "stdio" or command:
            transport = "stdio"
        else:
            transport = "unknown"
        host = _host(url) if url else None
        in_url = bool(url) and _secret_in_url(url or "")
        values = list(_obj(entry.get("env")).values())
        auth: Auth = "none"
        if headers:
            auth = "header"
        elif in_url:
            auth = "url"
        elif any(_uses(value, self._sensitive) for value in values):
            auth = "keychain"
        elif env:
            auth = "env"
        elif transport in {"http", "sse"}:
            auth = "none-or-oauth"
        executable = Path(command).name if command else None
        own = executable in OWN or any(a in OWN for a in arguments)
        identity = ":".join(part for part in (source, origin, name) if part)
        self.servers.append(
            FoundServer(
                id=identity,
                name=name,
                source=source,
                origin=origin,
                transport=transport,
                command=_name(executable) if executable else None,
                arguments=len(arguments),
                host=host,
                auth=auth,
                env_keys=env,
                header_keys=headers,
                status="self" if own else "found",
                file=self.tilde(path),
                fingerprint=_digest(transport, executable, len(arguments), host, env, headers),
            )
        )

    def _desktop(self) -> None:
        """Claude Desktop's hand-configured servers."""
        for directory in self._desktop_dirs():
            path = directory / "claude_desktop_config.json"
            if not path.exists():
                continue
            try:
                self._entries(_json(path, self.home), path, "desktop", None)
            except Refused as error:
                self.skip(path, str(error))

    def _desktop_dirs(self) -> list[Path]:
        """Where Claude Desktop keeps its files: macOS, else Linux."""
        return [self.home / "Library" / "Application Support" / "Claude", self.home / ".config" / "Claude"]

    def _extensions(self) -> None:
        """Claude Desktop extensions: each `manifest.json` names one server, and which of its settings are secret."""
        for directory in self._desktop_dirs():
            base = directory / "Claude Extensions"
            for entry in _children(base):
                manifest = entry / "manifest.json"
                try:
                    doc = _json(manifest, self.home)
                except Refused as error:
                    if manifest.exists():
                        self.skip(manifest, str(error))
                    continue
                self._extension(doc, entry.name, manifest)

    def _extension(self, doc: dict[str, Any], directory: str, path: Path) -> None:
        """One extension's server; `${user_config.<key>}` of a sensitive field is a keychain secret."""
        server = doc.get("server")
        config = _obj(_obj(server).get("mcp_config"))
        if not config:
            return
        sensitive = {key for key, spec in _obj(doc.get("user_config")).items() if _obj(spec).get("sensitive") is True}
        self._sensitive = sensitive
        label = doc.get("name") if isinstance(doc.get("name"), str) else directory
        entry: dict[str, Any] = {
            "type": "stdio",
            **{k: v for k, v in config.items() if k in {"command", "args", "env"}},
        }
        self._server(_name(str(label)), entry, path, "desktop-extension", None)
        self._sensitive = set()

    def _code(self) -> list[Path]:
        """Claude Code: user and local-scope servers from `~/.claude.json`; the project directories it lists."""
        projects: list[Path] = []
        path = self.home / ".claude.json"
        try:
            doc = _json(path, self.home)
        except Refused as error:
            if path.exists():
                self.skip(path, str(error))
            doc = {}
        self._entries(doc, path, "code-user", None)
        self.connectors = len(_arr(doc.get("claudeAiMcpEverConnected")))
        for key, value in list(_obj(doc.get("projects")).items())[:MAX_PROJECTS]:
            if not key.startswith("/"):
                continue
            folder = Path(key)
            self._entries(_obj(value), path, "code-local", self.tilde(folder))
            if folder.is_dir():
                projects.append(folder)
        self._plugins()
        return projects

    def _plugins(self) -> None:
        """Enabled Claude Code plugins: their `.mcp.json` servers and `skills/*/SKILL.md`."""
        settings = self.home / ".claude" / "settings.json"
        try:
            enabled = _json(settings, self.home).get("enabledPlugins")
        except Refused as error:
            if settings.exists():
                self.skip(settings, str(error))
            return
        self._enabled = {k for k, v in _obj(enabled).items() if v is True}
        root = self.home / ".claude" / "plugins"
        try:
            installed = _json(root / "installed_plugins.json", self.home).get("plugins")
        except Refused:
            return
        for key, versions in list(_obj(installed).items())[:MAX_ENTRIES]:
            if key not in self._enabled:
                continue
            # One install is live: the user-scope one (the latest listed); project-scope installs belong to a project.
            installs = [_obj(v) for v in _arr(versions) if _obj(v).get("scope") == "user"]
            location = installs[-1].get("installPath") if installs else None
            if isinstance(location, str):
                self._plugin(key, Path(location), root)

    def _plugin(self, name: str, location: Path, root: Path) -> None:
        """One enabled plugin's servers and skills, only below `root` (the plugins directory)."""
        try:
            base = location.resolve(strict=True)
        except OSError:
            return
        if not base.is_relative_to(root.resolve()):
            self.skip(location, LINK)
            return
        path = base / ".mcp.json"
        if path.exists():
            try:
                doc = _json(path, base)
            except Refused as error:
                self.skip(path, str(error))
            else:
                wrapped = doc if "mcpServers" in doc else {"mcpServers": doc}
                self._entries(wrapped, path, "code-plugin", _name(name))
        self._skills(base / "skills", base, "code-plugin", _name(name))

    def _project(self, folder: Path) -> None:
        """A project listed in `~/.claude.json`: its shared `.mcp.json` and its `.claude/skills`."""
        base = folder.resolve()
        if base == self.home:  # Claude run in the home directory: those are the user-wide sources, read once
            return
        origin = self.tilde(folder)
        path = base / ".mcp.json"
        if path.exists():
            try:
                self._entries(_json(path, base), path, "code-project", origin)
            except Refused as error:
                self.skip(path, str(error))
        self._skills(base / ".claude" / "skills", base, "code-project", origin)

    # --- Skills

    def _user_skills(self) -> None:
        """Your own skills, `~/.claude/skills`."""
        self._skills(self.home / ".claude" / "skills", self.home, "code-user", None)

    def _skills(self, directory: Path, within: Path, source: SkillSource, origin: str | None) -> None:
        """Skills in `directory/<name>/SKILL.md`. The directory must lie inside `within`, and each skill inside it."""
        if not directory.is_dir():
            return
        root = directory.resolve()
        if not root.is_relative_to(within.resolve()):
            self.skip(directory, LINK)
            return
        for entry in _children(directory):
            try:
                real = entry.resolve(strict=True)
            except OSError:
                continue
            if not real.is_dir():
                continue
            if not real.is_relative_to(root):
                self.skip(entry, LINK)
                continue
            try:
                self._skill(entry, real, source, origin)
            except Refused as error:
                self.skip(entry, str(error))

    def _skill(self, entry: Path, real: Path, source: SkillSource, origin: str | None) -> None:
        """One skill: frontmatter name, description and keys; bundled files counted, never read or run."""
        if not (real / "SKILL.md").exists():  # a directory that is no skill is none of our business
            return
        raw = _read(real / "SKILL.md", real, MAX_SKILL)
        text = raw.decode("utf-8", errors="replace")
        meta = _frontmatter(text)
        name = _name(str(meta.get("name") or entry.name))
        description = clean_text(str(meta.get("description") or "")).strip()
        bundled = {kind: _files(real / kind, real) for kind in BUNDLED}
        files = {kind: len(found) for kind, found in bundled.items() if found}
        listing = [(p.relative_to(real).as_posix(), _size(p)) for found in bundled.values() for p in found]
        self.skills.append(
            FoundSkill(
                id=":".join(part for part in (source, origin, name) if part),
                name=name,
                description=description[:DESCRIPTION] + ("…" if len(description) > DESCRIPTION else ""),
                source=source,
                origin=origin,
                frontmatter_keys=sorted(_name(str(k)) for k in meta),
                files=files,
                path=self.tilde(entry),
                fingerprint=_digest(hashlib.sha256(raw).hexdigest(), listing),
            )
        )


def _secret_in_url(url: str) -> bool:
    """Whether a URL carries user info or a query, where tokens go."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return bool(parts.query or parts.username or parts.password)


def _host(url: str) -> str | None:
    """`scheme://host[:port]` of a URL: no user info, path or query; none when it has no host."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if not parts.hostname:
        return None
    return f"{parts.scheme}://{parts.hostname}" + (f":{port}" if port else "")


def _uses(value: object, sensitive: set[str]) -> bool:
    """Whether an environment value refers to a sensitive `${user_config.<key>}` of an extension."""
    return isinstance(value, str) and any(f"${{user_config.{key}}}" in value for key in sensitive)


def _children(directory: Path) -> list[Path]:
    """The subdirectories of `directory` by name, at most `MAX_ENTRIES`; links are returned and judged by the caller."""
    try:
        names = sorted(entry.name for entry in directory.iterdir())[:MAX_ENTRIES]
    except OSError:
        return []
    return [directory / name for name in names if not name.startswith(".")]


def _files(directory: Path, root: Path) -> list[Path]:
    """Regular files below `directory`, links not followed, at most `MAX_FILES`."""
    found: list[Path] = []
    if directory.is_symlink() or not directory.is_dir():
        return found
    for current, folders, names in os.walk(directory, followlinks=False):
        folders[:] = sorted(f for f in folders if not (Path(current) / f).is_symlink())
        for name in sorted(names):
            path = Path(current) / name
            if not path.is_symlink() and path.is_relative_to(root):
                found.append(path)
        if len(found) >= MAX_FILES:
            break
    return found[:MAX_FILES]


def _size(path: Path) -> int:
    """A file's size; zero when it vanished meanwhile."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _has_alias(document: str) -> bool:
    """Whether a YAML document uses an alias (`*name`)."""
    events = yaml.parse(document, Loader=yaml.SafeLoader)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return any(isinstance(event, yaml.AliasEvent) for event in events)  # pyright: ignore[reportUnknownVariableType]


def _frontmatter(text: str) -> dict[str, Any]:
    """The YAML frontmatter of a SKILL.md (between the first two `---` lines); empty when it has none or is invalid.

    Aliases are refused: expanding nested ones from a file somebody else wrote could use unbounded memory.
    """
    if not text.startswith("---"):
        return {}
    head, separator, _ = text[3:].partition("\n---")
    if not separator:
        return {}
    head = head[:MAX_FRONTMATTER]
    try:
        if _has_alias(head):
            return {}
        data = yaml.safe_load(head)
    except (yaml.YAMLError, ValueError, RecursionError):
        return {}
    return cast("dict[str, Any]", data) if isinstance(data, dict) else {}
