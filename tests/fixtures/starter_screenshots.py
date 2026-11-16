"""Exercise reviewed starter stories as private drafts, never fabricate an image-review verdict."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from heinzel_plugin_tools import capture
from heinzel_plugin_tools.planning import DEFINITION, STORY, record_definition, validate_definition
from heinzel_plugin_tools.privacy import CaptureError, validate_bundle


def reviewed_plan(destination, preset, authored):
    """Use only reviewed read-only operations present in the two starter presets."""
    # Reviewed fictional template behavior: hello greets Ada; contributions expose read-only info and inspector.
    capture.write_default_plan(destination)
    plan = capture.read_plan(destination)
    module = next(iter(capture.project_data(destination)["project"]["entry-points"]["heinzel.modules"]))
    command = [module, "double", "7"] if authored else [module, "hello" if preset == "minimal" else "info"]
    command.append("--json")
    for scene in plan["scenes"]:
        if scene["surface"] == "cli":
            scene.update(command=command, caption="Fictional starter operation: " + " ".join(command))
        for step in scene.get("steps", []):
            if step["action"] == "terminal":
                step["command"] = command
        if preset == "contributions" and scene.get("plugin_card"):
            scene.update(
                route=f"/?panel={module}:inspector",
                plugin_card=False,
                caption="Fictional starter inspector showing installed plugin information",
                steps=[
                    {"action": "wait", "role": "region", "name": module},
                    {"action": "wait", "text": "Version 0.0.0"},
                ],
            )
    lines = ["format = 1"]
    for scene in plan["scenes"]:
        lines.append("\n[[scenes]]")
        lines.extend(f"{key} = {capture.toml_value(value)}" for key, value in scene.items())
    (destination / capture.PLAN).write_text("\n".join(lines) + "\n")
    plan = capture.read_plan(destination)
    return plan, module


def draft(project: Path, destination: Path, core: str, preset: str, previous: Path | None) -> None:
    """Capture an isolated source snapshot and verify all source-bound private evidence."""
    shutil.copytree(project, destination, ignore=shutil.ignore_patterns(*capture.IGNORED))
    if previous:
        for relative in (capture.PLAN, DEFINITION, STORY):
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(previous / relative, target)
        try:
            validate_definition(destination, capture.read_plan(destination))
        except CaptureError:
            pass
        else:
            msg = "The stock definition must reject the authored source change"
            raise AssertionError(msg)
        (destination / capture.PLAN).unlink()
    plan, module = reviewed_plan(destination, preset, bool(previous))
    story = (
        "Reviewed deterministic Core starter fixture. Show the installed fictional plugin in the app "
        "and run its read-only operation in the app terminal and CLI. Minimal hello greets Ada; "
        "contributions info reports the plugin and Core versions, with its inspector view. "
        "After the authored change, double 7 must show 14. These are real isolated captures, "
        "not a published gallery; independent image/privacy review remains required."
    )
    definition = record_definition(destination, plan, story, reviewer="Core starter fixture source review")
    result = subprocess.run(
        [
            "uv",
            "run",
            "--quiet",
            "--no-project",
            "--script",
            str(Path(capture.__file__)),
            "--project",
            str(destination),
            "--core",
            core,
            "--draft",
            "--json",
        ],
        cwd=destination,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["ok"], report
    assert report["review_required"], report
    assert report["fresh"] is False, report
    folder = Path(report["draft"])
    bundle = json.loads((folder / "review-input.json").read_text())
    validate_bundle(bundle, folder)
    assert bundle["definition"] == definition == validate_definition(destination, plan)
    assert bundle["inputs_sha256"] == capture.input_digest(destination, plan)
    assert len(bundle["images"]) == len(plan["scenes"]) == 3
    output = json.loads((folder / "cli.txt").read_text())
    if previous:
        assert output == 14
    elif preset == "minimal":
        assert output == "Hello, Ada!"
    else:
        assert output["plugin"] == capture.project_data(destination)["project"]["name"]
        assert module in output["modules"]
        assert output["heinzel_version"] == bundle["core_version"]
    assert not (destination / capture.MANIFEST).exists(), "Private drafts must not publish a gallery"
    (destination / "draft-result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(  # noqa: T201 - fixture reports only fixed validation counts
        json.dumps(
            {
                "preset": preset,
                "authored": bool(previous),
                "images": len(bundle["images"]),
                "review_required": True,
                "published": False,
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--core", required=True)
    parser.add_argument("--preset", choices=("minimal", "contributions"), required=True)
    parser.add_argument("--previous", type=Path)
    args = parser.parse_args()
    draft(args.project, args.destination, args.core, args.preset, args.previous)
