"""An installed source-declared provider double with durable local transport state and a real CLI terminal."""

import base64
import csv
import hashlib
import io
import os
import pty
import re
import select
import subprocess
import time
import zipfile

from tests.integration.draft_workflow_test import SOURCE


def provider_wheel(directory, state_path):
    package = "fixture_draft_workflow"
    info = "fixture_draft_workflow-1.0.0.dist-info"
    source = SOURCE.replace("import hashlib", "import hashlib\nimport json\nfrom pathlib import Path")
    source = source.replace("Module, ReleaseFeedService", "ReleaseRecord, Module, ReleaseFeedService")
    state = f"_STATE_PATH = Path({str(state_path)!r})\n_STATE = json.loads(_STATE_PATH.read_text())\n"
    state += (
        "def _persist():\n"
        '    _STATE_PATH.write_text(json.dumps(_STATE, default=lambda value: value.model_dump(mode="json")))\n'
    )
    state += "def _refresh():\n    _STATE.clear()\n    _STATE.update(json.loads(_STATE_PATH.read_text()))\n"
    source = re.sub(r"_STATE = .*?\n\nclass Feed", state + "\nclass Feed", source, flags=re.DOTALL)
    source = source.replace(
        "    def target(self, reference, section):", "    def target(self, reference, section):\n        _refresh()"
    )
    source = source.replace("    def delivery_target(self):", "    def delivery_target(self):\n        _refresh()")

    source = source.replace(
        'releases=tuple(_STATE["releases"][int(cursor):])',
        "releases=tuple(ReleaseRecord.model_validate_json(json.dumps(value)) "
        'for value in _STATE["releases"][int(cursor):])',
    )
    source = source.replace('_STATE["revision"] += 1', '_STATE["revision"] += 1\n                _persist()')
    source = source.replace('_STATE["notices"].append(notice)', '_STATE["notices"].append(notice)\n        _persist()')
    source = source.replace('_STATE["shares"].append(notice)', '_STATE["shares"].append(notice)\n        _persist()')
    contents = {
        f"{package}/__init__.py": source.encode(),
        f"{info}/METADATA": b"Metadata-Version: 2.1\nName: fixture-draft-workflow\nVersion: 1.0.0\n",
        f"{info}/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        f"{info}/entry_points.txt": (
            f"[heinzel.modules]\nworkflowfixture = {package}\n"
            f"[heinzel.connected_sources]\nworkflowfixture = {package}\n"
        ).encode(),
    }
    rows = [
        (
            name,
            "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(value).digest()).decode().rstrip("="),
            str(len(value)),
        )
        for name, value in contents.items()
    ]
    rows.append((f"{info}/RECORD", "", ""))
    record = io.StringIO(newline="")
    csv.writer(record, lineterminator="\n").writerows(rows)
    contents[f"{info}/RECORD"] = record.getvalue().encode()
    wheel = directory / f"{package}-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, value in contents.items():
            archive.writestr(name, value)
    return wheel


def terminal(command, environment, *, answer="yes\n", expected=0, before_answer=None):
    master, slave = pty.openpty()
    process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave, env=environment)
    os.close(slave)
    output = bytearray()
    deadline = time.monotonic() + 60
    answered = False
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                output.extend(chunk)
                if not answered and b"Approve these exact terms?" in output:
                    if before_answer is not None:
                        before_answer()
                    os.write(master, answer.encode())
                    answered = True
            if process.poll() is not None:
                break
        assert process.wait(timeout=2) == expected, output.decode(errors="replace")
        assert answered, output.decode(errors="replace")
        return output.decode(errors="replace")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        os.close(master)
