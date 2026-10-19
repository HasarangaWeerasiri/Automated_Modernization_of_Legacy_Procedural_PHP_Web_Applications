"""Component 2's command for the group UI (tools/ui_cli.py): it only runs discovery apps, and it is never reached from
the pipeline (src/, config/, tools/harness/) or the evaluation (tools/eval/). Its full run (harness, Stages 1-5, next
build, layers) is the pipeline as it is: on HMS its stage outputs equal the Phase 4 run's except for `outputDir`
(checked by hand, 19 Oct 2026); it is not repeated here (a build takes minutes).
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_an_app_that_is_not_a_discovery_app_is_refused(tmp_path):
    run = subprocess.run([sys.executable, str(ROOT / "tools" / "ui_cli.py"), "--app", "crud-php-mysqli",
                          "--out", str(tmp_path)], capture_output=True, text=True, encoding="utf-8")
    assert run.returncode == 2 and "not a discovery app" in run.stdout
    assert not (tmp_path / "summary.json").exists()


def test_without_arguments_it_prints_its_usage():
    run = subprocess.run([sys.executable, str(ROOT / "tools" / "ui_cli.py")], capture_output=True, text=True,
                         encoding="utf-8")
    assert run.returncode == 2 and "ui_cli.py --app" in run.stderr


def test_the_pipeline_and_the_evaluation_never_reach_it():
    for folder in ("src", "config", "tools/harness", "tools/eval"):
        for file in (ROOT / folder).rglob("*.py"):
            assert "ui_cli" not in file.read_text(encoding="utf-8"), file


def test_progress_comes_from_a_wrapper_not_an_edit_of_the_pipeline():
    text = (ROOT / "tools" / "ui_cli.py").read_text(encoding="utf-8")
    assert "run_apps.run_stages = traced" in text and "run_apps.run_stages = original" in text
