"""Scoped tool reinstall preserves verified authority and caller-supplied constraints."""

import subprocess
import sys
from pathlib import Path

import pytest

from heinzel import HeinzelError, Progress
from heinzel.kernel import Installation

pytestmark = pytest.mark.unit


@pytest.fixture
def target(tmp_path):
    prefix = tmp_path / "tools/heinzel"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin/hz").write_text("fixture")
    base = tmp_path / "fixture-python"
    assert not base.exists()
    assert not base.is_symlink()
    base.write_text("fixture")
    assert base.resolve().is_relative_to(tmp_path.resolve())
    assert not base.samefile(sys.executable)
    assert not base.samefile(sys._base_executable)
    base.chmod(0o700)
    (prefix / "bin/python").symlink_to(base)
    binary = tmp_path / "commands"
    binary.mkdir()
    (binary / "hz").symlink_to(prefix / "bin/hz")
    receipt = prefix / "uv-receipt.toml"
    receipt.write_text(f"""[tool]
requirements = [{{name="heinzel"}}]
entrypoints = [{{name="hz", install-path={str(binary / "hz")!r}}}]
""")
    calls = []

    def fake_uv(command, environment):
        contents = None
        if "--constraints" in command:
            path = Path(command[command.index("--constraints") + 1])
            contents = path.read_text()
            assert path.stat().st_mode & 0o777 == 0o600
        calls.append((command, environment, contents))
        return subprocess.CompletedProcess(command, 0, "", "")

    return Installation(Progress(), prefix=prefix, uv="fake-uv", runner=fake_uv), receipt, binary, calls


def test_additional_constraints_preserve_receipt_policy_and_default_behavior(target):
    installation, receipt, _binary, calls = target
    installation.run(installation.tool_install_command("heinzel", []), "Failed", "Changing")
    assert calls[0][2] is None
    receipt.write_text(receipt.read_text() + 'constraints = [{name="other", specifier="<3"}]\n')
    installation.run(
        installation.tool_install_command("heinzel", []),
        "Failed",
        "Changing",
        additional_constraints=("heinzel==0.9.6",),
    )
    assert calls[1][2] == "other<3\nheinzel==0.9.6\n"
    assert not Path(calls[1][0][calls[1][0].index("--constraints") + 1]).exists()


def test_conflicting_or_invalid_additional_constraints_deny_before_uv(target):
    installation, receipt, _binary, calls = target
    receipt.write_text(receipt.read_text() + 'constraints = [{name="heinzel", specifier=">=0.9.7"}]\n')
    command = installation.tool_install_command("heinzel", [])
    with pytest.raises(HeinzelError, match="conflict with existing constraints"):
        installation.run(command, "Failed", "Changing", additional_constraints=("heinzel==0.9.6",))
    with pytest.raises(HeinzelError, match="additional tool constraints are invalid"):
        installation.run(command, "Failed", "Changing", additional_constraints=("--index-url evil",))
    assert calls == []


@pytest.mark.parametrize(
    "requirements",
    [
        ["heinzel==0.9.7", "heinzel @ file:///fixture/heinzel-0.9.7-py3-none-any.whl"],
        ["heinzel-extra==1", "Heinzel_Extra @ git+https://example.test/repository"],
        ["heinzel-extra[a]==1", "heinzel-extra[b]==1"],
    ],
)
def test_selected_distribution_names_must_be_unique_after_normalization(target, requirements):
    installation, _receipt, _binary, calls = target
    with pytest.raises(HeinzelError, match="same distribution more than once"):
        installation.validate_install_constraints(requirements, ())
    assert calls == []


def test_duplicate_core_selection_refuses_before_uv_and_preserves_receipt(target):
    installation, receipt, _binary, calls = target
    before = receipt.read_bytes()
    command = installation.tool_install_command("heinzel", ["heinzel @ git+https://example.test/core"])
    with pytest.raises(HeinzelError, match="same distribution more than once"):
        installation.run(command, "Failed", "Changing", additional_constraints=("heinzel==0.9.7",))
    assert calls == []
    assert receipt.read_bytes() == before


def test_duplicate_constraints_for_one_selected_distribution_remain_supported(target):
    installation, _receipt, _binary, calls = target
    installation.validate_install_constraints(["heinzel"], ("heinzel>=0.9", "heinzel==0.9.7", "heinzel==0.9.7"))
    assert calls == []


def test_nested_tool_transaction_revalidates_owned_entrypoint_before_uv(target, tmp_path):
    installation, _receipt, binary, calls = target
    foreign = tmp_path / "foreign"
    foreign.write_text("foreign")
    with installation.tool_transaction():
        command = installation.tool_install_command("heinzel", [])
        (binary / "hz").unlink()
        (binary / "hz").symlink_to(foreign)
        with pytest.raises(HeinzelError, match="verify the running uv tool"):
            installation.run(command, "Failed", "Changing", additional_constraints=("heinzel==0.9.6",))
    assert calls == []


def test_captured_constraints_remain_available_to_recovery(target):
    installation, receipt, binary, _calls = target
    receipt.write_text(receipt.read_text() + 'constraints = [{name="other", specifier="<3"}]\n')
    previous = receipt.read_text()
    paths = []
    probes = []

    def failing_then_restore(command, environment):
        path = Path(command[command.index("--constraints") + 1])
        assert path.read_text() == "other<3\nheinzel==0.9.6\n"
        paths.append(path)
        if len(paths) == 1:
            receipt.unlink()
            (binary / "hz").unlink()
            return subprocess.CompletedProcess(command, 1, "", "fixture failure")
        receipt.write_text(previous)
        (binary / "hz").symlink_to(installation.prefix / "bin/hz")
        return subprocess.CompletedProcess(command, 0, "", "")

    installation._runner = failing_then_restore
    installation.hz = lambda *args: probes.append(args) or "ok"  # type: ignore[method-assign]
    with pytest.raises(HeinzelError, match="previous tool was restored"):
        installation.run(
            installation.tool_install_command("heinzel", []),
            "Failed",
            "Changing",
            additional_constraints=("heinzel==0.9.6",),
        )
    assert len(paths) == 2
    assert paths[0] == paths[1]
    assert not paths[0].exists()
    assert probes == [("--version",)]
