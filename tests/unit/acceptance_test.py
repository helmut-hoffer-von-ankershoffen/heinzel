"""The acceptance harness's verdict logic, without network or installs (scripts/acceptance/)."""

import argparse
import ast
import contextlib
import importlib.util
import json
import re
import shutil
import subprocess
import urllib.error
import zipfile
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

HARNESS = Path(__file__).resolve().parents[2] / "scripts" / "acceptance"


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"acceptance_{name}", HARNESS / f"{name}.py")
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harness = load("run_acceptance")
summary = load("summary")
UNKNOWN_FOREGROUND_VERSION = f"{max(int(version.split('.')[0]) for version in harness.FOREGROUND_UI) + 1}.0.0"


def run(**overrides):
    args = {
        "diagnostic_explicit_profile": False,
        "bootstrap": "literal",
        "candidate_wheel": None,
        "candidate_wheel_url": None,
    }
    return SimpleNamespace(**(args | overrides))


def cases(**statuses: str) -> dict:
    """Every case passes unless a status is given for it, e.g. cases(**{"3": "FAIL"})."""
    result = {ident: {"status": "PASS"} for ident in harness.CASES}
    result.update({ident: {"status": status} for ident, status in statuses.items()})
    return result


def test_a_clean_literal_run_is_accepted_and_unprovable_cases_are_named():
    assert harness.verdict(run(), cases()) == "ACCEPTED"
    mixed = cases(**{"11": "UNPROVABLE", "12": "UNPROVABLE"})
    assert harness.verdict(run(), mixed) == "ACCEPTED_EXCEPT_UNPROVABLE"
    assert harness.verdict(run(), cases(**{"13": "SKIPPED"})) == "ACCEPTED_EXCEPT_UNPROVABLE"


def test_a_failure_or_refusal_rejects_and_a_failure_wins_over_a_refusal():
    assert harness.verdict(run(), cases(**{"3": "FAIL"})) == "REJECTED"
    assert harness.verdict(run(), cases(**{"8": "REFUSED"})) == "REFUSED"
    assert harness.verdict(run(), cases(**{"3": "FAIL", "8": "REFUSED"})) == "REJECTED"


def test_a_subset_of_cases_is_partial_not_accepted():
    assert harness.verdict(run(), {"2": {"status": "PASS"}}) == "PARTIAL_PASS"


@pytest.mark.parametrize(
    "options",
    [
        {"diagnostic_explicit_profile": True},
        {"bootstrap": "explicit"},
        {"bootstrap": "candidate-app"},
        {"candidate_wheel": Path("heinzel.whl")},
    ],
)
def test_a_candidate_or_diagnostic_run_never_reads_as_a_public_path_pass(options):
    assert harness.verdict(run(**options), cases()) == "NOT_ACCEPTANCE_DIAGNOSTIC"
    assert harness.verdict(run(**options), cases(**{"3": "FAIL"})) == "NOT_ACCEPTANCE_DIAGNOSTIC"


def test_a_release_candidate_run_has_its_own_labels():
    release = run(candidate_wheel_url="https://example.invalid/heinzel.whl")
    assert harness.verdict(release, cases()) == "CANDIDATE_ACCEPTED"
    skipped = cases(**{"1": "SKIPPED", "5": "SKIPPED", "13": "SKIPPED"})
    assert harness.verdict(release, skipped) == "CANDIDATE_ACCEPTED_EXCEPT_UNPROVABLE"
    assert harness.verdict(release, cases(**{"6": "FAIL"})) == "CANDIDATE_REJECTED"
    assert harness.verdict(release, cases(**{"6": "REFUSED"})) == "REFUSED"
    assert harness.verdict(release, {"2": {"status": "PASS"}}) == "CANDIDATE_PARTIAL"
    labels = {harness.verdict(release, c) for c in (cases(), skipped, cases(**{"6": "FAIL"}))}
    assert not labels & {"ACCEPTED", "ACCEPTED_EXCEPT_UNPROVABLE", "REJECTED", "PARTIAL_PASS"}


@pytest.mark.parametrize(
    ("label", "code"),
    [
        ("ACCEPTED", 0),
        ("ACCEPTED_EXCEPT_UNPROVABLE", 0),
        ("PARTIAL_PASS", 0),
        ("CANDIDATE_ACCEPTED", 0),
        ("CANDIDATE_ACCEPTED_EXCEPT_UNPROVABLE", 0),
        ("REJECTED", 1),
        ("REJECTED_ISOLATION", 1),
        ("CANDIDATE_REJECTED", 1),
        ("NOT_ACCEPTANCE_DIAGNOSTIC", 1),
        ("INCOMPLETE", 1),
        ("PREFLIGHT_FAILED", 2),
        ("REFUSED", 3),
    ],
)
def test_the_exit_code_fails_the_job_unless_the_verdict_passes(label, code):
    assert harness.exit_code(label) == code


@pytest.mark.parametrize(
    ("version", "final"),
    [
        ("0.9.9", True),
        ("10.20.30", True),
        ("0.9.10.dev1234", False),
        ("0.10.0.dev3", False),
        ("1.0.0rc1", False),
        ("1.0.0a2", False),
        ("1.0.0.post1", False),
        ("1.0.0+local", False),
        ("1.0", False),
        ("published", False),
        ("", False),
        (None, False),
    ],
)
def test_only_x_y_z_is_a_final_version(version, final):
    assert harness.is_final_version(version) is final


@pytest.mark.parametrize(
    ("version", "command"),
    [
        *[(f"0.9.{patch}", ["ui"]) for patch in range(8, 16)],
        ("0.9.16", ["ui", "serve"]),
        ("0.9.17", ["ui", "serve"]),
        ("0.9.18", ["ui", "serve"]),
        ("0.9.19", ["ui", "serve"]),
        ("0.9.20", ["ui", "serve"]),
    ],
)
def test_installed_metadata_selects_the_canonical_foreground_command(version, command, tmp_path):
    calls = []
    session = object()

    def installed_version(argv, label, deadline):
        calls.append((argv, label, deadline))
        return 0, version + "\n", ""

    def foreground(argv, label):
        calls.append((argv, label))
        return session

    case = SimpleNamespace(
        prefix=tmp_path / "tools/heinzel",
        bin=tmp_path / "bin",
        expect_version=version,
        run=installed_version,
        foreground=foreground,
    )
    assert harness.ui_app(case, "plugin-ui") is session
    assert calls == [
        (
            [
                str(case.prefix / "bin/python"),
                "-I",
                "-c",
                "from importlib.metadata import version; print(version('heinzel'))",
            ],
            "plugin-ui-installed-version",
            harness.SHORT_DEADLINE,
        ),
        ([str(case.bin / "hz"), *command], "plugin-ui"),
    ]


@pytest.mark.parametrize(
    ("installed", "expected"),
    [
        ("0.9.15", "0.9.16"),
        ("0.9.16", "0.9.15"),
        ("0.9.17", "0.9.16"),
        ("0.9.16", "0.9.17"),
        *[
            (installed, expected)
            for installed in ("0.9.17", "0.9.18", "0.9.19", "0.9.20")
            for expected in ("0.9.17", "0.9.18", "0.9.19", "0.9.20")
            if installed != expected
        ],
        (UNKNOWN_FOREGROUND_VERSION, UNKNOWN_FOREGROUND_VERSION),
        *[
            (f"0.9.{patch}{suffix}", f"0.9.{patch}{suffix}")
            for patch in (17, 18, 19, 20)
            for suffix in ("+local", ".dev1", "rc1")
        ],
        ("0.9.018", "0.9.018"),
        ("v0.9.19", "v0.9.19"),
        ("0.9.19 ", "0.9.19"),
        ("0.9.7", "0.9.7"),
        ("0.9.16.dev1", "0.9.16.dev1"),
        ("0.9.16rc1", "0.9.16rc1"),
        ("0.9.016", "0.9.016"),
        ("v0.9.16", "v0.9.16"),
        (" 0.9.16", "0.9.16"),
        ("0.9.16 ", "0.9.16"),
        ("0.9.16\n", "0.9.16"),
        ("0.9.16\n0.9.15", "0.9.16"),
        ("", "0.9.16"),
        ("help", "0.9.16"),
    ],
)
def test_unrecognized_or_wrong_installed_version_never_starts_foreground(installed, expected, tmp_path):
    case = SimpleNamespace(
        prefix=tmp_path / "tools/heinzel",
        bin=tmp_path / "bin",
        expect_version=expected,
        run=lambda *_args: (0, installed + "\n", ""),
        foreground=lambda *_args: pytest.fail("Unrecognized installed version started foreground"),
    )
    with pytest.raises(harness.StepFailed):
        harness.ui_app(case, "plugin-ui")


def test_missing_installed_metadata_never_starts_foreground(tmp_path):
    def missing_metadata(*_args):
        msg = "Installed metadata query failed"
        raise harness.StepFailed(msg)

    case = SimpleNamespace(
        prefix=tmp_path / "tools/heinzel",
        bin=tmp_path / "bin",
        expect_version="0.9.16",
        run=missing_metadata,
        foreground=lambda *_args: pytest.fail("Missing installed metadata started foreground"),
    )
    with pytest.raises(harness.StepFailed, match="metadata query failed"):
        harness.ui_app(case, "plugin-ui")


def test_noninteractive_cases_supply_fictional_profile_without_inheriting_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("HZ_ORG__NAME", "Inherited Person")
    monkeypatch.setenv("HZ_TEAM__NAME", "Inherited Team")
    monkeypatch.setenv("HZ_ORG__EMAIL", "inherited@example.invalid")
    monkeypatch.setenv("HZ_CONFIG_HOME", "/inherited/configuration")
    monkeypatch.setenv("GITHUB_TOKEN", "inherited-token")
    case = harness.Case("2", tmp_path, {"args": SimpleNamespace(expect_version="0.9.16"), "explicit_profile": None})
    assert {key: case.env[key] for key in harness.PROFILE_FIXTURE} == {
        "HZ_ORG__NAME": "Mira Example",
        "HZ_ORG__HANDLE": "mira-example",
        "HZ_ORG__ROLE": "engineer",
        "HZ_ORG__TIMEZONE": "Europe/Berlin",
        "HZ_TEAM__NAME": "Example Team",
    }
    assert case.notes["fictional_profile_fixture"] == harness.PROFILE_FIXTURE
    assert "HZ_ORG__EMAIL" not in case.env
    assert "GITHUB_TOKEN" not in case.env
    assert "HZ_APP_PROFILE" not in case.env
    assert case.env["HOME"] == str(case.root / "home")
    assert case.env["HZ_CONFIG_HOME"] == str(case.root / "config_home")
    assert case.env["XDG_CONFIG_HOME"] == str(case.root / "xdg-config")
    assert case.env["HZ_SYSTEM_CREDENTIAL__BACKEND"] == "environment"


def test_release_candidate_urls_accept_only_this_projects_final_or_edge_assets():
    wheel = harness.RELEASE_WHEEL_URL
    profile = harness.RELEASE_PROFILE_URL
    base = "https://github.com/helmut-hoffer-von-ankershoffen"
    assert wheel.fullmatch(f"{base}/heinzel/releases/download/v1.2.3/heinzel-1.2.3-py3-none-any.whl")
    assert profile.fullmatch(f"{base}/heinzel-plugins/releases/download/app-v1.2.3/standard.json")
    for bad in (
        f"{base}/heinzel/releases/download/v1.2.4.dev5/heinzel-1.2.4.dev5-py3-none-any.whl",
        f"{base}/heinzel/releases/download/edge-v1.2.4/heinzel-1.2.4-py3-none-any.whl",
        f"{base}/heinzel/releases/download/v1.2.3/heinzel-1.2.4-py3-none-any.whl",
        f"{base}/other/releases/download/v1.2.3/heinzel-1.2.3-py3-none-any.whl",
        f"http://github.com/{base[19:]}/heinzel/releases/download/v1.2.3/heinzel-1.2.3-py3-none-any.whl",
        "https://example.invalid/heinzel-1.2.3-py3-none-any.whl",
    ):
        assert not wheel.fullmatch(bad), bad
    assert wheel.fullmatch(f"{base}/heinzel/releases/download/edge-v1.2.4.dev5/heinzel-1.2.4.dev5-py3-none-any.whl")
    assert profile.fullmatch(f"{base}/heinzel-plugins/releases/download/app-v1.2.3.dev1/standard.json")
    assert not profile.fullmatch("https://example.invalid/standard.json")


def test_release_candidate_inputs_go_together_and_exclude_other_candidates(capsys):
    base = ["--expect-version", "1.2.3", "--expect-app-version", "1.2.3"]
    complete = ["--candidate-wheel-url", "u", "--candidate-wheel-sha256", "a" * 64, "--candidate-profile-url", "p"]
    args = harness.parse_args([*base, *complete])
    assert args.candidate_wheel_url == "u"
    assert harness.parse_args(base).candidate_wheel_url is None
    for incomplete in (complete[:2], complete[2:4], complete[:2] + complete[4:]):
        with pytest.raises(SystemExit):
            harness.parse_args([*base, *incomplete])
    for other in (["--bootstrap", "explicit"], ["--candidate-wheel", "x.whl"], ["--diagnostic-explicit-profile"]):
        with pytest.raises(SystemExit):
            harness.parse_args([*base, *complete, *other])
    with pytest.raises(SystemExit):
        harness.parse_args(["--expect-version", "published", "--expect-app-version", "1.2.3", *complete])
    capsys.readouterr()


def test_chrome_runs_without_its_sandbox_only_when_asked():
    base = ["--expect-version", "1.2.3", "--expect-app-version", "1.2.3"]
    assert harness.parse_args(base).chrome_no_sandbox is False
    assert harness.parse_args([*base, "--chrome-no-sandbox", "false"]).chrome_no_sandbox is False
    assert harness.parse_args([*base, "--chrome-no-sandbox", "true"]).chrome_no_sandbox is True


def test_nothing_refuses_merely_because_it_runs_in_ci(monkeypatch):
    """A hosted runner sets CI variables; none of them may make the harness refuse or change what it asserts."""
    source = (HARNESS / "run_acceptance.py").read_text(encoding="utf-8")
    for name in ("GITHUB_ACTIONS", '"CI"', "'CI'"):
        assert name not in source
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_TOKEN", "not-recorded")
    record = harness.ci_record()
    assert record["run_id"] == "42"
    assert "not-recorded" not in json.dumps(record)


def test_the_public_exclusion_has_no_private_hosts_or_paths():
    for path in HARNESS.rglob("*"):
        if path.is_file() and path.suffix in {".py", ".md"}:
            text = path.read_text(encoding="utf-8")
            assert "/Users/" not in text, path
            assert "heinzel-hld" not in text, path


def test_the_summary_shows_the_verdict_and_every_case():
    result = {
        "verdict": "CANDIDATE_REJECTED",
        "mode": "candidate_release",
        "public_path_proof": False,
        "expectation_supplied": True,
        "host": {"os": "Linux", "machine": "x86_64"},
        "cases": {
            "2": {"name": "fresh_uvx", "status": "PASS", "seconds": 3.0, "first_failure": None},
            "10": {"name": "update", "status": "FAIL", "seconds": 4.0, "first_failure": {"step": "s", "failure": "x"}},
        },
    }
    text = summary.summarize(result)
    assert "CANDIDATE_REJECTED" in text
    assert text.index("| 2 fresh_uvx") < text.index("| 10 update")
    assert "s: x" in text


def test_only_the_named_edge_core_counts_and_public_runs_name_none(monkeypatch):
    assert harness.EDGE_CANDIDATE is None
    assert not harness.is_accepted_core("1.2.4.dev5")
    monkeypatch.setattr(harness, "EDGE_CANDIDATE", "1.2.4.dev5")
    assert harness.is_accepted_core("1.2.4.dev5")
    assert not harness.is_accepted_core("1.2.4.dev6")
    assert harness.is_accepted_core("1.2.3")


@pytest.mark.parametrize(
    "url",
    ["http://github.com/x", "https://example.org/x", "https://github.com.example.org/x", "file:///etc/passwd"],
)
def test_the_harness_fetches_only_from_official_release_hosts(url):
    with pytest.raises(BaseException, match="official release hosts"):
        harness.fetch(url)


def test_a_result_holding_an_object_still_writes(tmp_path):
    path = tmp_path / "result.json"
    harness.write_result(path, {"cases": {"8": {"session": object()}}}, [])
    assert "object object" in path.read_text()


def test_the_injected_fixture_is_valid_python():
    compile(harness.FIXTURE, "fixture", "exec")


def test_a_public_preflight_never_accepts_an_edge_version(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "EDGE_CANDIDATE", None)
    args = argparse.Namespace(expect_version="1.2.4.dev5", expect_app_version="1.2.4.dev5")
    with pytest.raises(harness.StepFailed, match="must be final"):
        harness.preflight(args, tmp_path)
    assert harness.EDGE_CANDIDATE is None


FAKE_LIST = """
function el(tag, text, children = [], label = null) {
  const node = { tagName: tag, innerText: text, textContent: text, children, parentElement: null, label };
  node.firstElementChild = children[0] ?? null;
  for (const child of children) child.parentElement = node;
  node.closest = (tag) => node.tagName === tag.toUpperCase() ? node : node.parentElement?.closest(tag);
  node.querySelectorAll = (selector) => {
    const found = [];
    const walk = (n) => {
      for (const child of n.children) {
        if (selector === "span" ? child.tagName === "SPAN" : child.label?.startsWith("Remove ")) found.push(child);
        walk(child);
      }
    };
    walk(node);
    return found;
  };
  return node;
}
const row = (name, version, pending = "") =>
  el("LI", `${name} v${version} ${pending}`, [
    el("DIV", `${name} v${version}`, [el("SPAN", `v${version}`), el("BUTTON", "", [], `Remove ${name}`)]),
    el("DIV", pending, [el("SPAN", pending)])
  ]);
const clock = row("heinzel-clock", CLOCK_VERSION, PENDING_VERSION);
const tailscale = row("heinzel-tailscale", "0.9.2");
const rows = SINGLE_PLUGIN ? [clock] : [clock, tailscale];
const list = el("UL", rows.map(r => r.innerText).join(" "), rows);
el("DIV", list.innerText, [list]);
globalThis.document = {
  querySelector: (selector) => {
    const name = selector.match(/Remove ([^']+)'/)[1];
    return rows.find(r => r.children[0].children[1].label === `Remove ${name}`)?.children[0].children[1] ?? null;
  },
};
"""


def test_a_row_check_reads_only_the_installed_version_in_its_own_header():
    """Execute every real page expression in fresh JS contexts under one five-second process budget."""
    node = shutil.which("node")
    assert node, "node runs the page expression; the CI runner images have it"
    cases = [
        ("installed", "0.9.3", "", False, ("heinzel-clock", "0.9.3"), True),
        ("other-plugin", "0.9.3", "", False, ("heinzel-clock", "0.9.2"), False),
        ("version-prefix", "0.9.20", "", False, ("heinzel-clock", "0.9.2"), False),
        ("pending-only", "0.9.3", "v0.9.2", True, ("heinzel-clock", "0.9.2"), False),
        ("installed-with-pending", "0.9.3", "v0.9.2", True, ("heinzel-clock", "0.9.3"), True),
        ("missing-plugin", "0.9.3", "", False, ("heinzel-missing", "0.9.2"), False),
    ]
    expressions = []
    for case, installed, pending, single, lookup, _shown in cases:
        setup = (
            f"const CLOCK_VERSION={json.dumps(installed)}, PENDING_VERSION={json.dumps(pending)}, "
            f"SINGLE_PLUGIN={json.dumps(single)};"
        )
        expressions.append({"case": case, "source": setup + FAKE_LIST + harness.row_has(*lookup)})
    script = (
        'const vm = require("node:vm");'
        f"const cases = {json.dumps(expressions)};"
        "console.log(JSON.stringify(cases.map(({case: id, source}) => "
        "({case: id, shown: vm.runInNewContext(source, {}, {timeout: 5000})}))));"
    )
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True, timeout=5)
    for result, (case, *_inputs, shown) in zip(json.loads(out.stdout), cases, strict=True):
        assert result["case"] == case
        assert result["shown"] is shown, case


def test_plugin_navigation_cannot_restart_an_expired_change_budget(monkeypatch):
    monkeypatch.setattr(harness.time, "monotonic", lambda: 100)
    assert harness.ui_remaining(105, 60) == 5
    assert harness.ui_remaining(200, 30) == 30
    with pytest.raises(harness.StepFailed, match="deadline passed"):
        harness.ui_remaining(100, 60)


@pytest.mark.parametrize("field", ["accept_wheel_sha256", "accept_profile_sha256"])
def test_a_public_release_snapshot_rejects_replaced_bytes(field, tmp_path, monkeypatch):
    """A later release must not satisfy acceptance of the coordinator's pinned bytes."""
    version = "1.2.3"
    wheel = b"published wheel"
    wheel_hash = harness.sha256(wheel)
    profile = {"format": 2, "core_version": version, "artifacts": [{"name": "heinzel", "sha256": wheel_hash}]}
    profile_bytes = json.dumps(profile).encode()
    metadata = {
        "info": {"version": version},
        "urls": [{"packagetype": "bdist_wheel", "digests": {"sha256": wheel_hash}}],
    }
    monkeypatch.setattr(harness, "latest_app_version", lambda: version)
    responses = {
        "https://pypi.org/pypi/heinzel/json": json.dumps(metadata).encode(),
        f"{harness.COLLECTION}/releases/download/app-v{version}/standard.json": profile_bytes,
    }
    monkeypatch.setattr(harness, "fetch", lambda url, *a: responses[url])
    args = argparse.Namespace(
        expect_version=version,
        expect_app_version=version,
        accept_wheel_sha256=wheel_hash,
        accept_profile_sha256=harness.sha256(profile_bytes),
    )
    setattr(args, field, "0" * 64)
    with pytest.raises(harness.StepFailed, match="differs from the accepted hash"):
        harness.preflight(args, tmp_path)


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "baseline-version",
        "baseline-hash",
        "target-hash",
        "same-version-hash",
        "extra-wheel",
        "old-target",
        "missing-clock",
    ],
)
def test_plugin_update_expectation_is_bound_independently_before_mutating(defect, monkeypatch, tmp_path):
    artifact = {"name": "heinzel-clock", "version": "0.9.5", "sha256": "c" * 64}
    payload = b"registry-selected wheel"
    digest = harness.sha256(payload)
    wheel_url = "https://files.pythonhosted.org/clock.whl"
    baseline = {
        "info": {"version": "0.9.5"},
        "urls": [{"packagetype": "bdist_wheel", "digests": {"sha256": "c" * 64}}],
    }
    index = {
        "info": {"version": "0.9.6"},
        "urls": [{"packagetype": "bdist_wheel", "digests": {"sha256": digest}, "url": wheel_url}],
    }
    case = SimpleNamespace(ctx={"profile": {"artifacts": [artifact]}}, notes={}, root=tmp_path)
    if defect == "baseline-version":
        baseline["info"]["version"] = "0.9.6"
    elif defect == "baseline-hash":
        baseline["urls"][0]["digests"]["sha256"] = "d" * 64
    elif defect == "target-hash":
        index["urls"][0]["digests"]["sha256"] = "d" * 64
    elif defect == "extra-wheel":
        index["urls"] *= 2
    elif defect in ("old-target", "same-version-hash"):
        index["info"]["version"] = {"old-target": "0.9.2", "same-version-hash": "0.9.5"}[defect]
    elif defect == "missing-clock":
        case.ctx["profile"]["artifacts"] = []

    def metadata(url, _limit=None):
        if url == wheel_url:
            return payload
        return json.dumps(
            {
                "https://pypi.org/pypi/heinzel-clock/0.9.5/json": baseline,
                "https://pypi.org/pypi/heinzel-clock/json": index,
            }[url]
        ).encode()

    monkeypatch.setattr(harness, "fetch", metadata)
    if defect:
        with pytest.raises(harness.StepFailed):
            harness.plugin_update_version(case)
        assert case.notes == {}
    else:
        assert harness.plugin_update_version(case) == "0.9.6"
        assert case.notes == {
            "clock_update_baseline": {"version": "0.9.5", "sha256": "c" * 64},
            "clock_update_target": {"version": "0.9.6", "sha256": digest},
        }
        assert (tmp_path / "clock-update-target.whl").read_bytes() == payload


@pytest.mark.parametrize(
    ("version", "defect"),
    [
        ("0.5.2", None),
        ("0.7.0", None),  # a newer example release needs no harness change
        ("0.7.0rc1", "prerelease"),
        (None, "no-version"),
        ("0.7.0", "no-wheel"),
        ("0.7.0", "extra-wheel"),
        ("0.7.0", "yanked-wheel"),
        ("0.7.0", "foreign-host"),
    ],
)
def test_example_install_target_is_bound_from_the_index_before_any_change(version, defect, monkeypatch):
    wheel = {
        "packagetype": "bdist_wheel",
        "digests": {"sha256": "e" * 64},
        "url": "https://files.pythonhosted.org/heinzel_example.whl",
    }
    index = {"info": {"version": version}, "urls": [wheel, {"packagetype": "sdist", "digests": {"sha256": "f" * 64}}]}
    if defect == "no-wheel":
        index["urls"] = index["urls"][1:]
    elif defect == "extra-wheel":
        index["urls"].append(dict(wheel))
    elif defect == "yanked-wheel":
        wheel["yanked"] = True
    elif defect == "foreign-host":
        wheel["url"] = "https://example.invalid/heinzel_example.whl"
    fetched = []

    def metadata(url, _limit=None):
        fetched.append(url)
        return json.dumps(index).encode()

    monkeypatch.setattr(harness, "fetch", metadata)
    case = SimpleNamespace(notes={})
    if defect:
        with pytest.raises(harness.StepFailed):
            harness.example_plugin_version(case)
        assert case.notes == {}
    else:
        assert harness.example_plugin_version(case) == version
        assert case.notes == {"example_install_target": {"version": version, "sha256": "e" * 64}}
    assert fetched == ["https://pypi.org/pypi/heinzel-example/json"]


@pytest.mark.parametrize(("listed", "passes"), [("0.7.0", True), ("0.5.2", False), (None, False)])
def test_installed_example_must_be_at_the_bound_version(listed, passes, monkeypatch):
    inventory = {"heinzel-resource": {"distribution": "heinzel-resource", "version": "1.0.0"}}
    if listed:
        inventory["heinzel-example"] = {"distribution": "heinzel-example", "version": listed}
    monkeypatch.setattr(harness, "plugin_map", lambda _case, label: inventory if label == "example-add-list" else {})
    case = SimpleNamespace(plugins_expected={}, notes={})
    if passes:
        harness.check_installed_version(case, "heinzel-example", "0.7.0", "example-add-list")
    else:
        with pytest.raises(harness.StepFailed, match=re.escape("not at the bound 0.7.0")):
            harness.check_installed_version(case, "heinzel-example", "0.7.0", "example-add-list")
    assert case.plugins_expected == {"heinzel-example": {"enabled": True, "version": "0.7.0"}}
    # The version found is recorded whether or not it matches, so a failure names it.
    assert case.notes == {
        "installed_versions": {"example-add-list": {"plugin": "heinzel-example", "bound": "0.7.0", "found": listed}}
    }


VERSION = re.compile(r"(?<![0-9.])[0-9]+\.[0-9]+\.[0-9]+(?![0-9.])")


@pytest.mark.parametrize(
    ("target", "versions", "python", "error"),
    [
        (("published", "0.9.49"), ("0.9.8", "0.9.8", "0.9.43"), "python3.12", None),
        (("published", "0.9.49"), ("0.9.43", "0.9.43", "0.9.49"), "python3.12", None),
        (("candidate", "0.9.49"), ("0.9.8", "0.9.8", "0.9.43"), "python3.12", "installed"),
        (("published", "0.9.49"), ("0.9.9", "0.9.9", "0.9.43"), "python3.12", "installed"),
        (("published", "0.9.49"), ("0.9.8", "0.9.8", "0.9.42"), "python3.12", "installed"),
        (("published", "0.9.49"), ("0.9.8", "0.9.8", "0.9.43"), "python3.15", "installed"),
        (("published", "0.9.42"), ("0.9.8", "0.9.8", "0.9.43"), "python3.12", "installed"),
        (("published", "0.9.49"), ("0.9.8", "0.9.9", "0.9.43"), "python3.12", "started"),
        (("published", "0.9.49"), ("0.9.43", "0.9.43", "0.9.43"), "python3.12", "installed"),
        (("published", "0.9.49"), (None, "0.9.8", "0.9.43"), "python3.12", "installed"),
    ],
)
def test_update_allows_only_the_published_legacy_bridge(target, versions, python, error):
    mode, target_version = target
    previous, before, after = versions

    def cli(args, _label, _timeout):
        assert args == ["bin/hz", "system", "update", "--json"] + (
            ["--source", "candidate.whl"] if mode == "candidate" else []
        )
        return 0, json.dumps({"before": before, "after": after, "updated": True}), ""

    case = SimpleNamespace(
        ctx={"update_target": {"mode": mode, "version": target_version, "wheel": "candidate.whl"}},
        notes={"python_before": f"/managed/bin/{python}"},
        bin=Path("bin"),
        run=cli,
    )
    if error:
        with pytest.raises(harness.StepFailed, match=error):
            harness.run_update(case, "update", previous=previous)
    else:
        assert harness.run_update(case, "update", previous=previous)["after"] == after


@pytest.mark.parametrize(
    ("responses", "delays", "refusal"),
    [
        ([(1, 1010)], [], False),
        ([(0, 1010), (1, 4610)], [11], False),
        ([(0, 1120)], [], True),
        ([(0, 998)], [], True),
        ([(0, 1010), (0, 4610)], [11], True),
    ],
)
def test_bridge_quota_wait_is_bounded(monkeypatch, responses, delays, refusal):
    elapsed = 0
    waits = []
    results = iter(responses)

    def sleep(delay):
        nonlocal elapsed
        waits.append(delay)
        elapsed += delay

    def probe(timeout):
        assert 0 < timeout <= harness.BRIDGE_PROBE_TIMEOUT
        return next(results)

    clock = SimpleNamespace(monotonic=lambda: elapsed, time=lambda: 1000 + elapsed, sleep=sleep)
    monkeypatch.setattr(harness, "time", clock)
    monkeypatch.setattr(harness, "_bridge_quota", probe)
    evidence = {}
    if refusal:
        with pytest.raises(harness.Refused, match="insufficient anonymous GitHub quota"):
            harness.verify_legacy_bridge_readiness(evidence=evidence)
        assert evidence["remaining_after_probe"] == 0
        assert evidence["reset_epoch"] == responses[-1][1]
        assert evidence["sleep_seconds"] == sum(delays)
    else:
        result = harness.verify_legacy_bridge_readiness()
        assert result["required_requests"] == result["remaining_after_probe"] == 1
        assert result["sleep_seconds"] == result["elapsed_seconds"] == sum(delays)
        assert result["reset_epoch"] == responses[-1][1]
    assert waits == delays
    assert next(results, None) is None


@pytest.mark.parametrize(
    ("status", "remaining", "content_type", "refusal"),
    [
        (200, "1", "application/json; charset=utf-8", None),
        (200, "0", "application/json", None),
        (200, "1", "application/vnd.github+json", None),
        (200, "9", "text/html", "unexpected GitHub release response"),
        (200, "unknown", "application/json", "quota could not be verified"),
        (403, "0", "application/json", None),
        (429, "0", "application/json", None),
        (403, "9", "application/json", "rejected the anonymous probe"),
        (302, "9", "application/json", "rejected the anonymous probe"),
        (None, "9", "application/json", "endpoint unavailable"),
    ],
)
def test_bridge_quota_probes_only_the_real_anonymous_release_endpoint(
    monkeypatch, status, remaining, content_type, refusal
):
    headers = Message()
    headers["Content-Type"] = content_type
    headers["X-RateLimit-Remaining"] = remaining
    headers["X-RateLimit-Reset"] = "1010"
    headers["X-RateLimit-Resource"] = "core"

    def open_probe(request, *, timeout):
        assert request.full_url == (
            "https://api.github.com/repos/helmut-hoffer-von-ankershoffen/heinzel/releases?per_page=30&page=1"
        )
        assert request.get_method() == "GET"
        assert dict(request.header_items()) == {
            "Accept": "application/vnd.github+json",
            "Cache-control": "no-cache",
            "User-agent": "heinzel/0.9.43",
        }
        assert timeout == 5
        if status is None:
            message = "private network diagnostic"
            raise TimeoutError(message)
        if status != 200:
            raise urllib.error.HTTPError(request.full_url, status, "private response", headers, None)
        return contextlib.nullcontext(SimpleNamespace(status=status, headers=headers))

    def build_opener(proxy, redirect):
        assert proxy.proxies == {}
        assert redirect.redirect_request(None) is None
        return SimpleNamespace(open=open_probe)

    monkeypatch.setattr(harness.urllib.request, "build_opener", build_opener)
    if refusal:
        with pytest.raises(harness.Refused, match=refusal) as failure:
            harness._bridge_quota(5)
        assert "private" not in str(failure.value)
    else:
        assert harness._bridge_quota(5) == (int(remaining), 1010)


@pytest.mark.parametrize("quota_ready", [False, True])
def test_bridge_readiness_precedes_literal_update_and_does_not_mask_its_failure(monkeypatch, quota_ready):
    calls = []

    def step(_name, action):
        return action()

    def verify_bridge(_case, profile):
        assert profile == "published profile"
        calls.append("verified bridge")

    def quota(_case):
        calls.append("quota")
        if not quota_ready:
            message = "infrastructure deadline"
            raise harness.Refused(message)

    def cli(args, label, _timeout):
        assert args == ["bin/hz", "system", "update", "--json"]
        calls.append(label)
        if label == "hz-system-update":
            return 0, '{"updated": true, "before": "0.9.8", "after": "0.9.43"}', ""
        return 0, '{"updated": false, "before": "0.9.43", "after": "0.9.43"}', ""

    case = SimpleNamespace(
        ctx={
            "from_app": {"profile": "published profile", "accepted": {}, "version": "0.9.8", "url": "published URL"},
            "update_target": {"mode": "published", "version": "0.9.50"},
        },
        env={},
        bin=Path("bin"),
        root=Path("private-case"),
        notes={},
        plugins_expected={},
        provide_uv=lambda: None,
        step=step,
        run=cli,
    )
    monkeypatch.setattr(harness, "app_cycle", lambda *_args: None)
    user_commands = []
    monkeypatch.setattr(harness, "hz", lambda _case, *args, **_kwargs: user_commands.append(args))
    monkeypatch.setattr(
        harness,
        "check_installed_version",
        lambda _case, name, version, label: calls.append((name, version, label)),
    )
    monkeypatch.setattr(harness, "plugin_map", lambda *_args: {})
    monkeypatch.setattr(harness, "expect_plugins", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(harness, "installed_interpreter", lambda _case: "/managed/bin/python3.12")
    monkeypatch.setattr(harness, "snapshot_files", lambda _path: {"config": b"preserved"})
    monkeypatch.setattr(harness, "verify_state", lambda *_args: None)
    monkeypatch.setattr(harness, "verify_legacy_bridge", verify_bridge)
    monkeypatch.setattr(harness, "wait_for_legacy_bridge", quota)
    monkeypatch.setattr(harness, "bridge_post_interpreter", lambda _case: "python3.12")
    monkeypatch.setattr(harness, "bridge_python_need", lambda _case: None)

    if quota_ready:
        with pytest.raises(harness.StepFailed, match="updated=False"):
            harness.case_update(case)
        assert calls == [
            ("heinzel-example", "0.5.2", "user-plugin-version"),
            "hz-system-update",
            "verified bridge",
            "quota",
            "hz-system-update-from-bridge",
        ]
    else:
        with pytest.raises(harness.Refused, match="infrastructure deadline"):
            harness.case_update(case)
        assert calls == [
            ("heinzel-example", "0.5.2", "user-plugin-version"),
            "hz-system-update",
            "verified bridge",
            "quota",
        ]
    assert user_commands == [
        ("system", "plugin", "add", "heinzel-example==0.5.2", "--confirm"),
        ("system", "plugin", "disable", "heinzel-resource"),
        ("system", "config", "set", "clock.zones", '["Europe/Berlin","Asia/Tokyo"]'),
    ]


@pytest.mark.parametrize("installed", ["0.5.2", "0.5.3"])
def test_update_fixture_checks_the_installed_pin_before_updating(monkeypatch, installed):
    case = SimpleNamespace(
        ctx={
            "from_app": {"profile": {}, "accepted": {}, "version": "0.9.8", "url": "published URL"},
            "update_target": {"version": "target"},
        },
        env={},
        root=Path("private-case"),
        notes={},
        plugins_expected={},
        provide_uv=lambda: None,
    )

    def stop_after_prepare(_name, action):
        action()
        raise StopIteration

    case.step = stop_after_prepare
    monkeypatch.setattr(harness, "app_cycle", lambda *_args: None)
    monkeypatch.setattr(harness, "hz", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(harness, "plugin_map", lambda *_args: {"heinzel-example": {"version": installed}})
    monkeypatch.setattr(harness, "installed_interpreter", lambda _case: "/managed/bin/python3.12")
    monkeypatch.setattr(harness, "snapshot_files", lambda _path: {"config": b"preserved"})
    if installed == "0.5.2":
        with pytest.raises(StopIteration):
            harness.case_update(case)
        assert case.plugins_expected["heinzel-example"] == {"enabled": True, "version": "0.5.2"}
        assert case.plugins_expected["heinzel-resource"]["enabled"] is False
    else:
        with pytest.raises(harness.StepFailed, match=r"not at the bound 0\.5\.2"):
            harness.case_update(case)
    assert case.notes["installed_versions"]["user-plugin-version"] == {
        "plugin": "heinzel-example",
        "bound": "0.5.2",
        "found": installed,
    }


def test_legacy_bridge_rejects_wrong_wheel_before_creating_proof(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "fetch", lambda _url: b"wrong wheel")
    with pytest.raises(harness.StepFailed, match="bridge wheel fails its digest"):
        harness.verify_legacy_bridge(SimpleNamespace(root=tmp_path), {})
    assert not list(tmp_path.iterdir())


def test_bridge_receipt_diagnostics_do_not_expose_private_sources():
    facts = harness.bridge_receipt_facts(b"""[tool]
requirements = [
  { name = "heinzel", specifier = ">=0.9.43" },
  { name = "heinzel-clock", url = "https://example.invalid/private?token=secret" },
  { name = "private-package", path = "/secret" },
]
constraints = [{ path = "/secret/constraints.txt" }]
""")
    assert facts == {
        "requirements": [
            {"name": "heinzel", "specifier": ">=0.9.43", "source": "index"},
            {"name": "heinzel-clock", "specifier": None, "source": "other"},
            {"source": "other"},
        ],
        "constraints": [{"source": "other"}],
        "overrides": [],
    }
    assert "secret" not in json.dumps(facts)
    with pytest.raises(ValueError, match="receipt exceeds limit"):
        harness.bridge_receipt_facts(b"x" * 65537)


def test_failed_legacy_bridge_update_keeps_bounded_outcome():
    def cli(_args, _label, _timeout):
        return 0, json.dumps({"before": "0.9.43", "after": "0.9.43", "latest": "secret-token", "updated": False}), ""

    case = SimpleNamespace(
        ctx={"update_target": {"mode": "published", "version": "0.9.49"}}, notes={}, bin=Path("bin"), run=cli
    )
    with pytest.raises(harness.StepFailed, match="updated=False"):
        harness.run_update(case, "update", previous="0.9.43")
    assert case.notes["legacy_bridge_update"] == {
        "before": "0.9.43",
        "after": "0.9.43",
        "latest": None,
        "updated": False,
        "post_interpreter": None,
        "python_need": None,
        "stderr_python_unknown_event_seen": False,
        "stderr_python_managed_event_seen": False,
        "phases": {},
    }


def test_bridge_diagnostics_never_follow_receipt_symlinks(tmp_path):
    sensitive = tmp_path / "private"
    sensitive.write_text('private = "secret"')
    prefix = tmp_path / "tool"
    prefix.mkdir()
    (prefix / "uv-receipt.toml").symlink_to(sensitive)
    case = SimpleNamespace(prefix=prefix, notes={})
    harness.record_legacy_bridge_diagnostics(case)
    assert case.notes["legacy_bridge_diagnostics"]["unavailable"] == "OSError"
    assert "secret" not in json.dumps(case.notes)


def test_bridge_diagnostics_do_not_suppress_guard_refusal(tmp_path, monkeypatch):
    (tmp_path / "uv-receipt.toml").write_text("[tool]\nrequirements = []\n")

    def refused(*_args):
        message = "guard"
        raise harness.Refused(message)

    monkeypatch.setattr(harness, "plugin_map", refused)
    with pytest.raises(harness.Refused, match="guard"):
        harness.record_legacy_bridge_diagnostics(SimpleNamespace(prefix=tmp_path, notes={}))


def test_bridge_post_update_facts_are_bounded_and_redacted(tmp_path):
    managed = tmp_path / "uv-python"
    managed.mkdir()
    python = managed / "python3.12"
    python.touch()
    prefix = tmp_path / "tool"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin/python").symlink_to(python)
    cache = tmp_path / "cache_dir"
    cache.mkdir()
    (cache / "python-need.json").write_text(json.dumps({"release": "0.9.49", "python": "3.15", "held": "secret"}))
    case = SimpleNamespace(root=tmp_path, prefix=prefix)
    assert harness.bridge_post_interpreter(case) == "python3.12"
    assert harness.bridge_python_need(case) == {"release": "0.9.49", "python": "3.15", "held": True}
    stderr = "\n".join(
        [
            json.dumps({"event": "phase", "id": "check", "state": "warning", "detail": "secret"}),
            json.dumps({"event": "phase", "id": "secret", "state": "done"}),
        ]
    )
    assert harness.bridge_update_phases(stderr) == {"check": "warning"}


def test_bridge_diagnostics_reject_non_ascii_digits(tmp_path):
    assert harness.safe_version("\uff10.9.43") is None
    cache = tmp_path / "cache_dir"
    cache.mkdir()
    (cache / "python-need.json").write_text(
        json.dumps({"release": "\uff10.9.49", "python": "3.\uff11\uff15", "held": None})
    )
    assert harness.bridge_python_need(SimpleNamespace(root=tmp_path)) == {
        "release": None,
        "python": None,
        "held": False,
    }
    managed = tmp_path / "uv-python"
    managed.mkdir()
    python = managed / "python\uff13.12"
    python.touch()
    prefix = tmp_path / "tool"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin/python").symlink_to(python)
    assert harness.bridge_post_interpreter(SimpleNamespace(root=tmp_path, prefix=prefix)) is None
    facts = harness.bridge_receipt_facts(
        '[tool]\nrequirements = [{name = "heinzel", specifier = ">=\uff10.9.43"}]'.encode()
    )
    assert facts["requirements"][0]["specifier"] is None


@pytest.mark.parametrize(
    ("actual", "version", "error"),
    [
        ("python3.15", "3.15", None),
        ("python3.14", "3.14", "must migrate to managed Python 3.15"),
        ("python3.12", "3.12", "did not migrate"),
        ("outside", "3.15", "Not the case's managed Python"),
    ],
)
def test_legacy_migration_requires_a_different_private_python315(tmp_path, actual, version, error):
    managed = tmp_path / "uv-python"
    managed.mkdir()
    previous = managed / "python3.12"
    previous.touch()
    interpreter = tmp_path / "outside" if actual == "outside" else managed / actual
    interpreter.touch()
    prefix = tmp_path / "prefix"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin/python").symlink_to(interpreter)
    case = SimpleNamespace(
        prefix=prefix,
        root=tmp_path,
        notes={"python_before": str(previous.resolve())},
        run=lambda *_args: (0, version, ""),
    )
    if error:
        with pytest.raises(harness.StepFailed, match=error):
            harness.verify_update_interpreter(case, migrated=True)
    else:
        assert harness.verify_update_interpreter(case, migrated=True) == str(interpreter.resolve())


VERSION_TABLES = {"FOREGROUND_UI", "UPGRADES_PLUGINS_ON_ADD", "LEGACY_BRIDGE_START", "LEGACY_BRIDGE_VERSION"}
ALLOWED_VERSIONS = {"0.9.2", "heinzel-clock==0.9.2", "0.9.8"}  # the deliberate older clock pin; the default old app


def written_versions(source):
    """Every X.Y.Z string constant in the harness outside the Core version tables, docstrings and allowed pins."""
    tree = ast.parse(source)
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in VERSION_TABLES for t in node.targets
        ):
            skip.update(id(n) for n in ast.walk(node))
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "UPDATE_FIXTURE_PLUGIN"
            and isinstance(node.value, ast.Constant)
            and node.value.value == "heinzel-example==0.5.2"
        ):
            skip.add(id(node.value))
        if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Module)) and ast.get_docstring(node) is not None:
            skip.add(id(node.body[0].value))
    return [
        (n.lineno, n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and id(n) not in skip
        and VERSION.search(n.value)
        and n.value not in ALLOWED_VERSIONS
    ]


def test_no_plugin_version_is_written_except_deliberate_historical_fixtures():
    """A plugin the cases install unpinned is bound from the index in each run; a literal goes stale on release."""
    assert written_versions((HARNESS / "run_acceptance.py").read_text()) == []


def test_historical_plugin_pin_is_used_only_by_the_update_fixture():
    tree = ast.parse((HARNESS / "run_acceptance.py").read_text())
    users = [
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load) and child.id == "UPDATE_FIXTURE_PLUGIN"
            for child in ast.walk(node)
        )
    ]
    assert users == ["case_update"]


INSTALL = 'case.step("install a plugin (CLI)", lambda: add(EXAMPLE_PLUGIN, "example-add", example_version))'


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (INSTALL, 'case.step("install a plugin (CLI)", lambda: add(EXAMPLE_PLUGIN, "example-add", "0.5.2"))'),
        (
            INSTALL,
            (
                'case.step(\n        "install a plugin (CLI)",\n        lambda: add(\n            "heinzel-example",\n'
                '            "example-add",\n            "0.5.2",\n        ),\n    )'
            ),
        ),
        ('EXAMPLE_PLUGIN = "heinzel-example"', 'EXAMPLE_PLUGIN = "heinzel-example"\nEXAMPLE_VERSION = "0.5.2"'),
        (
            'ui_apply(browser, "the install", row_has(EXAMPLE_PLUGIN, example_version))',
            'ui_apply(browser, "the install", row_has(EXAMPLE_PLUGIN, "0.5.2"))',
        ),
    ],
    ids=["constant-and-literal", "separate-lines", "module-constant", "page-row"],
)
def test_the_version_guard_finds_a_written_back_example_version(old, new):
    source = (HARNESS / "run_acceptance.py").read_text()
    assert old in source
    assert "0.5.2" in {value for _, value in written_versions(source.replace(old, new))}


@pytest.mark.parametrize("defect", [None, "installed-bytes", "registry-advanced"])
def test_clock_update_verifies_installed_runtime_files(defect, tmp_path):
    prefix = tmp_path / "tool"
    site = prefix / "lib/python3.14/site-packages"
    metadata = site / "heinzel_clock-0.9.6.dist-info/METADATA"
    metadata.parent.mkdir(parents=True)
    version = "0.9.7" if defect == "registry-advanced" else "0.9.6"
    metadata.write_text(f"Version: {version}\n")
    module = site / "heinzel_clock/__init__.py"
    module.parent.mkdir()
    module.write_bytes(b"changed" if defect == "installed-bytes" else b"accepted")
    with zipfile.ZipFile(tmp_path / "clock-update-target.whl", "w") as wheel:
        wheel.writestr("heinzel_clock/__init__.py", b"accepted")

    case = SimpleNamespace(root=tmp_path.resolve(), prefix=prefix)
    if defect:
        match = "differs from the accepted wheel" if defect == "installed-bytes" else "expected 0.9.6"
        with pytest.raises(harness.StepFailed, match=match):
            harness.verify_clock_update(case, "0.9.6")
    else:
        assert harness.verify_clock_update(case, "0.9.6") == 1


class AccountBrowser:
    """A new account trigger has no obsolete Profile suffix and accepts only keyboard focus."""

    def __init__(self):
        self.tabs, self.opened, self.clicks = 0, False, []

    def locate(self, selector, name, exact):
        assert selector == "button[aria-haspopup='menu'][title=\"Mira Example · engineer\"]"
        assert name == "Mira"
        assert exact is False
        return "account"

    def present(self, *_args):
        return False

    def evaluate(self, expression):
        assert ".focus(" not in expression
        if "document.activeElement" in expression:
            return self.tabs == 2
        assert "aria-expanded" in expression
        assert "false" in expression
        return not self.opened

    def press(self, key, code, virtual):
        if key == "Tab":
            assert code == "Tab"
            assert virtual == 9
            self.tabs += 1
        else:
            assert (key, code, virtual) == ("Enter", "Enter", 13)
            assert self.tabs == 2
            self.opened = True

    def wait(self, expression, _message, _timeout):
        if "aria-expanded" in expression:
            assert self.opened
        else:
            assert "Installed on this machine" in expression
            assert self.clicks == [("[role=menuitem]", "Settings"), ("button", "Plugins")]

    def click(self, selector, name, *_args, **_kwargs):
        assert self.opened
        self.clicks.append((selector, name))


def test_account_navigation_requires_real_tab_enter_and_the_bound_menu_identity(monkeypatch):
    monkeypatch.setattr(harness.time, "sleep", lambda _seconds: None)
    browser = AccountBrowser()
    harness.ui_open_plugins(browser, None)
    assert browser.tabs == 2
    assert browser.opened


@pytest.mark.parametrize("action", ["add", "remove"])
def test_plugin_preview_requires_action_bound_consent_and_cancel_before_writing(action):
    title = "Install heinzel-example" if action == "add" else "Remove heinzel-example?"
    permission = (
        "Runs as code on this computer with your permissions."
        if action == "add"
        else "Its modules stop and its files are deleted."
    )
    text = f"{title}\n{permission}\nhz restarts and this page reloads itself.\nYour open work is preserved."
    selector = harness.plugin_dialog(action, "confirm")

    class Preview:
        def wait(self, expression, _what, timeout):
            assert json.dumps(selector) in expression
            assert timeout == 30
            return text

        def present(self, buttons, name):
            assert buttons == f"{selector} button"
            return name in ("Cancel", "Install" if action == "add" else "Remove")

    assert harness.plugin_preview(Preview(), action, "heinzel-example", 30) == text
    text = text.replace("hz restarts and this page reloads itself.", "")
    with pytest.raises(harness.StepFailed, match="preview lacks"):
        harness.plugin_preview(Preview(), action, "heinzel-example", 30)


@pytest.mark.parametrize("action", ["add", "remove"])
@pytest.mark.parametrize("failure", [None, "The exact installed plugin could not be verified"])
@pytest.mark.parametrize("settings_open", [True, False])
def test_plugin_apply_requires_one_confirmation_new_document_and_success(action, failure, settings_open, monkeypatch):
    monkeypatch.setattr(harness.time, "monotonic", lambda: 100)
    navigation = []
    monkeypatch.setattr(harness, "ui_open_plugins", lambda *_args, **kwargs: navigation.append(kwargs["deadline"]))

    class Change:
        locate = staticmethod(harness.Browser.locate)

        def __init__(self):
            self.clicks, self.waits, self.marker = [], [], False

        def evaluate(self, expression):
            if expression == "window.__hzAcceptancePluginRestart = true":
                self.marker = True
                return None
            if expression == "new URLSearchParams(location.search).has('settings')":
                return settings_open
            if expression == "document.body.innerText.includes('Installed on this machine')":
                return True
            selector = json.dumps(harness.plugin_dialog(action, "failed"))
            assert expression == f"document.querySelector({selector})?.innerText"
            return failure

        def click(self, selector, name, **kwargs):
            assert self.marker
            assert kwargs["timeout"] <= 60
            self.clicks.append((selector, name))

        def wait(self, expression, _what, timeout):
            assert timeout <= 360
            self.waits.append(expression)
            return True

    browser = Change()
    if failure:
        with pytest.raises(harness.StepFailed, match=failure):
            harness.ui_apply(browser, "the fixture change", "installed-row", action=action)
        assert navigation == ([] if settings_open else [460])
        assert len(browser.clicks) == 1
    else:
        harness.ui_apply(browser, "the fixture change", "installed-row", action=action)
        assert navigation == ([] if settings_open else [460])
        assert browser.clicks == [
            (f"{harness.plugin_dialog(action, 'confirm')} button", "Install" if action == "add" else "Remove"),
            (f"{harness.plugin_dialog(action, 'done')} button", "Done"),
        ]
        assert browser.waits[-1] == "installed-row"
    assert "window.__hzAcceptancePluginRestart === undefined" in browser.waits[0]
    assert 'data-stage=\\"done\\"' in browser.waits[1]
    assert 'data-stage=\\"failed\\"' in browser.waits[1]


@pytest.mark.parametrize("value", ["0", "-1", "3901", "1.5", "nan"])
def test_bridge_quota_cli_rejects_unbounded_wait(value):
    with pytest.raises(SystemExit):
        harness.parse_args(
            ["--expect-version", "published", "--expect-app-version", "published", "--bridge-quota-wait-seconds", value]
        )


@pytest.mark.parametrize("value", [None, "1", "3900"])
def test_bridge_quota_cli_budget(value):
    argv = ["--expect-version", "published", "--expect-app-version", "published"]
    if value is not None:
        argv += ["--bridge-quota-wait-seconds", value]
    assert harness.parse_args(argv).bridge_quota_wait_seconds == (int(value) if value else 120)


@pytest.mark.parametrize("oversleep", [False, True])
def test_bridge_quota_long_window_records_only_bounded_sleep_credit(monkeypatch, oversleep):
    elapsed = 0.0
    calls = []

    def sleep(delay):
        nonlocal elapsed
        assert delay == 3601
        elapsed += 5000 if oversleep else delay

    def probe(timeout):
        nonlocal elapsed
        calls.append(timeout)
        elapsed += 2  # Network work must consume workload time, never sleep credit.
        return (0, 4602) if len(calls) == 1 else (1, 8202)

    monkeypatch.setattr(
        harness, "time", SimpleNamespace(monotonic=lambda: elapsed, time=lambda: 1000 + elapsed, sleep=sleep)
    )
    monkeypatch.setattr(harness, "_bridge_quota", probe)
    case = SimpleNamespace(
        ctx={"args": SimpleNamespace(bridge_quota_wait_seconds=3900)}, notes={}, infrastructure_sleep=0.0
    )
    if oversleep:
        with pytest.raises(harness.Refused):
            harness.wait_for_legacy_bridge(case)
        assert case.infrastructure_sleep == 3900
        assert len(calls) == 1
    else:
        result = harness.wait_for_legacy_bridge(case)
        assert result["reset_epoch"] == 8202
        assert result["remaining_after_probe"] == 1
        assert result["elapsed_seconds"] == 3605
        assert case.infrastructure_sleep == 3601
        assert len(calls) == 2
    assert case.notes["legacy_bridge_quota"]["sleep_seconds"] == case.infrastructure_sleep
    assert elapsed - case.infrastructure_sleep >= 2


@pytest.mark.parametrize("work_seconds", [2400, 2401])
def test_bridge_sleep_credit_keeps_original_workload_deadline(monkeypatch, work_seconds):
    monkeypatch.setattr(harness.time, "monotonic", lambda: 3601 + work_seconds)
    monkeypatch.setattr(harness, "manager_gate", lambda: None)
    case = SimpleNamespace(
        started=0,
        infrastructure_sleep=3601,
        steps=[],
        first_failure=None,
        check_denied=lambda: None,
        notes={"legacy_bridge_quota": {"sleep_seconds": 999999}},
    )
    if work_seconds > harness.CASE_DEADLINE:
        with pytest.raises(harness.StepFailed, match="case deadline"):
            harness.Case.step(case, "original updater", lambda: pytest.fail("must not execute"))
    else:
        assert harness.Case.step(case, "original updater", lambda: "executed") == "executed"


def test_acceptance_dry_run_is_skipped_on_release_pull_requests_and_dispatch_still_runs():
    workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/acceptance.yml").read_text()
    assert "if: github.event_name != 'pull_request' || !startsWith(github.head_ref, 'release/')" in workflow
    assert "workflow_dispatch:" in workflow
    assert "workflow_call:" in workflow


def test_full_acceptance_quota_window_does_not_change_sharded_budget():
    workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/acceptance.yml").read_text()
    shard = (Path(__file__).resolve().parents[2] / ".github/workflows/acceptance-sharded.yml").read_text()
    assert "--bridge-quota-wait-seconds 3900" in workflow
    assert "timeout-minutes: 240" in workflow
    assert "--bridge-quota-wait-seconds" not in shard
    assert "timeout-minutes: 15" in shard
