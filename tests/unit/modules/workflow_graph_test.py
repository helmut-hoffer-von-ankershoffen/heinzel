"""The contract graph: contracts linked by the outputs they maintain and the inputs that read them (ADR 0021)."""

import json
import textwrap
from pathlib import Path

import pytest

from heinzel.modules.workflow._contracts import Contract, read_contract
from heinzel.modules.workflow._graph import build, mermaid, problems
from heinzel.modules.workflow._models import Namespace

pytestmark = pytest.mark.unit


def text_of(name: str, *reads: str, output: str = "digest") -> str:
    """A valid contract `name` maintaining `output` and reading each of `reads` (contracts or tracker projects)."""
    inputs = {f"in{i}": read for i, read in enumerate(reads)}
    lines = []
    for key, read in inputs.items():
        if read.startswith("contract:"):
            lines.append(f'  {key}: {{read: "{read}", stage: draft}}')
        else:
            project = json.dumps(read)
            lines.append(f"  {key}: {{read: tracker.issues_updated, args: {{project: {project}}}, window: 7d}}")
    block = ("inputs:\n" + "\n".join(lines) + "\n") if lines else ""
    requires = "requires: [tracker:read]\n" if any(not r.startswith("contract:") for r in reads) else ""
    requires_section = "".join(f"- `{key}`: what it holds.\n" for key in inputs)
    text = textwrap.dedent(f"""\
        ---
        name: {name}
        kind: responsibility
        description: Keeps it true.
        hz:
          schema: 1
          title: {name}
        """) + textwrap.indent(requires + block + f"outputs:\n  {output}: {{format: markdown}}\n", "  ")
    text += "---\n\n### Goal\n\nIt is true.\n\n"
    if inputs:
        text += f"### Requires\n\n{requires_section}\n"
    return text + f"### Maintains\n\n- `{output}`: it.\n\n### Done when\n\n- [ ] It is.\n"


def make(name: str, *reads: str, namespace: Namespace = "me", output: str = "digest") -> Contract:
    """The contract of `text_of`, read in `namespace`."""
    contract = read_contract(Path(f"/{namespace}/{name}.prose.md"), namespace, text_of(name, *reads, output=output))
    assert contract.valid, contract.diagnostics
    return contract


def codes(contracts: list[Contract]) -> dict[str, list[tuple[str, str, str]]]:
    return {name: [(d.code, d.severity, d.field) for d in found] for name, found in problems(contracts).items()}


def test_a_contract_reading_another_contracts_output_links_them_through_that_asset():
    digest = make("weekly-digest", "APP")
    newsletter = make("newsletter", "contract:me/weekly-digest#digest", output="issue")
    graph = build([digest, newsletter])
    assert [(n.kind, n.id, n.maintained) for n in graph.nodes] == [
        ("asset", "contract:me/newsletter#issue", True),
        ("asset", "contract:me/weekly-digest#digest", True),
        ("contract", "me:newsletter", True),
        ("contract", "me:weekly-digest", True),
        ("source", 'tracker.issues_updated project="APP"', True),
    ]
    assert {(e.source, e.kind, e.target, e.input) for e in graph.edges} == {
        ("me:weekly-digest", "maintains", "contract:me/weekly-digest#digest", None),
        ("me:newsletter", "maintains", "contract:me/newsletter#issue", None),
        ('tracker.issues_updated project="APP"', "reads", "me:weekly-digest", "in0"),
        ("contract:me/weekly-digest#digest", "reads", "me:newsletter", "in0"),
    }
    assert (graph.cycles, graph.unmaintained) == ([], [])
    assert problems([digest, newsletter]) == {}
    assert graph.next[0].command == "hz workflow graph --mermaid"


def test_two_contracts_reading_one_source_share_its_node():
    graph = build([make("a", "APP"), make("b", "APP")])
    assert [n.id for n in graph.nodes if n.kind == "source"] == ['tracker.issues_updated project="APP"']


def test_an_input_nobody_maintains_is_flagged_with_why():
    reader = make("newsletter", "contract:me/weekly-digest#digest", "contract:me/notes#summary")
    graph = build([reader, make("notes")])
    assert [(u.input, u.asset, u.why) for u in graph.unmaintained] == [
        ("in0", "contract:me/weekly-digest#digest", "there is no contract me:weekly-digest"),
        ("in1", "contract:me/notes#summary", "me:notes has no output 'summary'; it maintains digest"),
    ]
    assert [n.maintained for n in graph.nodes if n.id == "contract:me/weekly-digest#digest"] == [False]
    assert codes([reader, make("notes")]) == {
        "me:newsletter": [
            ("HZC031", "warning", "hz.inputs.in0.read"),
            ("HZC031", "warning", "hz.inputs.in1.read"),
        ]
    }
    assert graph.next[0].command == "hz workflow check"


def test_contracts_that_depend_on_each_other_are_a_cycle_and_errors():
    a = make("a", "contract:me/b#digest")
    b = make("b", "contract:me/a#digest")
    c = make("c", "contract:me/a#digest")  # downstream of the cycle, not on it
    graph = build([a, b, c])
    assert graph.cycles == [["me:a", "me:b"]]
    assert codes([a, b, c]) == {
        "me:a": [("HZC030", "error", "hz.inputs.in0.read")],
        "me:b": [("HZC030", "error", "hz.inputs.in0.read")],
    }
    assert "on a cycle of me:a, me:b" in problems([a, b, c])["me:a"][0].message


def test_a_contract_reading_its_own_output_is_a_cycle():
    alone = make("alone", "contract:me/alone#digest")
    assert build([alone]).cycles == [["me:alone"]]
    assert codes([alone]) == {"me:alone": [("HZC030", "error", "hz.inputs.in0.read")]}


def test_a_long_chain_is_no_cycle_and_doesnt_exhaust_the_stack():
    chain = [make("c0")] + [make(f"c{i}", f"contract:me/c{i - 1}#digest") for i in range(1, 300)]
    assert build(chain).cycles == []


def test_a_team_contract_cant_read_a_personal_ones_output():
    mine = make("mine")
    ours = make("ours", "contract:me/mine#digest", namespace="team")
    assert codes([mine, ours]) == {"team:ours": [("HZC032", "error", "hz.inputs.in0.read")]}
    assert problems([make("theirs", namespace="team"), make("mine2", "contract:team/theirs#digest")]) == {}


def test_contracts_whose_frontmatter_failed_and_checked_paths_are_left_out():
    broken = read_contract(Path("/me/broken.prose.md"), "me", "no frontmatter")
    path_contract = read_contract(Path("/x/x.prose.md"), "path", text_of("x"))
    assert path_contract.valid
    graph = build([broken, path_contract])
    assert graph.nodes == []
    assert graph.next[0].command == "hz workflow new my-first-workflow"


def test_mermaid_draws_every_node_and_edge_and_marks_problems():
    a = make("a", "contract:me/b#digest")
    b = make("b", "contract:me/a#digest", "contract:me/gone#digest")
    chart = build([a, b]).mermaid
    assert chart.startswith("flowchart LR\n")
    assert '["me:a"]' in chart
    assert '("contract:me/a#35;digest")' in chart  # `#` starts an entity code in Mermaid
    assert "-- in0 -->" in chart
    assert "class " in chart
    assert "missing" in chart.splitlines()[-2]
    assert chart.splitlines()[-1].startswith("  class ")


def test_mermaid_labels_cant_break_out_of_their_quotes():
    hostile = make("hostile", 'APP"] --> x["<b>&')
    chart = build([hostile]).mermaid
    label = next(line for line in chart.splitlines() if "tracker" in line)
    assert '"]' not in label.split('>"', 1)[1].removesuffix('"]')
    assert "<b>" not in chart
    assert "#quot;" in label
    assert "#lt;b#gt;#amp;" in label
    assert (
        mermaid(build([]))
        == "flowchart LR\n  classDef missing stroke-dasharray: 5 5\n  classDef cycle stroke-width: 3px\n"
    )


def test_a_team_contract_reading_a_personal_output_is_an_error_even_before_that_output_exists():
    """The rule doesn't depend on someone's private contracts, so `check` agrees in CI and on every laptop."""
    reader = make("report", "contract:me/not-written-yet#digest", namespace="team")
    assert codes([reader]) == {"team:report": [("HZC032", "error", "hz.inputs.in0.read")]}


def test_an_upstream_with_errors_is_named_as_such_not_as_missing():
    broken = read_contract(Path("/me/weekly-digest.prose.md"), "me", "---\nname: weekly-digest\n---\n")
    assert broken.frontmatter is None
    reader = make("newsletter", "contract:me/weekly-digest#digest", output="issue")
    (diagnostic,) = problems([broken, reader])["me:newsletter"]
    assert diagnostic.code == "HZC031"
    assert "has errors" in diagnostic.message
    assert "there is no contract" not in diagnostic.message
    assert diagnostic.hint == "fix its errors: hz workflow check"
    assert build([broken, reader]).unmaintained[0].why.startswith("me:weekly-digest has errors")


def test_only_the_inputs_on_a_cycle_are_flagged_and_each_cycle_is_its_own():
    a = make("a", "contract:me/b#digest", "contract:me/d#digest")
    b = make("b", "contract:me/a#digest")
    d = make("d", "APP")
    x = make("x", "contract:me/y#digest")
    y = make("y", "contract:me/x#digest")
    found = codes([a, b, d, x, y])
    assert found["me:a"] == [("HZC030", "error", "hz.inputs.in0.read")]  # in1 reads d, which is not on the cycle
    assert "me:d" not in found
    assert build([a, b, d, x, y]).cycles == [["me:a", "me:b"], ["me:x", "me:y"]]


def test_a_graph_diagnostic_points_at_the_line_of_its_read():
    reader = make("newsletter", "contract:me/nobody#digest", output="issue")
    (diagnostic,) = problems([reader])["me:newsletter"]
    assert diagnostic.line is not None
    line = text_of("newsletter", "contract:me/nobody#digest", output="issue").splitlines()[diagnostic.line - 1]
    assert "contract:me/nobody#digest" in line


def test_a_mermaid_label_cant_start_a_markdown_string():
    assert "`" not in mermaid(build([make("a", "`b`")]))
