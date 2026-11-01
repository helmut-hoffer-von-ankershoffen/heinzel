"""Promotion's state machine: PyPI is only ever reached through passing acceptance of the same release (ADR 0114)."""

import importlib.util
import itertools
import sys
from dataclasses import replace
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SPEC = importlib.util.spec_from_file_location(
    "release_promotion", Path(__file__).resolve().parents[2] / "scripts/release_promotion.py"
)
assert SPEC is not None
assert SPEC.loader is not None
promotion = importlib.util.module_from_spec(SPEC)
sys.modules["release_promotion"] = promotion
SPEC.loader.exec_module(promotion)

IDLE = promotion.Facts(
    main_version="1.0.0",
    tag_exists=True,
    release_final=True,
    release_held=False,
    on_pypi=True,
    acceptance="passed",
    public_acceptance="passed",
    release_pr_open=False,
    shipping_commits=3,
    edge_acceptance="passed",
    app_composed=True,
    edge_app_composed=True,
    hours_since_stable=7.0,
    pypi_bytes=300 * 1024**2,
)


def walk(**changes):
    return promotion.next_step(replace(IDLE, **changes))[0]


def test_a_green_accepted_edge_opens_a_release():
    assert walk() == "open_pr"


@pytest.mark.parametrize(
    "changes",
    [
        {"release_pr_open": True},
        {"shipping_commits": 0},
        {"edge_acceptance": "running"},
        {"edge_acceptance": "failed"},
        {"hours_since_stable": 5.9},
    ],
)
def test_nothing_starts_without_every_condition(changes):
    assert walk(**changes) == "wait"


def test_an_unaccepted_edge_gets_accepted_first():
    assert walk(edge_acceptance="none") == "accept_edge"


def test_acceptance_waits_for_its_app():
    assert walk(edge_acceptance="none", edge_app_composed=False) == "wait"
    assert walk(on_pypi=False, release_held=True, release_final=False, acceptance="none", app_composed=False) == "wait"


def test_the_public_check_waits_for_the_app():
    assert walk(public_acceptance="none", app_composed=False) == "wait"


def test_the_pypi_budget_stops_promotion():
    assert walk(pypi_bytes=int(0.8 * 10 * 1024**3)) == "stop"


def test_the_walk_from_release_commit_to_verified():
    base = {"on_pypi": False, "release_final": False, "acceptance": "none", "public_acceptance": "none"}
    assert walk(**base, tag_exists=False) == "tag"
    assert walk(**base) == "wait"  # tagged, Publish still running
    held = {**base, "release_held": True}
    assert walk(**held) == "accept"
    assert walk(**{**held, "acceptance": "running"}) == "wait"
    assert walk(**{**held, "acceptance": "passed"}) == "finalize"
    assert walk(**{**base, "release_final": True, "acceptance": "passed"}) == "upload"
    assert walk(on_pypi=True, public_acceptance="none") == "verify"
    assert walk(on_pypi=True, public_acceptance="running") == "wait"
    assert walk(on_pypi=True, public_acceptance="failed") == "stop"


def test_a_rejected_release_is_never_promoted():
    assert walk(on_pypi=False, release_final=False, release_held=True, acceptance="failed") == "stop"


@pytest.mark.parametrize(
    ("held", "final", "acceptance"),
    list(itertools.product([True, False], [True, False], ["none", "running", "passed", "failed"])),
)
def test_upload_is_chosen_only_for_a_final_accepted_release(held, final, acceptance):
    chosen = walk(on_pypi=False, release_held=held, release_final=final, acceptance=acceptance)
    if chosen == "upload":
        assert final
        assert not held
        assert acceptance == "passed"
    if final and not held and acceptance != "passed":
        assert chosen == "stop"
