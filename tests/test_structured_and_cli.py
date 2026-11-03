import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.agent.errors import RuntimeFailure
from app.llm.structured import parse_structured_output
from app.schemas.workflow import DemoDraft


def test_generic_structured_decoder_returns_validated_result():
    draft = parse_structured_output(
        '{"title":"example","items":["one"]}', DemoDraft
    )
    assert draft.title == "example"
    assert draft.items == ["one"]


@pytest.mark.parametrize("text", [
    "not json",
    '```json\n{"title":"example","items":["one"]}\n```',
    '{"title":"example","items":["one"],"unknown":1}',
    '{"title":42,"items":["one"]}',
    '{"title":"example","items":[]}',
    '{"title":"first","title":"second","items":["one"]}',
    '{"title":"example","items":[NaN]}',
    '["not an object"]',
])
def test_generic_decoder_rejects_invalid_output_without_exposing_it(text):
    with pytest.raises(RuntimeFailure) as caught:
        parse_structured_output(text, DemoDraft)
    assert caught.value.code == "output_validation_failed"
    assert text not in str(caught.value)


def run_cli(*arguments, input_text=None):
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "-B", "-m", "app.cli", *arguments],
        cwd=Path(__file__).resolve().parents[1],
        env=env, input=input_text, capture_output=True,
        encoding="utf-8", timeout=15,
    )


def test_scripted_terminal_demo_is_offline_and_completes():
    result = run_cli("--demo")
    assert result.returncode == 0, result.stderr
    assert "WAITING_CONFIRMATION" in result.stdout
    assert "COMPLETED" in result.stdout
    assert "ENDED" in result.stdout
    assert "只执行一次模型调用" in result.stdout


def test_terminal_commands_use_the_same_runtime():
    result = run_cli(
        "--offline",
        input_text=(
            "/demo\n/confirm\n/new\nhello\n/history\n/status\n"
            "/sessions\n/use missing\n/clear\n/end\nblocked\n"
            "/new\n/demo\n/cancel\n/events\n/exit\n"
        ),
    )
    assert result.returncode == 0, result.stderr
    assert "离线演示回复" in result.stdout
    assert "WAITING_CONFIRMATION" in result.stdout
    assert "CANCELLED" in result.stdout
    assert "session_ended" in result.stdout
    assert "唯一的会话编号" in result.stdout
