"""Wheel packaging and browser setup share one frontend build, even on a cold parallel run."""

import importlib.util
import multiprocessing
import os
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
REPO = Path(__file__).resolve().parents[2]


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def build_tools(tmp_path, monkeypatch):
    web = load(REPO / "scripts/web_build.py")
    interface = types.ModuleType("hatchling.builders.hooks.plugin.interface")
    interface.BuildHookInterface = type(
        "BuildHookInterface", (), {"target_name": "wheel", "__class_getitem__": classmethod(lambda cls, _: cls)}
    )
    monkeypatch.setitem(sys.modules, "hatchling.builders.hooks.plugin.interface", interface)
    hatch = load(REPO / "hatch_build.py")
    monkeypatch.setattr(hatch, "_web", lambda: web)
    monkeypatch.setattr(
        hatch, "_script", lambda _: types.SimpleNamespace(APP=tmp_path / "absent", current=lambda: True)
    )
    return web, hatch


def test_wheel_packaging_waits_for_a_browser_build_and_reuses_its_complete_output(tmp_path, monkeypatch, build_tools):
    web, hatch = build_tools
    monkeypatch.setattr(web, "ROOT", tmp_path)
    ready, active = tmp_path / "complete.html", tmp_path / "building"
    monkeypatch.setattr(web, "current", lambda *_: ready.is_file())
    context = multiprocessing.get_context("fork")

    def worker(browser, channel):
        flock = web.fcntl.flock

        def locking(handle, operation):
            if operation == web.fcntl.LOCK_EX:
                channel.send("locking")
            return flock(handle, operation)

        def build(_static=web.STATIC):
            # A second destructive build while the first is active must fail, without relying on sleeps.
            with os.fdopen(os.open(active, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"):
                channel.send("building")
                assert channel.recv() == "finish"
                ready.write_text("complete app")
            return web.STATIC

        web.fcntl.flock, web.build = locking, build
        try:
            if browser:
                web.ensure_built()
            else:
                hatch.WebAppHook().initialize("standard", {"force_include": {}})
        except Exception as error:
            channel.send(repr(error))
            raise

    browser_channel, browser_child = context.Pipe()
    wheel_channel, wheel_child = context.Pipe()
    browser = context.Process(target=worker, args=(True, browser_child))
    wheel = context.Process(target=worker, args=(False, wheel_child))
    try:
        browser.start()
        assert browser_channel.poll(10)
        assert browser_channel.recv() == "locking"
        assert browser_channel.poll(10)
        assert browser_channel.recv() == "building"
        wheel.start()
        assert wheel_channel.poll(10)
        observed = wheel_channel.recv()
        browser_channel.send("finish")
        browser.join(10)
        wheel.join(10)
        assert browser.exitcode == wheel.exitcode == 0, observed
        assert ready.read_text() == "complete app"
    finally:
        for process in (browser, wheel):
            if process.is_alive():
                process.terminate()
                process.join(10)
