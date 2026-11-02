"""Downloaded evidence must identify its week and authorised version."""
import csv
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_basics import app, client, supervisor_client


def test_csv_identifies_published_snapshot_and_draft_inside_file(app, supervisor_client):
    with app.app.app_context():
        week = app.get_semana_ativa()
        code = week.codigo
    published = supervisor_client.get('/supervisor/exportar-horas-csv', query_string={'semana': code})
    assert published.status_code == 200
    rows = list(csv.DictReader(io.StringIO(published.data.decode('utf-8-sig')), delimiter=';'))
    assert rows and all(row['Semana'] == code for row in rows)
    assert all('Publicada v1' in row['Estado da escala'] for row in rows)
    assert published.headers['X-Amorino-Schedule-Version'] == 'v1'
    assert rows[-1]['Nome'] == 'TOTAL'
    total = sum(float(row['Total horas'].replace(',', '.')) for row in rows[:-1])
    assert abs(total - float(rows[-1]['Total horas'].replace(',', '.'))) < .01
    draft = supervisor_client.get('/supervisor/exportar-horas-csv', query_string={'semana': code, 'rascunho': '1'})
    draft_rows = list(csv.DictReader(io.StringIO(draft.data.decode('utf-8-sig')), delimiter=';'))
    assert all('RASCUNHO' in row['Estado da escala'] for row in draft_rows)
    assert draft.headers['X-Amorino-Schedule-Version'] == 'RASCUNHO'
