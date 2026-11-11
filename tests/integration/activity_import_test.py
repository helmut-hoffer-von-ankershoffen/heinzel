"""A cold durable activity read has no dependency on the model runtime."""

import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.integration


def test_cold_activity_projects_real_private_events_without_importing_ai(tmp_path):
    code = textwrap.dedent("""
        import importlib.abc
        import sys
        from pathlib import Path

        blocked = ("pydantic_ai", "heinzel.modules.heinzel._interactive_turn", "heinzel.modules.heinzel._engine")
        class NoModelRuntime(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
                    raise AssertionError("read-only activity imported model runtime: " + fullname)
        sys.meta_path.insert(0, NoModelRuntime())

        from heinzel import CurrentUser
        from heinzel.modules.heinzel._session_crud import SessionCrud
        from heinzel.modules.heinzel._session_models import SessionCosts
        from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
        from heinzel.modules.heinzel._turn_activity import interactive_turn_id
        from heinzel.modules.heinzel._user_chat import UserChatService
        from heinzel.modules.system.runner._snapshot import RunnerSnapshotService

        current = CurrentUser(Path(sys.argv[1]) / "identity")
        with current.local("cli"):
            crud = SessionCrud(
                PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
            )
            session = crud.new_chat("owner").session_id
            turn = interactive_turn_id("request")
            with crud.repository.control_writer(session) as writer:
                writer.append("interactive.started", turn=turn, data={"message_id": "request"})
                writer.append("interactive.tool", turn=turn,
                    data={"tool": "text.word_count", "status": "completed", "arguments": "PRIVATE_ARGUMENT"})
            service = UserChatService(crud, current, RunnerSnapshotService())
            result = service.activity(session)
            assert [(item.tool, item.status) for item in result.items] == [("text.word_count", "completed")]
            assert "PRIVATE_ARGUMENT" not in result.model_dump_json()
        assert not any(module == name or module.startswith(name + ".") for module in sys.modules for name in blocked)
    """)
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, timeout=10, check=False
    )
    assert result.returncode == 0, result.stderr
