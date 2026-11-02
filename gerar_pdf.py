"""
gerar_pdf.py — Gerador de PDF de escalas Amorino
Estilo replica o template original: dia completo + data, blocos por loja,
labels de turno coloridos, lista compacta de siglas por celula.
"""
import io
from datetime import date, timedelta
from xml.sax.saxutils import escape
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph,
    Spacer, HRFlowable, KeepTogether, PageBreak,
)
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.pdfbase.pdfmetrics import stringWidth
from helpers.time_helpers import shift_bounds, shift_hours, descarga_hours

# ── Paleta Amorino ─────────────────────────────────────────────────────────────
C_BG          = colors.HexColor('#ffffff')
C_TITLE_BAR   = colors.HexColor('#c97a30')   # laranja Amorino para titulo da loja
C_HEADER_BG   = colors.HexColor('#e8b67a')   # bege para cabecalho de dias
C_SHIFT_BG    = colors.HexColor('#f5d9b0')   # cor de fundo para labels de turno
C_SHIFT_FG    = colors.HexColor('#7a4a00')
C_BORDER      = colors.HexColor('#c4862b')
C_BORDER_LT   = colors.HexColor('#e8d5b5')
C_ROW         = colors.white
C_ROW_ALT     = colors.HexColor('#fefaf3')
C_TEXT        = colors.HexColor('#1a0d06')
C_MUTED       = colors.HexColor('#7a5c48')
C_TURNOS      = colors.HexColor('#c97a30')   # avenca em laranja
C_FERIADO_BG  = colors.HexColor('#fff0f0')
C_FERIADO_FG  = colors.HexColor('#a02020')

# ── Mapping de dia abreviado para nome completo + data ────────────────────────
DIA_COMPLETO = {
    'Seg': 'Segunda-feira', 'Ter': 'Terça-feira', 'Qua': 'Quarta-feira',
    'Qui': 'Quinta-feira',  'Sex': 'Sexta-feira', 'Sab': 'Sábado', 'Dom': 'Domingo',
}

def dia_para_label(dia_label, dia_date_iso=None):
    """Recebe 'Seg 11' (e opcionalmente '2026-05-11'), devolve dict com nome + data."""
    parts = dia_label.split(' ')
    abrev = parts[0].rstrip('.')
    full  = DIA_COMPLETO.get(abrev, abrev)
    data  = ''
    if dia_date_iso:
        try:
            d = date.fromisoformat(dia_date_iso)
            data = f"{d.day:02d}/{d.month:02d}"
        except Exception:
            data = parts[1] if len(parts) > 1 else ''
    else:
        data = parts[1] if len(parts) > 1 else ''
    return {'nome': full, 'data': data}

# ── Estilos ───────────────────────────────────────────────────────────────────
def make_styles():
    return {
        'h1': ParagraphStyle('h1', fontSize=20, fontName='Helvetica-Bold',
                            textColor=C_TEXT, alignment=TA_CENTER, spaceAfter=2,
                            leading=24),
        'sub': ParagraphStyle('sub', fontSize=10, fontName='Helvetica-Oblique',
                            textColor=C_BORDER, alignment=TA_CENTER, spaceAfter=12),
        'store_title': ParagraphStyle('st', fontSize=11, fontName='Helvetica-Bold',
                            textColor=colors.white, alignment=TA_LEFT, leading=14),
        'day_name': ParagraphStyle('dn', fontSize=8, fontName='Helvetica-Bold',
                            textColor=C_TEXT, alignment=TA_CENTER, leading=10),
        'day_date': ParagraphStyle('dd', fontSize=7, fontName='Helvetica',
                            textColor=C_MUTED, alignment=TA_CENTER, leading=8),
        'feriado_day': ParagraphStyle('fd', fontSize=8, fontName='Helvetica-Bold',
                            textColor=C_FERIADO_FG, alignment=TA_CENTER, leading=10),
        'shift_label': ParagraphStyle('sl', fontSize=8, fontName='Helvetica-Bold',
                            textColor=C_SHIFT_FG, alignment=TA_LEFT, leading=10),
        'cell_sigla': ParagraphStyle('cs', fontSize=8, fontName='Helvetica-Bold',
                            textColor=C_TEXT, alignment=TA_CENTER, leading=11),
        'cell_avenca': ParagraphStyle('ca', fontSize=8, fontName='Helvetica-Bold',
                            textColor=C_TURNOS, alignment=TA_CENTER, leading=11),
        'cell_note': ParagraphStyle('cn', fontSize=6, fontName='Helvetica-Oblique',
                            textColor=C_MUTED, alignment=TA_CENTER, leading=7),
        'legend': ParagraphStyle('lg', fontSize=8, fontName='Helvetica',
                            textColor=C_TEXT, leading=10),
        'footer': ParagraphStyle('ft', fontSize=7, fontName='Helvetica',
                            textColor=C_MUTED, alignment=TA_CENTER),
    }

# ── Build a single store's table ─────────────────────────────────────────────
def build_store_block(loja_nome, grid, dias, dias_full, feriados, func_info, styles, page_w):
    """
    Build one store's mini-section: title bar + table.
    dias_full: list of dicts [{label, date, feriado, feriado_nome}, ...]
    """
    # ── Title bar (orange) ──
    title_bar = Table(
        [[Paragraph(f'Amorino_{loja_nome}', styles['store_title'])]],
        colWidths=[page_w]
    )
    title_bar.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (-1,-1), C_TITLE_BAR),
        ('TOPPADDING',    (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING',   (0,0), (-1,-1), 10),
    ]))

    # ── Header row (day names) ──
    shift_w = 38 * mm
    day_w   = (page_w - shift_w) / len(dias)
    col_widths = [shift_w] + [day_w] * len(dias)

    header_cells = [Paragraph('Horarios / Dias', styles['shift_label'])]
    for d_full in dias_full:
        dia_info = dia_para_label(d_full['label'], d_full.get('date'))
        is_feriado = d_full.get('feriado', False)
        style_n = styles['feriado_day'] if is_feriado else styles['day_name']
        if is_feriado:
            cell = [
                Paragraph(dia_info['nome'], style_n),
                Paragraph(dia_info['data'], styles['day_date']),
                Paragraph('Feriado', styles['day_date']),
            ]
        else:
            cell = [
                Paragraph(dia_info['nome'], style_n),
                Paragraph(dia_info['data'], styles['day_date']),
            ]
        header_cells.append(cell)

    # ── Body rows ──
    rows = [header_cells]
    has_any = False
    for horario, dias_dict in grid.items():
        if not any(dias_dict.get(d, []) for d in dias):
            continue
        has_any = True
        # Shift label cell
        # Parse "(+X)" suffix for visual grouping
        parts = horario.split(' (')
        main_part = parts[0]
        suffix = '(' + parts[1] if len(parts) > 1 else ''
        if suffix:
            shift_para = [
                Paragraph(main_part, styles['shift_label']),
                Paragraph(suffix, styles['shift_label']),
            ]
        else:
            shift_para = Paragraph(main_part, styles['shift_label'])

        row = [shift_para]
        for dia in dias:
            people = dias_dict.get(dia, [])
            if not people:
                row.append('')
            else:
                paras = []
                for p in people:
                    fi = func_info.get(p['sigla'], {})
                    is_t = fi.get('is_turnos', False)
                    style = styles['cell_avenca'] if is_t else styles['cell_sigla']
                    txt = p['sigla']
                    paras.append(Paragraph(txt, style))
                    if p.get('nota'):
                        paras.append(Paragraph('* ' + p['nota'], styles['cell_note']))
                row.append(paras)
        rows.append(row)

    if not has_any:
        # show empty placeholder with header only
        rows.append([Paragraph('—', styles['day_date'])] +
                    [Paragraph('', styles['day_date']) for _ in dias])

    # Build table
    t = Table(rows, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        # Header bg
        ('BACKGROUND',    (0,0), (-1,0), C_HEADER_BG),
        ('TEXTCOLOR',     (0,0), (-1,0), C_TEXT),
        ('FONTNAME',      (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE',      (0,0), (-1,0), 8),
        ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
        ('ALIGN',         (0,0), (-1,-1), 'CENTER'),
        # Shift label column
        ('BACKGROUND',    (0,1), (0,-1), C_SHIFT_BG),
        ('ALIGN',         (0,0), (0,-1), 'LEFT'),
        ('FONTNAME',      (0,0), (0,-1), 'Helvetica-Bold'),
        ('FONTSIZE',      (0,1), (0,-1), 8),
        # Grid
        ('GRID',          (0,0), (-1,-1), 0.4, C_BORDER_LT),
        ('LINEABOVE',     (0,0), (-1,0),  0.6, C_BORDER),
        ('LINEBELOW',     (0,0), (-1,0),  0.6, C_BORDER),
        ('LINEAFTER',     (0,0), (0,-1),  1.0, C_BORDER),
        # Padding
        ('TOPPADDING',    (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LEFTPADDING',   (0,0), (-1,-1), 4),
        ('RIGHTPADDING',  (0,0), (-1,-1), 4),
    ]

    # Alternating row backgrounds + feriado columns
    for i, row in enumerate(rows[1:], start=1):
        bg = C_ROW_ALT if i % 2 == 0 else C_ROW
        style_cmds.append(('BACKGROUND', (0,i), (-1,i), bg))
        # repaint shift label col since it should always be the shift-bg
        style_cmds.append(('BACKGROUND', (0,i), (0,i), C_SHIFT_BG))
        # Feriado overlay
        for j, d_full in enumerate(dias_full):
            if d_full.get('feriado'):
                style_cmds.append(('BACKGROUND', (j+1,i), (j+1,i), C_FERIADO_BG))

    # feriado header column
    for j, d_full in enumerate(dias_full):
        if d_full.get('feriado'):
            style_cmds.append(('BACKGROUND', (j+1,0), (j+1,0), C_FERIADO_BG))

    t.setStyle(TableStyle(style_cmds))
    return KeepTogether([title_bar, t, Spacer(1, 4 * mm)])

# ── Page-footer helpers (FEATURE 22) ──────────────────────────────────────────
# Stamped on every page of the generated PDF so a stack of printouts is easy to
# sort: "Amorino · 11 a 17 de Maio · página 2 / 4". The footer is intentionally
# small and quiet so it doesn't compete with the schedule itself.
def _draw_footer(canvas, doc, semana_label, kind='completa'):
    canvas.saveState()
    page_w, _ = doc.pagesize
    txt_left  = f'Amorino · {semana_label}'
    txt_right = f'Escala {kind} · pag. {doc.page}'
    canvas.setFont('Helvetica', 7)
    canvas.setFillColor(C_MUTED)
    canvas.drawString(10*mm, 6*mm, txt_left)
    canvas.drawRightString(page_w - 10*mm, 6*mm, txt_right)
    # Thin rule
    canvas.setStrokeColor(C_BORDER_LT)
    canvas.setLineWidth(0.3)
    canvas.line(10*mm, 9*mm, page_w - 10*mm, 9*mm)
    canvas.restoreState()

def _footer_cb(semana_label, kind):
    """Return an onPage callback bound to a semana_label."""
    return lambda c, d: _draw_footer(c, d, semana_label, kind)

# ── Main: full schedule PDF ───────────────────────────────────────────────────
def gerar_escala_pdf(semana_label, lojas_data, func_info, dias_full=None):
    """
    lojas_data: list of {nome, grid, dias, feriados}
    dias_full: list of dicts [{label,date,feriado,feriado_nome}, ...] (week metadata)
    Returns: bytes
    """
    buf = io.BytesIO()
    # FEATURE 22: bottom margin bumped a touch to make room for the footer.
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=10*mm, rightMargin=10*mm,
        topMargin=10*mm,  bottomMargin=14*mm,
        title=f'Escala Amorino — {semana_label}',
        author='Amorino Porto e Gaia',
    )

    styles = make_styles()
    page_w = landscape(A4)[0] - 20*mm
    story  = []

    # Title
    story.append(Paragraph(f'Amorino Porto &amp; Gaia', styles['h1']))
    story.append(Paragraph(f'Escala semanal · {semana_label}', styles['sub']))

    # If dias_full not provided, build a minimal version from each loja's dias
    if not dias_full and lojas_data:
        dias_full = [{'label': d, 'date': '', 'feriado': False, 'feriado_nome': ''}
                     for d in lojas_data[0]['dias']]

    # Each store
    for loja in lojas_data:
        story.append(build_store_block(
            loja['nome'], loja['grid'], loja['dias'],
            dias_full, loja['feriados'], func_info, styles, page_w
        ))

    # Legend
    story.append(Spacer(1, 4 * mm))
    legend = Table([
        [
            Paragraph('<b>Legenda:</b>', styles['legend']),
            _legend_swatch(C_TEXT, 'Contrato fixo'),
            _legend_swatch(C_TURNOS, 'Turnos / Avença'),
            _legend_swatch(C_FERIADO_BG, 'Feriado', text_color=C_FERIADO_FG),
            Paragraph('<i>* nota / troca de loja</i>', styles['legend']),
        ]
    ], colWidths=[22*mm, 38*mm, 38*mm, 60*mm, 35*mm])
    legend.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), C_ROW_ALT),
        ('BOX',        (0,0), (-1,-1), 0.5, C_BORDER_LT),
        ('VALIGN',     (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING',(0,0),(-1,-1),5),
        ('LEFTPADDING',(0,0), (-1,-1), 6),
    ]))
    story.append(legend)

    # NOTE: the old "Gerado automaticamente" line is gone — the per-page footer
    # carries that info now and shows on every page (not only the last).

    kind = 'por loja' if len(lojas_data) == 1 else 'completa'
    cb = _footer_cb(semana_label, kind)
    doc.build(story, onFirstPage=cb, onLaterPages=cb)
    buf.seek(0)
    return buf.read()


def _legend_swatch(color, label, text_color=None):
    """Returns a Paragraph with a coloured swatch followed by label."""
    tc = text_color or colors.black
    hex_color = color.hexval()[2:]
    label_color = tc.hexval()[2:]
    return Paragraph(
        f'<font color="#{hex_color}">█</font> '
        f'<font color="#{label_color}">{label}</font>',
        ParagraphStyle('sw', fontSize=8, fontName='Helvetica', textColor=colors.HexColor('#1a0d06'))
    )


# ── Individual employee PDF ───────────────────────────────────────────────────
def gerar_escala_individual_pdf(semana_label, sigla, nome, tipo_contrato, turnos_por_loja, dias_full=None):
    """Personal schedule PDF — one A4 page, neat, ready to email/print."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm,
        topMargin=18*mm,  bottomMargin=22*mm,  # FEATURE 22: room for footer
        title=f'A minha escala — {sigla}',
    )
    styles = make_styles()
    story = []

    # Big title
    story.append(Paragraph('Amorino Porto &amp; Gaia', styles['h1']))
    story.append(Paragraph(f'Escala individual · {escape(semana_label)}', styles['sub']))

    # Employee card
    tipo_label = 'Turnos' if tipo_contrato == 'turnos' else 'Contrato fixo'
    card_color = C_TURNOS if tipo_contrato == 'turnos' else colors.HexColor('#2c1810')
    sigla_width = 36 * mm
    sigla_size = min(22, (sigla_width - 24) / max(stringWidth(sigla, 'Helvetica-Bold', 1), 1))
    card = Table([[
        Paragraph(f'<font size={sigla_size:.2f} color="white"><b>{escape(sigla)}</b></font>',
                  ParagraphStyle('s', fontSize=sigla_size, leading=sigla_size * 1.15, textColor=colors.white)),
        Paragraph(
            f'<font size=14 color="white"><b>{escape(nome)}</b></font><br/>'
            f'<font size=9 color="#e8b67a">{tipo_label}</font>',
            ParagraphStyle('n', leading=18, textColor=colors.white)
        ),
    ]], colWidths=[sigla_width, None])
    card.setStyle(TableStyle([
        ('BACKGROUND',   (0,0), (-1,-1), card_color),
        ('VALIGN',       (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING',   (0,0), (-1,-1), 10),
        ('BOTTOMPADDING',(0,0), (-1,-1), 10),
        ('LEFTPADDING',  (0,0), (-1,-1), 14),
    ]))
    story.append(card)
    story.append(Spacer(1, 6*mm))

    if not turnos_por_loja:
        story.append(Paragraph(
            'Nenhuma atribuição esta semana.',
            ParagraphStyle('e', fontSize=11, textColor=C_MUTED, alignment=TA_CENTER)
        ))
    else:
        from collections import defaultdict
        por_dia = defaultdict(list)
        for t in turnos_por_loja:
            por_dia[t['dia']].append(t)

        rows = [[
            Paragraph('<b>Dia</b>',     styles['day_name']),
            Paragraph('<b>Horario</b>', styles['day_name']),
            Paragraph('<b>Loja</b>',    styles['day_name']),
            Paragraph('<b>Notas</b>',   styles['day_name']),
        ]]

        # Order days according to dias_full if available
        if dias_full:
            order = {d['label']: i for i, d in enumerate(dias_full)}
            sorted_days = sorted(por_dia.keys(), key=lambda x: order.get(x, 99))
        else:
            sorted_days = list(por_dia.keys())

        feriado_map = {d['label']: d.get('feriado_nome', '') for d in (dias_full or []) if d.get('feriado')}

        for dia in sorted_days:
            turnos = sorted(por_dia[dia], key=lambda t: (
                shift_bounds(t['horario'])[0] or 0, t['loja_nome']))
            for j, t in enumerate(turnos):
                if j == 0:
                    dia_info = dia_para_label(dia, next((d['date'] for d in (dias_full or []) if d['label']==dia), None))
                    dia_text = f"<b>{dia_info['nome']}</b><br/><font size=8 color='#7a5c48'>{dia_info['data']}</font>"
                    if dia in feriado_map:
                        dia_text += f"<br/><font size=7 color='#a02020'>[Feriado] {escape(feriado_map[dia])}</font>"
                    dia_cell = Paragraph(dia_text, ParagraphStyle('d', fontSize=10, leading=12, textColor=C_TEXT))
                else:
                    dia_cell = ''
                rows.append([
                    dia_cell,
                    Paragraph(f"<b>{escape(t['horario'])}</b>", ParagraphStyle('h', fontSize=11, textColor=C_TEXT, leading=14)),
                    Paragraph(escape(t['loja_nome']), styles['legend']),
                    Paragraph(escape(t.get('nota', '—') or '—'), styles['legend']),
                ])

        sched = Table(rows, colWidths=[38*mm, 35*mm, 50*mm, 50*mm])
        sched_styles = [
            ('BACKGROUND', (0,0), (-1,0), C_TITLE_BAR),
            ('TEXTCOLOR',  (0,0), (-1,0), colors.white),
            ('FONTNAME',   (0,0), (-1,0), 'Helvetica-Bold'),
            ('GRID',       (0,0), (-1,-1), 0.4, C_BORDER_LT),
            ('VALIGN',     (0,0), (-1,-1), 'MIDDLE'),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('BOTTOMPADDING',(0,0),(-1,-1),6),
            ('LEFTPADDING',(0,0), (-1,-1), 8),
            ('RIGHTPADDING',(0,0),(-1,-1), 8),
        ]
        # alternate rows
        for i in range(1, len(rows)):
            if i % 2 == 0:
                sched_styles.append(('BACKGROUND', (0,i), (-1,i), C_ROW_ALT))
        # A day can occupy several rows (shift + delivery); highlight every
        # row belonging to the holiday rather than indexing days as rows.
        row_idx = 1
        for dia in sorted_days:
            row_end = row_idx + len(por_dia[dia]) - 1
            if dia in feriado_map:
                sched_styles.append(('BACKGROUND', (0,row_idx), (-1,row_end), C_FERIADO_BG))
            row_idx = row_end + 1
        sched.setStyle(TableStyle(sched_styles))
        story.append(sched)

        # Summary
        story.append(Spacer(1, 6*mm))
        delivery_count = sum(t.get('tipo') == 'descarga' for t in turnos_por_loja)
        shift_count = len(turnos_por_loja) - delivery_count
        total_hours = sum(
            t.get('horas', (descarga_hours if t.get('tipo') == 'descarga' else shift_hours)(t['horario']))
            for t in turnos_por_loja)
        summary = f'{shift_count} turno{"s" if shift_count != 1 else ""}'
        if delivery_count:
            summary += f' · {delivery_count} descarga{"s" if delivery_count != 1 else ""}'
        hours_label = f'{total_hours:.1f}'.replace('.', ',')
        story.append(Paragraph(
            f'{summary} · Total: <b>{hours_label} h</b> esta semana',
            ParagraphStyle('ts', fontSize=10, textColor=C_TEXT, alignment=TA_CENTER)
        ))

    story.append(Spacer(1, 10*mm))
    story.append(HRFlowable(width='100%', thickness=0.4, color=C_BORDER_LT))

    # FEATURE 22: per-page footer (visible even on multi-page exports)
    kind = f'individual ({sigla})'
    cb = _footer_cb(semana_label, kind)
    doc.build(story, onFirstPage=cb, onLaterPages=cb)
    buf.seek(0)
    return buf.read()
