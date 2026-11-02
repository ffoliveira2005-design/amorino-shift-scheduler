"""Execute real browser functions in Node without network or database access."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('script,template', [
    ('tests/js/calendar_timezone.cjs', 'templates/index.html'),
    ('tests/ui_workflows.cjs', None),
    ('tests/js/presentation.cjs', None),
    ('tests/js/delivery_workflows.cjs', None),
    ('tests/js/calendar_dialog.cjs', None),
    ('tests/js/week_workflows.cjs', None),
    ('tests/js/schedule_team.cjs', None),
    ('tests/js/schedule_week.cjs', None),
    ('tests/js/theme_controller.cjs', None),
])
def test_browser_workflow_regressions(script, template):
    node = os.environ.get('AMORINO_TEST_NODE') or shutil.which('node')
    if not node:
        pytest.skip('Node is optional for browser-runtime regression checks')
    root = Path(__file__).resolve().parents[1]
    command = [node, str(root / script)]
    if template:
        command.append(str(root / template))
    result = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
