"""The README demo's tape is assembled from the core intro and the snippets modules contribute."""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "demo_tape.py"
spec = importlib.util.spec_from_file_location("demo_tape", SCRIPT)
assert spec
assert spec.loader
demo_tape = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo_tape)

INTRO = '# The intro.\n\nOutput demo.gif\n\nRequire hz\n\nSet Shell bash\n\nType "hz --version"\nEnter\n'


def module(root: Path, folder: str, snippet: str) -> Path:
    """A module package under root with a demo.tape snippet."""
    package = root / folder
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "demo.tape").write_text(snippet, encoding="utf-8")
    return package / "demo.tape"


@pytest.fixture
def root(tmp_path):
    (tmp_path / "docs" / "assets").mkdir(parents=True)
    (tmp_path / "docs" / "assets" / "demo-intro.tape").write_text(INTRO, encoding="utf-8")
    return tmp_path


def test_snippets_come_from_plugins_first_then_built_in_modules_each_sorted_by_path(root):
    doctor = module(root, "src/heinzel/modules/system/doctor", 'Type "hz system doctor"\n')
    agent = module(root, "src/heinzel/modules/system/agent", 'Type "hz system agent"\n')
    zebra = module(root, "plugins/heinzel-zebra/src/heinzel_zebra", 'Type "hz zebra"\n')
    clock = module(root, "packages/heinzel-clock/src/heinzel_clock", 'Type "hz clock"\n')
    assert demo_tape.snippets(root) == [clock, zebra, agent, doctor]


def test_a_snippet_types_only_real_hz_commands_and_sets_nothing(root):
    snippet = module(
        root,
        "src/heinzel/modules/fake",
        '# fine\nType "hz fake"\nType@50ms `clear`\nWait@60s\nSet FontSize 30\nOutput x.gif\nType "echo faked"\n',
    )
    found = demo_tape.problems(snippet, root)
    assert [problem.split(":")[1] for problem in found] == ["5", "6", "7"]


def test_a_snippet_lives_next_to_a_module(root):
    stray = root / "docs" / "demo.tape"
    stray.write_text('Type "hz x"\n', encoding="utf-8")
    assert demo_tape.problems(stray, root) == ["docs/demo.tape: not next to a module's __init__.py"]


def test_assembling_hoists_requires_clears_between_snippets_and_holds_the_end(root):
    module(root, "packages/heinzel-clock/src/heinzel_clock", '# Clock.\nRequire jq\nType "hz clock --json | jq"\n')
    module(root, "src/heinzel/modules/system", 'Require jq\nRequire hz\nType "hz system doctor"\n')
    lines = demo_tape.assemble(root).splitlines()
    assert lines[lines.index("Output demo.gif") + 2 : lines.index("Set Shell bash")] == ["Require hz", "Require jq", ""]
    clock = lines.index("# --- packages/heinzel-clock/src/heinzel_clock ---")
    assert lines[clock + 1 : clock + 6] == [*demo_tape.CLEAR, 'Type "hz clock --json | jq"']
    assert lines.index("# --- src/heinzel/modules/system ---") > clock
    assert "# Clock." not in lines
    assert lines[-1] == demo_tape.HOLD


def test_assembling_refuses_a_snippet_that_breaks_the_rules(root):
    module(root, "src/heinzel/modules/fake", "Set Theme x\n")
    with pytest.raises(ValueError, match="only Enter, Hide"):
        demo_tape.assemble(root)


def test_the_committed_tape_is_assembled_from_the_current_snippets():
    """`make fix` writes it; this and `make check` fail when a snippet or the intro changed without it."""
    assert (REPO / "docs" / "assets" / "demo.tape").read_text(encoding="utf-8") == demo_tape.assemble()
