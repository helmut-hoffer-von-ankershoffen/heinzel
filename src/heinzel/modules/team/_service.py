"""Creating, joining and adding to a team repository, through `git` and `gh` (proposed onboarding ADR).

Every step is an `Action`, shown before it happens. Steps that change something outside this machine (creating a
repository, pushing, opening a pull request) and the step that makes a team yours run only when the caller says
`yes`; reading (a clone into hz's data directory) doesn't wait. Access control is the code host's: a clone that works
is the check that you may read the team, and the owners in CODEOWNERS merge roster changes.

Joining doesn't ask to be let in: the roster (`team/roster.yaml`) is the owners', and `join` finds you in it by the
logins you are signed in with. Only when it doesn't list you does `--propose` open a pull request adding you.

Repository content is untrusted. Files are read with the strict YAML rules (`_yaml`) and size limits, handles and
names are checked before they name a file or a branch, nothing is read or written through a symbolic link, and every
git command runs hardened (`_repo.git`), so a hostile repository can't point hz at a file outside its checkout.
"""

import os
import shlex
import shutil
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ValidationError

from heinzel.kernel import KERNEL_SECTION, Config, ConfigEditor, HeinzelError, Settings, clean_text
from heinzel.modules.team import _template
from heinzel.modules.team._models import (
    CODEOWNERS,
    MANIFEST,
    PEOPLE,
    ROSTER,
    Action,
    Adopted,
    Manifest,
    Member,
    MemberView,
    Roster,
    TeamPlan,
    TeamSettings,
    TeamShow,
    check_handle,
    check_login,
    check_name,
    check_role,
)
from heinzel.modules.team._repo import (
    GITHUB_REPOSITORY,
    CommandFailed,
    Run,
    Runner,
    check_url,
    directory_name,
    git,
)
from heinzel.modules.team._repo import github_repository as github_of
from heinzel.modules.team._roster import Identity, check_source, draft_roster, identities, roster_command
from heinzel.modules.team._yaml import MAX_BYTES, YamlError, load

MODULE = "team"
MAX_STDERR = 600
"""Characters of a failed command's error output shown; enough for git's reason, not a wall of text."""
LEAVE = "hz system config unset team.checkout && hz system config unset team.repository"


@dataclass(slots=True)
class _Step:
    """An action, what carries it out, and what to do if it fails."""

    action: Action
    do: Callable[[], str | None]
    keep: Callable[[TeamPlan, str], None] | None = None
    recovery: str = ""
    cleanup: list[list[str]] = field(default_factory=list[list[str]])
    """Commands that undo a half-done change when this step fails, run in order, failures ignored."""


class TeamService:
    """Creates, joins, shows and adds to the team repository configured in `[team]`."""

    def __init__(
        self,
        settings: TeamSettings,
        data_dir: Path,
        editor: ConfigEditor,
        run: Run | None = None,
        today: Callable[[], date] | None = None,
    ) -> None:
        """Create the service; `for_user` builds it for the kernel, tests pass a fake `run`.

        Args:
            settings: The configured team repository and checkout.
            data_dir: Where new checkouts go, under `team/`.
            editor: Writes `[team]` to your user configuration.
            run: Runs `git` and `gh`; real subprocesses by default.
            today: The date memberships start; today by default.
        """
        self._settings = settings
        self._data_dir = data_dir
        self._editor = editor
        self._run = run or Runner()
        self._today = today or date.today

    @classmethod
    def for_user(cls, settings: TeamSettings, editor: ConfigEditor) -> "TeamService":
        """The service with user-scope kernel settings: a project's `hz.toml` can't move the checkouts."""
        config = Config.load(Path.home())  # as the daemon does: user scope, never the project's
        return cls(settings, config.settings(Settings, KERNEL_SECTION).data_dir, editor)

    @property
    def checkout(self) -> Path | None:
        """Your team's checkout: the configured one, or where `join` puts the configured repository; None without."""
        if self._settings.checkout is not None:
            return self._settings.checkout
        if self._settings.repository is not None:
            return self._data_dir / MODULE / directory_name(self._settings.repository)
        return None

    def identities(self) -> list[Identity]:
        """Who you are signed in as, by every identity source (`gh` for now)."""
        return identities(self._run)

    # Creating --------------------------------------------------------------------------------------------------------

    def new(  # noqa: PLR0913 - the options of `hz team new`
        self,
        name: str,
        *,
        github: str | None = None,
        roster: str | None = None,
        handle: str | None = None,
        login: str | None = None,
        role: str = "engineering-manager",
        yes: bool = False,
    ) -> TeamPlan:
        """Create a team repository from the template, you its first owner, the roster drafted from `roster`.

        Raises:
            HeinzelError: If an argument is invalid, a team is configured already, the checkout exists, the roster
                source can't be read, or a step fails.
        """
        name = _valid(check_name, name, "hz team new payments")
        if github is not None and not GITHUB_REPOSITORY.fullmatch(github):
            msg = f"{github!r} isn't a GitHub repository; name it owner/name: hz team new {name} --github acme/{name}"
            raise HeinzelError(msg)
        if roster is not None:
            roster = _valid(check_source, roster, f"hz team new {name} --roster github:acme-example/{name}")
        role = _valid(check_role, role, f"hz team new {name} --role engineering-manager")
        self._refuse_second_team()
        me = self._me(login, f"hz team new {name} --login <your GitHub login>")
        handle = _valid(check_handle, handle or _suggest(me), f"hz team new {name} --handle {_suggest(me)}")
        today = self._today()
        creator = Member(handle=handle, github=me, roles=[role], owner=True, since=today)
        drafted = self._drafted(roster, creator, today) if roster else []
        checkout = self._data_dir / MODULE / name
        _refuse_existing(checkout)
        files = _template.repository(name, creator, drafted)
        restart = f"rm -rf {_q(checkout)}"
        steps = [
            self._git("create a Git repository", "init", "--quiet", "--initial-branch=main", checkout),
            self._local(f"write the template: {', '.join(sorted(files))}", lambda: _write_files(checkout, files)),
            self._git("stage the files", "-C", checkout, "add", "--all"),
            self._git(
                "commit them", "-C", checkout, "commit", "--quiet", "-m", f"Create the hz team repository {name}"
            ),
        ]
        if github is not None:
            create = ["gh", "repo", "create", github, "--private", "--source", str(checkout), "--remote", "origin"]
            steps.append(self._remote(f"create the private repository {github} on GitHub", create))
            push = self._remote("push main to it", git("-C", checkout, "push", "--quiet", "-u", "origin", "main"))
            push.recovery = f"the repository exists on GitHub; push again: git -C {_q(checkout)} push -u origin main"
            steps += [push, self._read_origin(checkout)]
        for step in steps[1:5]:
            step.recovery = f"start over: {restart}"
        plan = TeamPlan(
            operation="new",
            team=name,
            repository=f"https://github.com/{github}.git" if github else None,
            checkout=checkout,
            handle=handle,
            drafted=[_view(m, None) for m in drafted],
        )
        steps.append(self._remember(plan))
        again = _again(["hz", "team", "new", name], github=github, roster=roster, handle=handle, login=me, role=role)
        self._carry_out(plan, steps, yes=yes, again=again)
        if plan.done:
            plan.next = _after_new(name, checkout, plan.repository, drafted)
        return plan

    def _drafted(self, source: str, creator: Member, today: date) -> list[Member]:
        """The members `source` lists beside the creator, as engineers, for the owners to review.

        Raises:
            HeinzelError: If the source can't be read, or a login would get the creator's handle.
        """
        try:
            logins = draft_roster(self._run, source)
        except CommandFailed as exc:
            msg = (
                f"Couldn't read {source}: {_stderr(exc)}. Run it yourself to see why: {_shown(roster_command(source))}"
            )
            raise HeinzelError(msg) from exc
        except ValueError as exc:
            raise HeinzelError(str(exc)) from exc
        drafted: list[Member] = []
        for login in logins:
            if creator.github is not None and login.lower() == creator.github.lower():
                continue
            # Logins are unique without case and map to distinct handles; only the creator's chosen handle can clash.
            if (handle := _suggest(login)) == creator.handle:
                msg = f"{login} would get your handle {handle}; pick another: hz team new --handle {handle}-2"
                raise HeinzelError(msg)
            drafted.append(Member(handle=handle, github=login, roles=["engineer"], since=today))
        return drafted

    # Joining ---------------------------------------------------------------------------------------------------------

    def join(  # noqa: PLR0913 - the options of `hz team join`
        self,
        url: str,
        *,
        propose: bool = False,
        handle: str | None = None,
        login: str | None = None,
        role: str = "engineer",
        yes: bool = False,
    ) -> TeamPlan:
        """Clone the team repository with your own access, find you in its roster, and make it your team.

        When the roster doesn't list you, `propose` opens a pull request adding you (or giving your login to the entry
        `handle` names), which the owners merge.

        Raises:
            HeinzelError: If an argument is invalid, another team is configured, the clone fails, the repository isn't
                a team repository, nobody says who you are, the handle is taken, or a step fails.
        """
        url = _valid(check_url, url, "hz team join https://github.com/acme-example/hz-team-payments.git")
        quoted = shlex.quote(url)
        role = _valid(check_role, role, f"hz team join {quoted} --propose --role engineer")
        if login is not None:
            login = _valid(check_login, login, f"hz team join {quoted} --login <your GitHub login>")
        if handle is not None:
            handle = _valid(check_handle, handle, f"hz team join {quoted} --propose --handle <handle>")
        checkout, joined = self._checkout_for(url)
        plan = TeamPlan(operation="join", team=None, repository=url, checkout=checkout, handle=handle or "")
        manifest, roster = self._read_team(url, checkout, plan)
        plan.plugin = manifest.plugin.name if manifest.plugin else None
        found = [Identity("github", login)] if login is not None else self.identities()
        plan.identities = [f"{i.source}:{i.login}" for i in found]
        steps = [] if joined else [self._remember(plan)]
        again = ["hz", "team", "join", url, *(["--login", login] if login else [])]
        today = self._today()
        if (member := _match(roster.members, found, today)) is not None:
            plan.handle, plan.member = member.handle, _view(member, manifest)
            self._carry_out(plan, steps, yes=yes, again=again)
            if plan.done:
                plan.next = [f"You are {member.handle} ({', '.join(member.roles)}) in {manifest.name}: team:<name>"]
            return plan
        plan.candidates = [m.handle for m in roster.members if m.active(today) and m.github is None]
        if not propose:
            self._carry_out(plan, steps, yes=yes, again=again)
            who = ", ".join(plan.identities) or "you"
            ask = _shown([*again, "--propose"])
            plan.next = [*plan.next, f"The roster of {manifest.name} doesn't list {who}; ask to be added: {ask}"]
            if plan.candidates:
                plan.next.append(
                    f"If the roster lists you without a login: {ask} --handle <{'|'.join(plan.candidates)}>"
                )
            return plan
        me = next((i.login for i in found if i.source == "github"), None)
        if me is None:
            fix = f"hz team join {quoted} --propose --login <login>"
            msg = f"No login says who you are (is `gh` signed in?); say it: {fix}"
            raise HeinzelError(msg)
        member = self._proposed(manifest, roster, handle, me, role, quoted)
        plan.handle = member.handle
        steps += self._roster_steps(checkout, manifest, member, branch=f"hz/join-{member.handle}", person=True)
        again = _again([*again, "--propose"], handle=handle, role=role)
        self._carry_out(plan, steps, yes=yes, again=again)
        return plan

    def _proposed(  # noqa: PLR0913, PLR0917 - everything a proposal is made of
        self, manifest: Manifest, roster: Roster, handle: str | None, me: str, role: str, quoted: str
    ) -> Member:
        """The roster entry a proposal writes: yours, or the entry `handle` names with your login added.

        Raises:
            HeinzelError: If the entry `handle` names has a login, or your handle or login is taken.
        """
        if handle is not None and (named := next((m for m in roster.members if m.handle == handle), None)) is not None:
            if named.github is not None:
                msg = (
                    f"{manifest.name}'s {handle} has the login {named.github} already; ask its owners if that is wrong"
                )
                raise HeinzelError(msg)
            _refuse_duplicate(roster, manifest, None, me)
            return named.model_copy(update={"github": me})
        handle = handle or _suggest(me)
        if any(m.handle == handle for m in roster.members):
            fix = f"hz team join {quoted} --propose --handle {handle}-2"
            msg = f"{manifest.name} has a member {handle} already; pick another handle: {fix}"
            raise HeinzelError(msg)
        _refuse_duplicate(roster, manifest, None, me)
        return Member(handle=handle, github=me, roles=[role], since=self._today())

    def _read_team(self, url: str, checkout: Path, plan: TeamPlan) -> tuple[Manifest, Roster]:
        """Clone `url` into `checkout` (or update it) and read its manifest and roster; reading needs no yes.

        Raises:
            HeinzelError: If the clone or update fails, or the repository isn't a team repository.
        """
        fresh = not (checkout / ".git").is_dir()
        if fresh:
            clone = git("clone", "--quiet", "--depth", "1", "--no-recurse-submodules", "--", url, checkout)
            self._do(self._step(f"clone {url} with your own access", clone), plan)
        else:
            self._do(self._git("update your checkout", *_pull(checkout)), plan)
        try:
            return self._team_files(checkout)
        except HeinzelError:
            if fresh:  # a repository that isn't a team leaves nothing behind
                shutil.rmtree(checkout, ignore_errors=True)
            raise

    def _checkout_for(self, url: str) -> tuple[Path, bool]:
        """Where `url` is checked out, and whether it is your team already.

        Raises:
            HeinzelError: If another team is configured, or the directory holds something else.
        """
        configured = self.checkout
        if configured is not None:
            if self._settings.repository is not None and _same(self._settings.repository, url):
                return configured, True
            msg = f"You are in the team at {_q(configured)} already; one team for now. To leave it: {LEAVE}"
            raise HeinzelError(msg)
        checkout = self._data_dir / MODULE / directory_name(url)
        if (checkout / ".git").is_dir() and (origin := self._origin(checkout)) and _same(origin, url):
            return checkout, False  # cloned by an earlier `join` that wasn't confirmed
        _refuse_existing(checkout)
        return checkout, False

    # Adding ----------------------------------------------------------------------------------------------------------

    def add(  # noqa: PLR0913 - the options of `hz team add`
        self,
        handle: str,
        *,
        login: str | None = None,
        role: str = "engineer",
        owner: bool = False,
        by: str | None = None,
        yes: bool = False,
    ) -> TeamPlan:
        """Add someone to your team's roster, as one of its owners: a pull request, or a commit without a remote.

        Raises:
            HeinzelError: If no team is configured, an argument is invalid, you aren't an owner, they are on the
                roster already, or a step fails.
        """
        handle = _valid(check_handle, handle, "hz team add theo --login theo-example")
        if login is not None:
            login = _valid(check_login, login, f"hz team add {handle} --login <their GitHub login>")
        elif owner:
            msg = f"An owner approves on GitHub, so they need a login: hz team add {handle} --owner --login <login>"
            raise HeinzelError(msg)
        if by is not None:
            by = _valid(check_login, by, f"hz team add {handle} --by <your GitHub login>")
        role = _valid(check_role, role, f"hz team add {handle} --role engineer")
        checkout = self._configured()
        manifest, roster = self._team_files(checkout)
        today = self._today()
        mine = [Identity("github", by)] if by is not None else self.identities()
        if not mine:
            msg = f"No login says who you are (is `gh` signed in?); say it: hz team add {handle} --by <your login>"
            raise HeinzelError(msg)
        owners = roster.owners(manifest, today)
        if _match(owners, mine, today) is None:  # GitHub enforces CODEOWNERS; this says so before a push fails
            names = ", ".join(o.handle for o in owners) or "nobody"
            msg = f"Only owners add members to {manifest.name}, and {mine[0].login} isn't one; ask one of: {names}"
            raise HeinzelError(msg)
        _refuse_duplicate(roster, manifest, handle, login)
        member = Member(handle=handle, github=login, roles=[role], owner=owner, since=today)
        plan = TeamPlan(
            operation="add", team=manifest.name, repository=self._settings.repository, checkout=checkout, handle=handle
        )
        steps = self._roster_steps(checkout, manifest, member, branch=f"hz/add-{handle}", person=False)
        again = _again(["hz", "team", "add", handle], login=login, role=role, by=by) + (["--owner"] if owner else [])
        self._carry_out(plan, steps, yes=yes, again=again)
        return plan

    def _roster_steps(
        self, checkout: Path, manifest: Manifest, member: Member, *, branch: str, person: bool
    ) -> list[_Step]:
        """Write `member` into the roster: on a branch with a pull request when the checkout has a remote."""
        remote = self._origin(checkout)
        files = [ROSTER, CODEOWNERS, *([f"{PEOPLE}/{member.handle}.yaml"] if person else [])]

        def write() -> None:
            current = self._team_files(checkout)[1]
            claimed = next((m for m in current.members if m.handle == member.handle and m.github is None), None)
            entry = member
            if person and claimed is not None and member.github is not None:  # joining claims an entry without login
                _refuse_duplicate(current, manifest, None, member.github)
                entry = claimed.model_copy(update={"github": member.github})  # keeps its roles, place and dates
                members = [entry if m is claimed else m for m in current.members]
            else:
                _refuse_duplicate(current, manifest, member.handle, member.github)
                members = [*current.members, member]
            owners = _read(checkout, CODEOWNERS) if _exists(checkout, CODEOWNERS) else _template.codeowners([])
            if entry.github and (entry.owner or set(entry.roles) & set(manifest.owners)):
                owners = _template.with_owner(owners, entry.github)
            line = _template.person_line(entry.handle, entry.github) if person and entry.github else ""
            if line and line not in owners:
                owners += line
            updated = {ROSTER: _template.roster_file(Roster(members=members)), CODEOWNERS: owners}
            if person and not _exists(checkout, f"{PEOPLE}/{entry.handle}.yaml"):
                updated[f"{PEOPLE}/{entry.handle}.yaml"] = _template.person(entry.handle)
            _write_files(checkout, updated)

        message = f"Add {member.handle} to {manifest.name} as {', '.join(member.roles)}"
        write_step = self._local(f"add {member.handle} ({', '.join(member.roles)}) to {', '.join(files)}", write)
        stage = self._git("stage the change", "-C", checkout, "add", "--", *files)
        # `-- <files>` commits only the roster change, whatever else someone staged in the checkout.
        commit = self._git("commit it", "-C", checkout, "commit", "--quiet", "-m", message, "--", *files)
        if remote is None:
            return [write_step, stage, commit]
        default = self._default_branch(checkout)
        undo = [
            git("-C", checkout, "switch", "--quiet", "--discard-changes", default),
            git("-C", checkout, "branch", "--quiet", "-D", branch),
        ]
        fetch = ("-C", checkout, "fetch", "--quiet", "--no-recurse-submodules", "origin", default)
        steps = [
            self._clean(checkout),
            self._git("fetch the team's latest roster", *fetch),
            self._git(
                f"start {branch} from it", "-C", checkout, "switch", "--quiet", "-C", branch, f"origin/{default}"
            ),
            write_step,
            stage,
            commit,
            self._remote(f"push {branch}", git("-C", checkout, "push", "--quiet", "-u", "origin", branch)),
        ]
        for step in steps[3:]:
            step.cleanup = undo
        steps[-1].recovery = "if you can read the team but may not push to it, an owner adds you: " + _shown(
            _again(["hz", "team", "add", member.handle], login=member.github, role=member.roles[0])
        )
        if (github := github_of(remote)) is not None:
            body = (
                f"{message}. Opened by hz; the owners of {manifest.name} (CODEOWNERS of team/) merge it. The roster "
                "holds a handle, a GitHub login, roles and dates, nothing else."
            )
            pr = ["gh", "pr", "create", "--repo", github, "--head", branch, "--title", message, "--body", body]
            step = self._remote(f"open a pull request on {github}", pr)
            step.keep = lambda plan, output: setattr(plan, "pull_request", _last_line(output))
            step.cleanup = [undo[0]]  # the branch is pushed; keep it for a pull request opened by hand
            step.recovery = f"the branch is pushed; open its pull request: gh pr create --repo {github} --head {branch}"
            steps.append(step)
        else:
            note = self._local(f"leave {branch} for a merge request on {remote}", lambda: None)
            steps.append(note)
        steps.append(self._git("switch your checkout back", "-C", checkout, "switch", "--quiet", default))
        return steps

    # Showing and adopting --------------------------------------------------------------------------------------------

    def show(self) -> TeamShow:
        """Your team as configured, and what its repository says; problems are listed, never raised."""
        checkout = self.checkout
        exists = bool(checkout and checkout.is_dir())
        show = TeamShow(repository=self._settings.repository, checkout=checkout, exists=exists)
        if checkout is None:
            show.next = [
                "Join the team you were invited to: hz team join <url>",
                "Or create one: hz team new <name> --github <owner>/<name>",
            ]
            return show
        if not exists:
            show.problems.append(f"{checkout} doesn't exist")
            repository = self._settings.repository
            show.next = [f"Clone it again: hz team join {_q(repository)}" if repository else f"Leave it: {LEAVE}"]
            return show
        try:
            manifest, roster = self._team_files(checkout)
        except HeinzelError as exc:
            show.problems.append(str(exc))
            return show
        today = self._today()
        show.name = manifest.name
        show.plugin = manifest.plugin.name if manifest.plugin else None
        show.members = [_view(m, manifest) for m in roster.members if m.active(today)]
        return show

    def manifest(self) -> Manifest | None:
        """The configured team's manifest; None when no team is configured.

        Raises:
            HeinzelError: If it is missing, unreadable or invalid.
        """
        return None if self.checkout is None else self._team_files(self.checkout)[0]

    def adopt(self, path: Path) -> Adopted:
        """Record an existing checkout as your team repository, replacing the deprecated `workflow.team_repository`.

        Raises:
            HeinzelError: If another team is configured, `path` isn't a Git checkout, or its `origin` isn't a URL hz
                accepts.
        """
        checkout = path.expanduser().resolve()
        configured = self.checkout
        if configured is not None and configured.resolve() != checkout:
            self._refuse_second_team()
        if not (checkout / ".git").exists():
            msg = f"{path} isn't a Git checkout; clone your team repository instead: hz team join <url>"
            raise HeinzelError(msg)
        origin = self._origin(checkout)
        if origin is not None:
            origin = _valid(check_url, origin, f"git -C {_q(checkout)} remote set-url origin <url>")
        self._editor.write("team.checkout", str(checkout))
        written = ["set team.checkout"]
        if origin is not None:
            self._editor.write("team.repository", origin)
            written.append("set team.repository")
        adopted = Adopted(repository=origin, checkout=checkout, written=written)
        try:
            self._editor.unset("workflow.team_repository")
            adopted.written.append("removed workflow.team_repository")
        except HeinzelError:  # not in the user file; another file or a variable may still set it
            adopted.next.append(
                "If a site file or HZ_WORKFLOW__TEAM_REPOSITORY still sets workflow.team_repository, remove it there"
            )
        if not _exists(checkout, MANIFEST):
            adopted.next.append(
                f"{checkout} has no {MANIFEST} yet; add one naming the team (`name: <team>`) in a pull request"
            )
        self._settings = self._settings.model_copy(update={"checkout": checkout, "repository": origin})
        return adopted

    # Steps -----------------------------------------------------------------------------------------------------------

    def _refuse_second_team(self) -> None:
        if (configured := self.checkout) is not None:
            msg = f"You are in the team at {_q(configured)} already; one team for now. To leave it: {LEAVE}"
            raise HeinzelError(msg)

    def _configured(self) -> Path:
        if (checkout := self.checkout) is None:
            msg = "No team is configured; join one: hz team join <url>, or create one: hz team new <name>"
            raise HeinzelError(msg)
        return checkout

    def _me(self, login: str | None, fix: str) -> str:
        if login is not None:
            return _valid(check_login, login, fix)
        if not (found := self.identities()):
            msg = f"No login says who you are (is `gh` signed in? gh auth login); say it: {fix}"
            raise HeinzelError(msg)
        return found[0].login

    def _team_files(self, checkout: Path) -> tuple[Manifest, Roster]:
        """The manifest and roster of the checkout, checked.

        Raises:
            HeinzelError: If the manifest is missing, either is unreadable or a link, or either is invalid.
        """
        if not _exists(checkout, MANIFEST):
            msg = (
                f"{checkout} has no {MANIFEST}, so it isn't a team repository; add one naming the team: `name: <team>`"
            )
            raise HeinzelError(msg)
        manifest = _parsed(Manifest, checkout, MANIFEST)
        roster = _parsed(Roster, checkout, ROSTER) if _exists(checkout, ROSTER) else Roster()
        return manifest, roster

    def _origin(self, checkout: Path) -> str | None:
        """The checkout's `origin` URL as configured, not rewritten by `insteadOf`; None without one."""
        try:
            return self._run(git("-C", checkout, "config", "--get", "remote.origin.url"), None).strip() or None
        except CommandFailed:
            return None

    def _default_branch(self, checkout: Path) -> str:
        """The remote's default branch, as the clone recorded it; `main` when it didn't."""
        try:
            ref = self._run(git("-C", checkout, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"), None).strip()
        except CommandFailed:
            return "main"
        branch = ref.removeprefix("origin/")
        return branch if branch and not branch.startswith("-") else "main"

    def _clean(self, checkout: Path) -> _Step:
        """A step that refuses a checkout with uncommitted changes: switching branches would carry them along."""
        status = git("-C", checkout, "status", "--porcelain", "--untracked-files=no")

        def check() -> None:
            if self._run(status, None).strip():
                msg = (
                    f"{checkout} has uncommitted changes, which a roster branch would carry along; "
                    f"commit or stash them first: git -C {_q(checkout)} stash"
                )
                raise HeinzelError(msg)

        step = self._local("check that your checkout has no uncommitted changes", check)
        step.action.command = _shown(status)
        return step

    def _read_origin(self, checkout: Path) -> _Step:
        step = self._step("read its URL", git("-C", checkout, "config", "--get", "remote.origin.url"))
        step.keep = lambda plan, url: setattr(plan, "repository", url.strip())
        return step

    def _step(self, what: str, command: list[str], *, remote: bool = False) -> _Step:
        return _Step(Action(what=what, command=_shown(command), remote=remote), lambda: self._run(command, None))

    def _git(self, what: str, *parts: str | Path) -> _Step:
        return self._step(what, git(*parts))

    def _remote(self, what: str, command: list[str]) -> _Step:
        return self._step(what, command, remote=True)

    @staticmethod
    def _local(what: str, do: Callable[[], object]) -> _Step:
        def run() -> None:
            do()

        return _Step(Action(what=what, remote=False), run)

    def _remember(self, plan: TeamPlan) -> _Step:
        def write() -> None:
            self._editor.write("team.checkout", str(plan.checkout))
            if plan.repository is not None:
                self._editor.write("team.repository", plan.repository)
            # What was written counts for the rest of this process too: `OrgContext` reads through this service.
            self._settings = self._settings.model_copy(
                update={"checkout": plan.checkout, "repository": plan.repository}
            )

        step = self._local("make it your team in your user configuration ([team])", write)
        step.action.confirm = True
        return step

    def _do(self, step: _Step, plan: TeamPlan) -> None:
        """Carry out one step and mark it done, or fail saying what was done before it and how to recover.

        Raises:
            HeinzelError: If the step fails.
        """
        if not any(action is step.action for action in plan.actions):
            plan.actions.append(step.action)
        try:
            output = step.do()
        except (CommandFailed, HeinzelError, ValueError, OSError) as exc:  # anything a step raises gets cleaned up
            for command in step.cleanup:
                with suppress(CommandFailed):  # best effort: the message below says what was done
                    self._run(command, None)
            reason = _stderr(exc) if isinstance(exc, CommandFailed) else str(exc).rstrip(".")
            done = [a.what for a in plan.actions if a.done]
            before = f" Done before it: {'; '.join(done)}." if done else " Nothing was done before it."
            recovery = f" To recover: {step.recovery}." if step.recovery else ""
            msg = f"Couldn't {step.action.what}: {reason}.{before}{recovery}"
            raise HeinzelError(msg) from exc
        step.action.done = True
        if step.keep is not None and output is not None:
            step.keep(plan, output)

    def _carry_out(self, plan: TeamPlan, steps: list[_Step], *, yes: bool, again: list[str]) -> None:
        """Run `steps`, or only add them to the plan when one waits for a yes and `yes` wasn't given."""
        plan.actions += [step.action for step in steps]
        if any(step.action.remote or step.action.confirm for step in steps) and not yes:
            plan.next = [f"The rest waits for your yes; to go ahead: {_shown([*again, '--yes'])}"]
            return
        for step in steps:
            self._do(step, plan)
        plan.done = True


# Files of a checkout -------------------------------------------------------------------------------------------------


def _inside(checkout: Path, relative: str) -> Path:
    """`checkout / relative`, refusing a path whose existing parts include a symbolic link.

    `relative` is a constant or built from a checked handle, so it never climbs out with `..`.

    Raises:
        HeinzelError: If a part is a link.
    """
    current = checkout.resolve()
    for part in Path(relative).parts:
        current /= part
        if current.is_symlink():
            msg = f"{current} is a symbolic link; hz reads and writes the team repository's own files only"
            raise HeinzelError(msg)
    return current


def _exists(checkout: Path, relative: str) -> bool:
    return _inside(checkout, relative).exists()


def _read(checkout: Path, relative: str) -> str:
    """A file of the checkout, at most `MAX_BYTES`, never through a link.

    Raises:
        HeinzelError: If it is a link, too large, not a file or unreadable.
    """
    path = _inside(checkout, relative)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as file:
            data = file.read(MAX_BYTES + 1)
    except OSError as exc:
        msg = f"Can't read {path}: {exc.strerror or exc}"
        raise HeinzelError(msg) from exc
    if len(data) > MAX_BYTES:
        msg = f"{path} is larger than {MAX_BYTES // 1024} KiB; a team repository's data files are small"
        raise HeinzelError(msg)
    try:
        return data.decode()
    except UnicodeDecodeError as exc:
        msg = f"{path} isn't UTF-8 text"
        raise HeinzelError(msg) from exc


def _parsed[M: BaseModel](model: type[M], checkout: Path, relative: str) -> M:
    """A data file of the checkout as `model`.

    Raises:
        HeinzelError: If it can't be read, isn't the YAML hz reads, or doesn't fit the model.
    """
    try:
        return model.model_validate(load(_read(checkout, relative)))
    except YamlError as exc:
        msg = f"{relative} in {checkout}: {exc}"
        raise HeinzelError(msg) from exc
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'file'}: {e['msg']}" for e in exc.errors())
        msg = f"{relative} in {checkout} is invalid: {problems}"
        raise HeinzelError(msg) from exc


def _write_files(checkout: Path, files: dict[str, str]) -> None:
    """Write files into the checkout, creating directories, never following a link.

    Raises:
        HeinzelError: If a path runs through a link or can't be written.
    """
    for relative, text in files.items():
        path = _inside(checkout, relative)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _inside(checkout, relative)  # again: a directory created above can't be a link, one found can
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags, 0o644)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
                file.write(text)
        except OSError as exc:
            msg = f"Can't write {path}: {exc.strerror or exc}"
            raise HeinzelError(msg) from exc


def _refuse_existing(checkout: Path) -> None:
    if checkout.exists() or checkout.is_symlink():
        msg = f"{checkout} exists already; move it away, or make it your team: hz team adopt {_q(checkout)}"
        raise HeinzelError(msg)


def _refuse_duplicate(roster: Roster, manifest: Manifest, handle: str | None, login: str | None) -> None:
    if handle is not None and any(m.handle == handle for m in roster.members):
        msg = f"{manifest.name} has a member {handle} already; see the roster with: hz team show"
        raise HeinzelError(msg)
    if login is None:
        return
    if (same := next((m for m in roster.members if (m.github or "").lower() == login.lower()), None)) is not None:
        msg = f"{manifest.name}'s roster has the login {login} already, as {same.handle}; see it with: hz team show"
        raise HeinzelError(msg)


# Small helpers -------------------------------------------------------------------------------------------------------


def _valid(check: Callable[[str], str], value: str, fix: str) -> str:
    """`check(value)`, with a failure as a HeinzelError that says what would work.

    Raises:
        HeinzelError: If the check fails.
    """
    try:
        return check(value)
    except ValueError as exc:
        msg = f"{exc}. Try: {fix}"
        raise HeinzelError(msg) from exc


def _suggest(login: str) -> str:
    """The handle a login suggests: the GitHub login by default, as handle characters."""
    handle = "-".join(part for part in "".join(c if c.isalnum() else "-" for c in login.lower()).split("-") if part)
    return handle[:39].rstrip("-") or "me"


def _match(members: list[Member], found: list[Identity], today: date) -> Member | None:
    """The active member one of the identities belongs to; GitHub logins compare without case, as GitHub does."""
    logins = {i.login.lower() for i in found if i.source == "github"}
    return next((m for m in members if m.active(today) and m.github is not None and m.github.lower() in logins), None)


def _same(first: str, second: str) -> bool:
    """Whether two repository URLs name the same repository: GitHub ones by owner/name, others as written."""
    if (a := github_of(first)) is not None and (b := github_of(second)) is not None:
        return a.lower() == b.lower()
    return first.rstrip("/").removesuffix(".git") == second.rstrip("/").removesuffix(".git")


def _view(member: Member, manifest: Manifest | None) -> MemberView:
    owner = member.owner or (manifest is not None and bool(set(member.roles) & set(manifest.owners)))
    return MemberView(handle=member.handle, github=member.github, roles=member.roles, owner=owner, since=member.since)


def _after_new(name: str, checkout: Path, repository: str | None, drafted: list[Member]) -> list[str]:
    steps: list[str] = []
    if drafted:
        steps.append(f"Review the drafted roster in {ROSTER}: everyone starts as engineer; fix roles in a commit")
    if repository:
        steps += [
            f"Invite your team with one line in chat: hz team join {shlex.quote(repository)}",
            (
                "GitHub enforces the owners' approval only when main requires a code-owner review: in the repository's "
                "settings, add a rule for main that requires a pull request reviewed by code owners"
            ),
        ]
    else:
        here = _q(checkout)
        steps.append(
            f"Push it once it has a home: gh repo create <owner>/{name} --private --source {here} --push "
            f"&& hz team adopt {here}"
        )
    return steps


def _pull(checkout: Path) -> list[str | Path]:
    return ["-C", checkout, "pull", "--quiet", "--ff-only", "--no-recurse-submodules"]


def _again(base: list[str], **options: str | None) -> list[str]:
    """The command line that repeats an operation with the options given."""
    return base + [part for key, value in options.items() if value is not None for part in (f"--{key}", value)]


def _q(path: Path | str) -> str:
    return shlex.quote(str(path))


def _shown(command: list[str]) -> str:
    return shlex.join(command)


def _last_line(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return clean_text(lines[-1]) if lines else ""


def _stderr(exc: CommandFailed) -> str:
    text = clean_text(exc.stderr) or "it failed without saying why"
    return text if len(text) <= MAX_STDERR else text[:MAX_STDERR] + "..."
