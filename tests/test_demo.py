"""Regression checks for the isolated, fictional CEO demonstration launcher."""

import importlib.util
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def demo(tmp_path):
    """Each test owns its launcher location, with no live or shared demo data."""
    project = tmp_path / "fictional-demo-project"
    (project / "scripts").mkdir(parents=True)
    for name in ("app.py", "config.py", "extensions.py", "models.py", "seed_data.py", "gerar_pdf.py"):
        shutil.copy2(ROOT / name, project / name)
    shutil.copy2(ROOT / "scripts" / "demo.py", project / "scripts" / "demo.py")
    for name in ("helpers", "templates", "static"):
        shutil.copytree(ROOT / name, project / name, ignore=shutil.ignore_patterns("__pycache__"))
    spec = importlib.util.spec_from_file_location("isolated_demo_launcher", project / "scripts" / "demo.py")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    return launcher


def test_demo_refuses_an_alternative_database_path(demo, monkeypatch, tmp_path):
    """A coding/configuration mistake must not turn reset into arbitrary deletion."""
    target = tmp_path / "important.db"
    target.write_text("preserve this file", encoding="utf-8")
    monkeypatch.setattr(demo, "DEMO_DB", target)
    with pytest.raises(RuntimeError, match="outside"):
        demo._reset_disposable_database()
    assert target.read_text(encoding="utf-8") == "preserve this file"


def test_demo_lock_blocks_a_second_launcher(demo):
    """Rehearsal actions must not be reset by another launch while serving."""
    with demo._demo_lock():
        with pytest.raises(RuntimeError, match="already running"):
            with demo._demo_lock():
                pytest.fail("The second launcher acquired an existing lock")


def test_prepare_only_is_fictional_repeatable_and_ignores_caller_database(demo, tmp_path):
    """Real imports, logins and exports run only on the dedicated throwaway DB."""
    caller_database = tmp_path / "do-not-open.db"
    marker = b"This is deliberately not SQLite. The demo must ignore this path."
    caller_database.write_bytes(marker)
    env = dict(os.environ, AMORINO_DB_PATH=str(caller_database))
    command = [sys.executable, str(demo.ROOT / "scripts" / "demo.py"), "--prepare-only"]

    def prepare():
        result = subprocess.run(command, cwd=demo.ROOT, env=env, capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "No server was started" in result.stdout
        assert caller_database.read_bytes() == marker
        summary = json.loads((demo.DEMO_DIR / "summary.json").read_text(encoding="utf-8"))
        assert summary["staff"] == 46
        assert summary["current_shifts"] == 175
        assert summary["draft_shifts"] == 172
        assert summary["draft_missing_places"] == 3
        assert summary["draft_responses"] == 41
        assert summary["current_published_version"] == 1
        assert summary["next_published_version"] == 1
        assert summary["route_checks"] >= 22
        assert summary['donation_checks'] == 11
        assert summary['donation']['donor'] == 'DCA04'
        assert summary['donation']['applicants'] == ['DCA03', 'DCA05']
        assert summary['donation']['time'] == '10:00-18:30'
        return summary

    first = prepare()
    with closing(sqlite3.connect(demo.DEMO_DB)) as conn, conn:
        conn.execute("INSERT INTO funcionarios (sigla,nome,loja_gerida,access_version,enrollment_hash,enrollment_kind) VALUES ('STALE','Discard on reset','',0,'','')")
    second = prepare()
    assert second["current_week"] == first["current_week"]
    with closing(sqlite3.connect(demo.DEMO_DB)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM funcionarios").fetchone()[0] == 46
        assert conn.execute("SELECT COUNT(*) FROM funcionarios WHERE sigla='STALE'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM funcionarios WHERE sigla NOT LIKE 'D%'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM trocas WHERE estado='pendente_supervisor'").fetchone()[0] == 1
        # Startup exercised publication, both applications and final approval,
        # then restored a clean initial scenario for the actual presentation.
        assert conn.execute("SELECT COUNT(*) FROM trocas WHERE tipo='aberto_doacao'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM trocas_candidaturas").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM turnos WHERE nota='Ensaio de alteração por publicar.'").fetchone()[0] == 0
        assert conn.execute("SELECT codigo FROM semanas WHERE ativa=1").fetchone()[0] == second["next_week"]["codigo"]
        assert conn.execute("SELECT COUNT(*) FROM schedule_releases").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM published_turnos").fetchone()[0] == 347
        assert conn.execute("SELECT COUNT(*) FROM published_descargas").fetchone()[0] == 6
        assert conn.execute("SELECT actor FROM schedule_releases GROUP BY actor").fetchall() == [('DEMO',)]
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE name='schedule_publication_v1'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE name='donation_admin_review_v1'").fetchone()[0] == 1
        for applicant in second['donation']['applicants']:
            assert conn.execute("SELECT horas_contrato FROM funcionarios WHERE sigla=?", (applicant,)).fetchone()[0] == 40
            assert conn.execute("SELECT COUNT(*) FROM turnos WHERE funcionario=? AND semana=? AND dia=?",
                                (applicant, second['next_week']['codigo'], second['donation']['day'])).fetchone()[0] == 0
            assert conn.execute("SELECT tipo FROM disponibilidades WHERE funcionario=? AND semana=? AND dia=?",
                                (applicant, second['next_week']['codigo'], second['donation']['day'])).fetchone()[0] == 'disponivel'

    # Restarting app.py on the same isolated demo keeps an ordinary staged
    # edit unpublished. The demo reset must not lose the migration marker.
    with closing(sqlite3.connect(demo.DEMO_DB)) as conn, conn:
        conn.execute("UPDATE turnos SET nota='Unpublished after restart' WHERE id=(SELECT MIN(id) FROM turnos)")
    restart_env = dict(env, AMORINO_DB_PATH=str(demo.DEMO_DB),
                       AMORINO_SUPERVISOR_PIN=demo.SUPERVISOR_PIN,
                       AMORINO_SECRET_KEY='fictional-demo-restart-test-secret-key')
    restarted = subprocess.run([sys.executable, '-c', 'import app'], cwd=demo.ROOT,
                               env=restart_env, capture_output=True, text=True, timeout=90)
    assert restarted.returncode == 0, restarted.stdout + restarted.stderr
    with closing(sqlite3.connect(demo.DEMO_DB)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM schedule_releases").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM published_turnos WHERE nota='Unpublished after restart'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM turnos WHERE nota='Unpublished after restart'").fetchone()[0] == 1
    assert caller_database.read_bytes() == marker
