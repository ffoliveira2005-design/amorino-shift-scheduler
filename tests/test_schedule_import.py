"""Schedule parser regressions; synthetic tables avoid storing staff PDFs."""
from datetime import date
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('schedule_parser_under_test',
                                              PROJECT_ROOT / 'scripts/import_history.py')
parser = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parser)


def _row(label, *cells):
    return [label, *cells, *([''] * (7 - len(cells)))]


def _header():
    return _row('Hórarios /Dias', *[f'Dia\n{day:02d}/10' for day in range(5, 12)])


def _parse(monkeypatch, rows, text=''):
    class FakePdf:
        pages = [SimpleNamespace(extract_tables=lambda: [rows], extract_text=lambda: text)]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setitem(sys.modules, 'pdfplumber', SimpleNamespace(open=lambda *_args: FakePdf()))
    return parser.parse_pdf(Path('Amorino_Horarios_2026.pdf'))


def test_multiline_store_title_does_not_assign_mbs_work_to_gaia(monkeypatch):
    parsed = _parse(monkeypatch, [
        _row('Amorino_Gaia'), _header(), _row('08:00_16:30', 'AA'),
        _row('9h30 - Visita\nHACCP e HST\nAmorino_Mercado Bom Sucesso'),
        _header(), _row('10:00_14:30', 'BB'),
    ])
    assert parsed['turnos'] == [('gaia', '08:00-16:30', 'Seg 5', 'AA'),
                                 ('mbs', '10:00-14:30', 'Seg 5', 'BB')]
    assert parsed['week_start'] == date(2026, 10, 5)
    assert parsed['warnings'] == []


def test_slash_clock_band_and_cell_override_keep_actual_work_times(monkeypatch):
    parsed = _parse(monkeypatch, [
        _row('Amorino_Carmelitas'), _header(),
        _row('8:00/17:30\n(+1)', 'AA 15-17', '', '', '', '', '', 'BB'),
    ])
    assert parsed['turnos'] == [('carmelitas', '15:00-17:00', 'Seg 5', 'AA'),
                                 ('carmelitas', '08:00-17:30 (+1)', 'Dom 11', 'BB')]
    assert parsed['warnings'] == []


def test_overrides_apply_only_to_the_preceding_employee():
    assignments, warnings = parser._cell_assignments('AA 15:30 - 17:00 BB CC 16-18', '08:00-17:30 (+1)')
    assert assignments == [('AA', '15:30-17:00'), ('BB', '08:00-17:30 (+1)'), ('CC', '16:00-18:00')]
    assert warnings == []


@pytest.mark.parametrize('band,expected', [
    ('8:00_16:30', '08:00-16:30'),
    ('8:00/17:30\n(+1)', '08:00-17:30 (+1)'),
    ('Provas Praticas', ''),
])
def test_time_normalization_preserves_paid_modifier_and_excludes_non_work(band, expected):
    assert parser.normalise_horario(band) == expected


def test_non_work_rows_clear_inherited_band_context(monkeypatch):
    parsed = _parse(monkeypatch, [
        _row('Amorino_Carmelitas'), _header(), _row('08:00_16:30', 'AA'),
        _row('Provas Praticas', 'BB'), _row('', 'CC'),
        _row('16:00_00:30', 'DD'), _row('', 'EE'),
        _row('* Troca de Loja', 'FF'), _row('', 'GG'),
    ])
    assert [r[3] for r in parsed['turnos']] == ['AA', 'DD', 'EE']


def test_unrecognized_store_never_inherits_previous_store(monkeypatch):
    parsed = _parse(monkeypatch, [
        _row('Amorino_Gaia'), _header(), _row('08:00_16:30', 'AA'),
        _row('Amorino_Unknown'), _header(), _row('10:00_14:30', 'BB'),
    ])
    assert parsed['turnos'] == [('gaia', '08:00-16:30', 'Seg 5', 'AA')]
    assert 'unknown loja header' in parsed['warnings'][0]


def test_delivery_footnotes_preserve_start_and_multiple_workers_without_guessing_end(monkeypatch):
    parsed = _parse(monkeypatch, [_row('Amorino_Carmelitas'), _header()], text='\n'.join([
        'Turnos TSM - Terça-feira - 9:00 - NorteShopping - Gelados - 1 palete - AA',
        'Troca de Turno TSM - Quarta-feira - 08:00 - Gaia - Gelados - 3 paletes / Secos - 1 palete - BB, CC, DD',
    ]))
    assert [(r['loja'], r['dia'], r['funcionario'], r['inicio']) for r in parsed['descargas']] == [
        ('norte', 'Ter 6', 'AA', '09:00'), ('gaia', 'Qua 7', 'BB', '08:00'),
        ('gaia', 'Qua 7', 'CC', '08:00'), ('gaia', 'Qua 7', 'DD', '08:00'),
    ]
    assert all('fim' not in r and 'horario' not in r for r in parsed['descargas'])
    assert parsed['warnings'] == []


def test_invalid_delivery_note_warns_instead_of_inventing_assignment():
    records, warnings = parser.parse_descargas('TSM - Terça-feira - sem hora - Gaia - AA', [])
    assert records == []
    assert warnings == ['  unparsed descarga footnote: TSM - Terça-feira - sem hora - Gaia - AA']
