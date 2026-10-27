"""The server supplies immutable actual facts while non-web processes have no invented endpoint."""

from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from heinzel import LocalWebRuntime

pytestmark = pytest.mark.unit


def test_a_non_web_kernel_has_no_invented_listener_or_origin(make_kernel):
    runtime = make_kernel().resolve(LocalWebRuntime)
    assert runtime.port is None
    assert runtime.loaded_origins == frozenset()


def test_the_server_can_bind_actual_facts_and_replace_its_complete_origin_snapshot(make_kernel):
    runtime = LocalWebRuntime(port=43210, loaded_origins=frozenset({"https://workstation.example"}))
    kernel = make_kernel()
    kernel.bind(LocalWebRuntime, runtime)
    assert kernel.resolve(LocalWebRuntime) is runtime
    with pytest.raises(FrozenInstanceError):
        runtime.port = 8737
    runtime.apply_origins(frozenset({"https://tailnet.example"}))
    assert runtime.port == 43210
    assert runtime.loaded_origins == frozenset({"https://tailnet.example"})
    with pytest.raises(ValidationError):
        runtime.apply_origins(frozenset({"https://*.example"}))
    assert runtime.loaded_origins == frozenset({"https://tailnet.example"})


def test_replacing_origins_revokes_only_removed_origins():
    revoked = []
    runtime = LocalWebRuntime(
        loaded_origins=frozenset({"https://one.example", "https://two.example"}),
        _on_origins_removed=revoked.append,
    )
    runtime.apply_origins(frozenset({"https://two.example", "https://three.example"}))
    assert revoked == [frozenset({"https://one.example"})]
    runtime.apply_origins(frozenset({"https://two.example", "https://three.example"}))
    assert revoked == [frozenset({"https://one.example"})]
