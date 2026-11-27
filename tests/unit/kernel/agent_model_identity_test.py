"""Agent models and aliases retain their published import and serialization identities."""

import pickle
from types import ModuleType

import pytest

from heinzel.kernel import AgentResult
from heinzel.kernel import _agent_models as definitions
from heinzel.kernel import _agents as legacy
from heinzel.kernel.ai import _agents as current

pytestmark = pytest.mark.unit


def test_agent_facades_preserve_models_and_type_aliases():
    assert legacy.__all__ == current.__all__
    for name in current.__all__:
        assert getattr(legacy, name) is getattr(current, name)
    assert AgentResult is current.AgentResult


def test_incomplete_resident_generation_uses_the_whole_new_contract(monkeypatch):
    assert current._source_models() is legacy
    older = ModuleType("published_agents")
    older.AgentResult = object()
    with monkeypatch.context() as temporary:
        temporary.setattr(current, "_resident", older)
        assert current._source_models() is definitions
    assert current._source_models() is legacy


@pytest.mark.parametrize("module", ["heinzel.kernel._agents", "heinzel.kernel.ai._agents"])
def test_both_published_pickle_paths_restore_the_one_result_model(module):
    # Protocol 0's GLOBAL below names a previously published type and contains no executable payload.
    published = f"c{module}\nAgentResult\np0\n.".encode()
    assert pickle.loads(published) is AgentResult  # noqa: S301 - fixed class reference in test
    assert pickle.dumps(AgentResult, protocol=0) == b"cheinzel.kernel.ai._agents\nAgentResult\np0\n."
    usage = current.TokenUsage(input_tokens=3)
    result = AgentResult(runner="example", status="completed", duration_seconds=0, usage=usage)
    restored = AgentResult.model_validate_json(result.model_dump_json())
    assert restored == result
    assert type(restored.usage) is current.TokenUsage
