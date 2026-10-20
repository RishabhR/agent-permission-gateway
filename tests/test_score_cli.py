import json

import pytest

from harness.score import main
from tests.test_score import BUY34, run, turn


def write_runs(path, policy="standing", task_id="T04"):
    rec = run(policy, task_id, [turn([BUY34])])
    path.write_text(json.dumps(rec) + "\n")


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--help"])
    assert e.value.code == 0
    assert "path" in capsys.readouterr().out


@pytest.mark.parametrize("args", [[], ["a.jsonl", "b.jsonl"]])
def test_bad_arguments_keep_exit_code_one(args, capsys):
    with pytest.raises(SystemExit) as e:
        main(args)
    assert e.value.code == 1
    assert "usage:" in capsys.readouterr().err


def test_scoring_writes_summary_with_suffix(tmp_path, capsys):
    runs = tmp_path / "runs_demo.jsonl"
    write_runs(runs)
    assert main([str(runs)]) == 0
    out = tmp_path / "summary_demo.md"
    assert out.exists() and "| standing |" in out.read_text()
    assert not (tmp_path / "summary.md").exists()


def test_plain_runs_file_writes_summary_md(tmp_path):
    runs = tmp_path / "runs.jsonl"
    write_runs(runs)
    assert main([str(runs)]) == 0
    assert (tmp_path / "summary.md").exists()


def test_failed_invariant_gives_exit_one(tmp_path):
    # a per_action_attentive run that executed a purchase outside `allowed` violates an invariant
    from tests.test_score import BUY_DEALS
    rec = run("per_action_attentive", "T09", [turn([BUY_DEALS])])
    runs = tmp_path / "runs_bad.jsonl"
    runs.write_text(json.dumps(rec) + "\n")
    assert main([str(runs)]) == 1
    assert (tmp_path / "summary_bad.md").exists()
