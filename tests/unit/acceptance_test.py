"""The acceptance harness's verdict logic, without network or installs (scripts/acceptance/)."""

import argparse
import importlib.util
import json
import shutil
import subprocess
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
