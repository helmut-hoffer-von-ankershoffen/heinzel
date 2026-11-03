"""The confidentiality check, with made-up terms: the real ones never enter the repository, not even in tests."""

import importlib.util
import io
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_confidentiality.py"
spec = importlib.util.spec_from_file_location("check_confidentiality", SCRIPT)
assert spec
assert spec.loader
confidentiality = importlib.util.module_from_spec(spec)
spec.loader.exec_module(confidentiality)

TERMS = "# made up\nacme\nproject   zebra  # a phrase\n\n"


@pytest.fixture(autouse=True)
def terms(monkeypatch, tmp_path):
    monkeypatch.setenv("CONFIDENTIAL_TERMS", TERMS)
    monkeypatch.setenv("CONFIDENTIAL_TERMS_FILE", str(tmp_path / "absent"))


def test_terms_skip_blank_lines_and_comments():
    assert confidentiality.parse(TERMS) == ["acme", "project   zebra"]


def findings(terms, text):
    return sorted((f.line, f.term) for f in confidentiality.scan("f", text, confidentiality.patterns(terms)))


def test_a_short_term_inside_a_longer_word_does_not_match():
    text = "acmeish and subacme\nminimal\nAcmeCorp"
    assert findings(["acme"], text) == []
    assert findings(["mini"], "minimal\nminimum") == []


def test_the_term_alone_or_next_to_punctuation_digits_and_markdown_matches():
    text = "ACME\nwe, acme.\n**acme**\n`acme`\n[acme](x)\n(acme)\nacme's\nacme_cli\nmy-acme-cli\n# acme\n"
    text += "> acme: yes\nacme2\n2acme"
    assert findings(["acme"], text) == [(n, 1) for n in range(1, 14)]


def test_long_terms_match_anywhere_also_inside_identifiers_and_next_to_digits():
    text = "globexdb\nmy_globex_cli\nGLOBEX\nunglobex\n[globex]\nGlobexCorp\nglobex2024\n2globex\nmyGlobexDb"
    assert findings(["globex"], text) == [(n, 1) for n in range(1, 10)]


def test_a_finding_per_term_and_line_with_the_right_number():
    assert findings(["acme", "globex"], "acme and globex\nGlobex\nnothing") == [(1, 1), (1, 2), (2, 2)]


def test_phrases_match_across_separators_and_line_breaks_or_glued():
    text = "the Project\n  Zebra\nproject-zebra\nprojectzebra\nproject_zebra\nprojects zebra"
    assert findings(["project zebra"], text) == [(1, 1), (3, 1), (4, 1), (5, 1)]


def test_a_phrase_does_not_match_inside_longer_words_or_with_a_word_missing():
    text = "reproject zebras\nproject zebras\nproject\nx\nzebra\nthe *project zebra*, yes"
    assert findings(["project zebra"], text) == [(6, 1)]


def test_invisible_and_compatibility_characters_do_not_hide_a_term():
    # A zero-width space, full-width letters and a soft hyphen.
    assert findings(["globex"], "glo\u200bbex\n\uff27\uff2c\uff2f\uff22\uff25\uff38\nglo\u00adbex") == [
        (1, 1),
        (2, 1),
        (3, 1),
    ]


def test_binary_files_are_scanned_through_their_printable_strings(tmp_path):
    blob = tmp_path / "image.png"
    blob.write_bytes(b"\x89PNG\0\0tEXtComment\0made by Acme\0\xff")
    assert confidentiality.main([str(blob)]) == 1


def test_findings_name_the_location_and_number_but_not_the_term(tmp_path, capsys):
    leak = tmp_path / "notes.md"
    leak.write_text("fine\nwe work at Acme\n", encoding="utf-8")
    assert confidentiality.main([str(leak)]) == 1
    err = capsys.readouterr().err
    assert f"{leak}:2: confidential term #1" in err
    assert "acme" not in err.lower().replace(str(leak).lower(), "")


def test_paths_are_checked_too(tmp_path):
    assert confidentiality.main([str(tmp_path / "acme-plan.md")]) == 1


def test_clean_binary_files_pass(tmp_path):
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"\0\x01nothing to see\xff")
    assert confidentiality.main([str(blob)]) == 0


def test_git_identities_and_branches_are_checked(monkeypatch):
    for name in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):  # CI runners have no git identity
        monkeypatch.setenv(name, "Dev")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "dev@acme.example")
    assert confidentiality.main(["--identity"]) == 1
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "dev@example.com")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "dev@example.com")
    assert confidentiality.main(["--identity"]) == 0
    assert confidentiality.main(["--branch"]) == 0


def test_preserved_push_refs_are_exact_and_malformed_input_fails_closed():
    local, remote = "1" * 40, "0" * 40
    assert confidentiality._pushed_refs(f"refs/heads/one {local} refs/heads/one {remote}\n") == [(local, remote)]
    with pytest.raises(ValueError, match="pushed-ref list is invalid"):
        confidentiality._pushed_refs("not a complete ref record")


def test_preserved_deletions_and_empty_pushes_have_no_commit_tips(monkeypatch, tmp_path):
    saved = tmp_path / "push-refs"
    monkeypatch.setenv("HZ_DEV_PUSH_REFS_FILE", str(saved))
    monkeypatch.setenv("PRE_COMMIT", "1")
    monkeypatch.setenv("PREK_HOOK_STAGE", "pre-push")
    monkeypatch.setattr(confidentiality.sys, "stdin", io.StringIO(""))
    for refs in ("", f"(delete) {'0' * 40} refs/heads/one {'0' * 40}\n"):
        saved.write_text(refs)
        assert confidentiality._push_range() == ["--all", "--not", "--all"]


def test_inherited_empty_marker_cannot_hide_real_stdin(monkeypatch, tmp_path):
    saved = tmp_path / "empty"
    saved.write_bytes(b"")
    monkeypatch.setenv("HZ_DEV_PUSH_REFS_FILE", str(saved))
    refs = f"refs/heads/one {'1' * 40} refs/heads/one {'0' * 40}\n"
    monkeypatch.setattr(confidentiality.sys, "stdin", io.StringIO(refs))
    assert confidentiality._push_input() == refs


def test_preserved_input_outside_hook_runner_fails_closed(monkeypatch, tmp_path):
    saved = tmp_path / "empty"
    saved.write_bytes(b"")
    monkeypatch.setenv("HZ_DEV_PUSH_REFS_FILE", str(saved))
    monkeypatch.delenv("PRE_COMMIT", raising=False)
    monkeypatch.setattr(confidentiality.sys, "stdin", io.StringIO(""))
    with pytest.raises(ValueError, match="only inside the pre-push runner"):
        confidentiality._push_input()


def test_only_fresh_destination_commits_are_excluded(monkeypatch):
    local, remote, other = "1" * 40, "2" * 40, "3" * 40
    monkeypatch.setenv("PRE_COMMIT_REMOTE_URL", "destination")
    calls = []

    def git(*args):
        calls.append(args)
        return f"{remote}\trefs/heads/main\n{other}\trefs/heads/unknown\n"

    monkeypatch.setattr(confidentiality, "_git", git)
    monkeypatch.setattr(confidentiality, "_known", lambda sha: sha == remote)
    monkeypatch.setattr(confidentiality, "_push_input", lambda: f"refs/heads/new {local} refs/heads/new {'0' * 40}\n")
    assert confidentiality._push_range() == [local, "--not", remote]
    assert calls == [("ls-remote", "--refs", "--", "destination")]


def test_missing_destination_proof_does_not_exclude_any_remote(monkeypatch):
    local = "1" * 40
    monkeypatch.delenv("PRE_COMMIT_REMOTE_URL", raising=False)
    monkeypatch.setattr(confidentiality, "_push_input", lambda: f"refs/heads/new {local} refs/heads/new {'0' * 40}\n")
    assert confidentiality._push_range() == [local, "--not"]


def test_failed_destination_lookup_stops_without_printing_its_url(monkeypatch):
    destination = "private-destination"
    monkeypatch.setenv("PRE_COMMIT_REMOTE_URL", destination)

    def failed(*args):
        raise subprocess.CalledProcessError(1, args, stderr="private diagnostic")

    monkeypatch.setattr(confidentiality, "_git", failed)
    with pytest.raises(ValueError, match="destination could not be inspected") as error:
        confidentiality._destination_commits()
    assert destination not in str(error.value)


@pytest.mark.parametrize(
    "email",
    [
        "dev@Some-MacBook-Air.local",
        "dev@build-box.LocalDomain",
        "dev@nas.lan",
        "dev@router.home",
        "dev@laptop.corp.internal",
        "dev@laptop",
        "dev@machine.local.",
    ],
)
def test_hostname_emails_are_refused(email):
    assert confidentiality.hostname_email(f"Dev <{email}> 1792821657 +0200") == email


@pytest.mark.parametrize(
    "email",
    [
        "dev@example.com",
        "noreply@github.com",
        "12345+dev@users.noreply.github.com",
        "41898282+github-actions[bot]@users.noreply.github.com",  # CI's bot identity
        "49699333+dependabot[bot]@users.noreply.github.com",
        "dev@localhost.example.org",
    ],
)
def test_mailbox_emails_pass(email):
    assert confidentiality.hostname_email(f"Dev <{email}> 1792821657 +0200") is None


def _identity(monkeypatch, email):
    for role in ("AUTHOR", "COMMITTER"):  # both, so neither the machine's nor CI's git configuration leaks in
        monkeypatch.setenv(f"GIT_{role}_NAME", "Dev")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", email)


def test_the_identity_check_refuses_a_hostname_email_and_says_how_to_fix_it(monkeypatch, capsys):
    _identity(monkeypatch, "dev@Some-MacBook-Air.local")
    assert confidentiality.main(["--identity"]) == 1
    err = capsys.readouterr().err
    assert "author email dev@Some-MacBook-Air.local" in err
    assert "committer email" in err
    assert "git config --global user.email" in err
    assert re.search(r"\bID\+USERNAME@users\.noreply\.github\.com\b", err)  # an address, not a URL check


def test_the_hostname_check_runs_without_terms(monkeypatch):
    """Plugin developers usually have no term list; the check protects them too."""
    monkeypatch.delenv("CONFIDENTIAL_TERMS")
    _identity(monkeypatch, "dev@Some-MacBook-Air.local")
    assert confidentiality.main(["--identity"]) == 1
    _identity(monkeypatch, "41898282+github-actions[bot]@users.noreply.github.com")
    assert confidentiality.main(["--identity"]) == 0
    assert confidentiality.main(["--text", "dev@Some-MacBook-Air.local"]) == 0  # only --identity checks emails


def test_commit_messages_and_text_are_checked(tmp_path):
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("feat: integrate Project Zebra\n", encoding="utf-8")
    assert confidentiality.main(["--commit-msg", str(message)]) == 1
    assert confidentiality.main(["--text", "fix: nothing to see"]) == 0


def test_without_terms_the_check_skips_unless_required(monkeypatch, capsys):
    monkeypatch.delenv("CONFIDENTIAL_TERMS")
    assert confidentiality.main(["--text", "acme"]) == 0
    assert "skipped" in capsys.readouterr().out
    assert confidentiality.main(["--require", "--text", ""]) == 1


def test_terms_can_come_from_a_file(monkeypatch, tmp_path):
    monkeypatch.delenv("CONFIDENTIAL_TERMS")
    path = tmp_path / "terms"
    path.write_text("acme\n", encoding="utf-8")
    monkeypatch.setenv("CONFIDENTIAL_TERMS_FILE", str(path))
    assert confidentiality.load_terms() == ["acme"]


def test_the_check_guards_commits_lint_ci_and_publishing():
    hooks = (REPO / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "check_confidentiality.py --commit-msg" in hooks
    assert "check_confidentiality.py --identity" in hooks
    assert "check_confidentiality.py --branch" in hooks
    assert "scripts/check_confidentiality.py\n" in hooks
    assert '"&confidential=uv run --script scripts/check_confidentiality.py"' in (REPO / "Makefile").read_text(
        encoding="utf-8"
    )
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "check_confidentiality.py --require" in ci
    assert "run: make commits" in ci
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    assert "check_confidentiality.py --messages $(BASE)..HEAD" in makefile
    assert 'check_confidentiality.py --text "$$TITLE"' in makefile
    assert 'check_confidentiality.py --text "$$BRANCH"' in makefile
    publish = (REPO / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    assert "check_confidentiality.py" in publish
