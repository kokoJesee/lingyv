import json
from pathlib import Path

import pytest

from app.evaluation import __main__ as cli
from app.config.settings import PROJECT_ROOT


def local_setup(monkeypatch, tmp_path):
    config = tmp_path / "local.paths.json"
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cli, "DEFAULT_OUTPUT", tmp_path / "reports")
    monkeypatch.setattr(cli, "LOCAL_PATHS_FILE", config)
    return config


def test_report_default_uses_checkout_without_personal_file(monkeypatch, tmp_path):
    config = local_setup(monkeypatch, tmp_path)
    assert cli.default_output() == tmp_path / "reports"
    assert not config.exists()


@pytest.mark.parametrize("value", ["", "reports-private", "输出目录"])
def test_relative_personal_output_is_based_on_checkout(monkeypatch, tmp_path, value):
    config = local_setup(monkeypatch, tmp_path)
    config.write_text(json.dumps({"evaluation_output_dir": value}), encoding="utf-8")
    assert cli.default_output() == tmp_path / (value or "reports")


def test_absolute_personal_output_preserved(monkeypatch, tmp_path):
    config = local_setup(monkeypatch, tmp_path)
    output = tmp_path.parent / "personal notes" / "reports"
    config.write_text(json.dumps({"evaluation_output_dir": str(output)}), encoding="utf-8")
    assert cli.default_output() == output


@pytest.mark.parametrize("content", ["{bad", "[]", '{"evaluation_output_dir":42}', " " * 16385])
def test_bad_local_paths_fail_without_export_or_case_execution(monkeypatch, tmp_path, content, capsys):
    config = local_setup(monkeypatch, tmp_path)
    config.write_text(content, encoding="utf-8")
    def forbidden(*args, **kwargs):
        raise AssertionError("must reject configuration before cases")
    monkeypatch.setattr(cli, "run_suite", forbidden)
    assert cli.main(["--suite", str(PROJECT_ROOT / "evaluation/cases/runtime_v1.json")]) == 2
    assert "本地路径配置无效" in capsys.readouterr().out
    assert not (tmp_path / "reports").exists()


def test_explicit_output_bypasses_invalid_personal_config(monkeypatch, tmp_path):
    config = local_setup(monkeypatch, tmp_path)
    config.write_text("{bad", encoding="utf-8")
    output = tmp_path / "cli-output"
    assert cli.main(["--suite", str(PROJECT_ROOT / "evaluation/cases/runtime_v1.json"),
                     "--output", str(output)]) == 0
    assert len(list(output.glob("*/report.md"))) == 1
