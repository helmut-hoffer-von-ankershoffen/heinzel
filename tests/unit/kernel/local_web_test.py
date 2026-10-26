"""The server supplies immutable actual facts while non-web processes have no invented endpoint."""

from dataclasses import FrozenInstanceError

import pytest

from heinzel import LocalWebRuntime

pytestmark = pytest.mark.unit


def test_a_non_web_kernel_has_no_invented_listener_or_origin(make_kernel):
    runtime = make_kernel().resolve(LocalWebRuntime)
    assert runtime.port is None
    assert runtime.loaded_origins == frozenset()


def test_the_server_can_bind_actual_immutable_facts_without_exposing_credentials(make_kernel):
    runtime = LocalWebRuntime(port=43210, loaded_origins=frozenset({"https://workstation.example"}))
    kernel = make_kernel()
    kernel.bind(LocalWebRuntime, runtime)
    assert kernel.resolve(LocalWebRuntime) is runtime
    with pytest.raises(FrozenInstanceError):
        runtime.port = 8737
