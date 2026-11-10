# ruff: noqa: INP001, PT009, SLF001 - standard library on the bootstrap checkout
"""Cover the focused runner's platform bootstrap and token boundaries."""

import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import run


class FocusedRunnerTest(unittest.TestCase):
    """Check what the reviewed runner actually invokes."""

    def test_platform_jobs_keep_their_pinned_install_and_test_commands(self) -> None:
        """Build pinned PyYAML only for CPython 3.15 and keep pytest on the prepared environment."""
        for lane in ("python315", "macos"):
            with self.subTest(lane=lane):
                job = run.MANIFEST["jobs"][lane]
                cases = []
                for selector in job["selectors"]:
                    file, name = selector.split("::", 1)
                    case = Mock(attrib={"classname": file.removesuffix(".py").replace("/", "."), "name": name})
                    case.find.return_value = None
                    cases.append(case)
                with (
                    patch.object(run, "_run") as command,
                    patch.object(run.ET, "parse") as parse,
                ):
                    parse.return_value.iter.return_value = cases
                    run._test_platform(Path("fixture"), lane)
                sync, *rest = command.call_args_list
                expected = (
                    "uv",
                    "sync",
                    "--python",
                    job["version"],
                    "--frozen",
                    "--no-build",
                    "--no-dev",
                    "--group",
                    "test",
                )
                if lane == "python315":
                    bootstrap, *rest = rest
                    self.assertEqual(sync.args, (*expected, "--no-install-package", "pyyaml"))
                    self.assertEqual(bootstrap.args, ("bash", "scripts/bootstrap-pyyaml.sh"))
                    self.assertEqual(bootstrap.kwargs["env"]["UV_PYTHON"], job["version"])
                else:
                    self.assertEqual(sync.args, expected)
                interpreter, tests = rest
                self.assertEqual(interpreter.args[:5], ("uv", "run", "--no-sync", "python", "-c"))
                self.assertEqual(interpreter.args[-1], job["version"])
                self.assertEqual(tests.args[:4], ("uv", "run", "--no-sync", "pytest"))
                self.assertEqual(tests.args[-len(job["selectors"]) :], tuple(job["selectors"]))

    def test_sonar_maps_read_only_token_only_into_protected_gate_process(self) -> None:
        """Keep the global workflow environment free of a GitHub token setting."""
        with (
            patch.dict(os.environ, {"GITHUB_WORKSPACE": "/fixture", "FOCUSED_SONAR_TOKEN": "stub"}, clear=True),
            patch.object(run.subprocess, "check_output", return_value=run.MANIFEST["base"]),
            patch.object(run.subprocess, "run") as command,
            patch.object(run, "_sonar_refs") as refs,
        ):
            run._test_sonar()
            self.assertNotIn("GITHUB_TOKEN", os.environ)
        env = command.call_args.kwargs["env"]
        self.assertEqual(env["GITHUB_TOKEN"], "stub")
        self.assertNotIn("FOCUSED_SONAR_TOKEN", env)
        self.assertEqual(env["PR_HEAD_SHA"], run.MANIFEST["candidate"])
        self.assertEqual(command.call_args.kwargs["cwd"], Path("/fixture/trusted-base"))
        self.assertEqual(refs.call_count, 2)


if __name__ == "__main__":
    unittest.main()
