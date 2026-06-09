"""
DigitalTwinEngine — Session HTML report generator.
Sprint 2a: called at session end (Ctrl-C) to produce a standalone HTML report.

Output: {out_dir}/session_YYYYMMDD_HHMM.html
        Self-contained (Plotly + DataTables from CDN).

Sections:
  1. Stats header
  2. Plotly time-series charts  (throughput / CU+CRC+link_score / stations+SNR)
  3. Raw Data — searchable, sortable, CSV-exportable DataTables
                radio_polls tab  (65 fields) | client_polls tab
"""
import json
import math
import os
from datetime import datetime
from typing import Optional

from plotly.subplots import make_subplots
import plotly.graph_objects as go
import plotly.io as pio


def _safe(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def generate_session_html(session_id: int, db_path: str, out_dir: str,
                          eod_url: Optional[str] = None) -> str:
    """
    Build a full session report HTML from digital_twin.db.
    eod_url: optional link back to the EOD session log (shown in header + footer).
    Returns absolute path to the written file.
    """
    from storage import SQLiteStore

    store = SQLiteStore(db_path)
    try:
        sess    = store.get_session(session_id)
        polls   = store.iter_radio_polls(session_id)
        clients = store.iter_client_polls(session_id)
        snr_ts  = store.avg_snr_by_ts_radio(session_id)
    finally:
        store.close()

    if not polls:
        raise ValueError(f"No radio_polls found for session_id={session_id}")

    # ── Organise by radio ─────────────────────────────────────────────────────
    radios_seen: list[str] = []
    by_radio: dict[str, list[dict]] = {}
    for row in polls:
        r = row['radio']
        if r not in by_radio:
            radios_seen.append(r)
            by_radio[r] = []
        by_radio[r].append(row)

    snr_by_radio: dict[str, tuple[list, list]] = {}
    for row in snr_ts:
        r = row['radio']
        if r not in snr_by_radio:
            snr_by_radio[r] = ([], [])
        snr_by_radio[r][0].append(row['ts'])
        snr_by_radio[r][1].append(row['avg_snr'])

    # ── Plotly 3-row chart ────────────────────────────────────────────────────
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=[
            'Throughput (Mbps)',
            'Channel Utilization % · CRC Error % · Link Score (0–100)',
            'Station Count · Avg Client SNR (dB)',
        ],
    )

    colours = {'wifi0': ('#2563eb', '#93c5fd'), 'wifi1': ('#16a34a', '#86efac')}

    for radio in radios_seen:
        rows_r  = by_radio[radio]
        ts_vals = [r['ts'] for r in rows_r]
        c_main, c_light = colours.get(radio, ('#7c3aed', '#c4b5fd'))
        band = rows_r[0].get('band', '')
        name = f'{radio} ({band})'

        def _series(field, _rows=rows_r):
            return [r.get(field) for r in _rows]

        fig.add_trace(go.Scatter(
            x=ts_vals, y=_series('tx_throughput_mbps'),
            name=f'{name} Tx Mbps', legendgroup=radio,
            line=dict(color=c_main, width=2),
            hovertemplate='%{y:.1f} Mbps<extra>Tx</extra>',
        ), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=ts_vals, y=_series('rx_throughput_mbps'),
            name=f'{name} Rx Mbps', legendgroup=radio,
            line=dict(color=c_light, width=1.5, dash='dot'),
            hovertemplate='%{y:.1f} Mbps<extra>Rx</extra>',
        ), row=1, col=1)

        fig.add_trace(go.Scatter(
            x=ts_vals, y=_series('total_cu_pct'),
            name=f'{name} Total CU%', legendgroup=radio,
            line=dict(color=c_main, width=2),
            hovertemplate='%{y:.1f}%<extra>Total CU</extra>',
        ), row=2, col=1)
        fig.add_trace(go.Scatter(
            x=ts_vals, y=_series('crc_error_pct'),
            name=f'{name} CRC%', legendgroup=radio,
            line=dict(color='#dc2626', width=1.5),
            hovertemplate='%{y:.2f}%<extra>CRC Error</extra>',
        ), row=2, col=1)
        fig.add_trace(go.Scatter(
            x=ts_vals, y=_series('link_score'),
            name=f'{name} Link Score', legendgroup=radio,
            line=dict(color=c_light, width=1.5, dash='dash'),
            hovertemplate='%{y:.0f}/100<extra>Link Score</extra>',
        ), row=2, col=1)

        fig.add_trace(go.Bar(
            x=ts_vals, y=_series('station_count'),
            name=f'{name} Stations', legendgroup=radio,
            marker_color=c_main, opacity=0.6,
            hovertemplate='%{y} clients<extra>Stations</extra>',
        ), row=3, col=1)
        if radio in snr_by_radio:
            snr_x, snr_y = snr_by_radio[radio]
            fig.add_trace(go.Scatter(
                x=snr_x, y=snr_y,
                name=f'{name} Avg SNR', legendgroup=radio,
                line=dict(color=c_light, width=2),
                yaxis='y6',
                hovertemplate='%{y:.1f} dB<extra>Avg SNR</extra>',
            ), row=3, col=1)

    # ── Metadata ──────────────────────────────────────────────────────────────
    ap_name  = (sess or {}).get('ap_name', 'Unknown AP')
    ap_model = (sess or {}).get('ap_model', '')
    start_ts = (sess or {}).get('start_ts', '')
    end_ts   = (sess or {}).get('end_ts', '')
    n_polls  = len(by_radio.get(radios_seen[0], [])) if radios_seen else 0

    try:
        start_dt = datetime.fromisoformat(start_ts)
        end_dt   = datetime.fromisoformat(end_ts) if end_ts else None
        duration = f'{int((end_dt - start_dt).total_seconds() / 60)} min' if end_dt else '—'
        ts_label = start_dt.strftime('%Y-%m-%d %H:%M UTC')
    except Exception:
        ts_label = start_ts
        duration = '—'

    fig.update_layout(
        title=dict(
            text=(f'<b>DigitalTwinEngine Session Report</b><br>'
                  f'<span style="font-size:13px;color:#666">'
                  f'{ap_name} ({ap_model}) · {ts_label} · {duration} · {n_polls} polls</span>'),
            font=dict(size=18), x=0.02,
        ),
        height=700,
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
        hovermode='x unified',
        paper_bgcolor='#ffffff',
        plot_bgcolor='#fafafa',
        font=dict(family='Inter, Helvetica Neue, Arial, sans-serif', size=12),
        margin=dict(t=120, b=40, l=60, r=20),
    )
    fig.update_xaxes(showgrid=True, gridcolor='#e5e7eb', tickformat='%H:%M:%S')
    fig.update_yaxes(showgrid=True, gridcolor='#e5e7eb')

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs='cdn',
                             config={'displayModeBar': True, 'scrollZoom': True})

    # ── Raw data JSON for DataTables ──────────────────────────────────────────
    radio_cols  = list(polls[0].keys())   if polls   else []
    client_cols = list(clients[0].keys()) if clients else []

    radio_json  = json.dumps([{c: _safe(row[c]) for c in radio_cols}  for row in polls])
    client_json = json.dumps([{c: _safe(row[c]) for c in client_cols} for row in clients])

    radio_dt_cols  = json.dumps([{"data": c, "title": c.replace('_', ' ')}
                                  for c in radio_cols])
    client_dt_cols = json.dumps([{"data": c, "title": c.replace('_', ' ')}
                                  for c in client_cols])

    n_radio_rows  = len(polls)
    n_client_rows = len(clients)

    # ── EOD back-link ─────────────────────────────────────────────────────────
    eod_link_top = (f'<p style="margin-bottom:8px">'
                    f'<a href="{eod_url}" style="color:#555;font-size:12px">'
                    f'← Session Log / EOD</a></p>' if eod_url else '')
    eod_link_footer = (f'&nbsp;·&nbsp;<a href="{eod_url}" style="color:#999">'
                       f'Session Log ↗</a>' if eod_url else '')

    # ── Write output path ─────────────────────────────────────────────────────
    os.makedirs(out_dir, exist_ok=True)
    try:
        slug = datetime.fromisoformat(start_ts).strftime('%Y%m%d_%H%M')
    except Exception:
        from datetime import timezone
        slug = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')

    out_path = os.path.join(out_dir, f'session_{slug}.html')

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DT Session — {ap_name} · {slug}</title>
<link href="https://fonts.googleapis.com/css2?family=Libre+Baskerville:wght@400;700&display=swap" rel="stylesheet">
<!-- DataTables 2.x (jQuery-free) + Buttons extension for CSV export -->
<link  rel="stylesheet" href="https://cdn.datatables.net/v/dt/jq-3.7.0-dt-2.0.8-b-3.0.2/datatables.min.css">
<script src="https://cdn.datatables.net/v/dt/jq-3.7.0-dt-2.0.8-b-3.0.2/datatables.min.js"></script>
<script src="https://cdn.datatables.net/buttons/3.0.2/js/buttons.html5.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js"></script>
<style>
  body {{ font-family:'Libre Baskerville',Georgia,serif; background:#fff; color:#1a1a1a;
         max-width:1400px; margin:0 auto; padding:36px 28px 80px; }}
  h1   {{ font-size:1.5rem; font-weight:700; margin-bottom:4px; }}
  h2   {{ font-size:1.1rem; font-weight:700; margin:40px 0 12px;
          border-bottom:2px solid #1a1a1a; padding-bottom:8px; }}
  .meta {{ font-size:13px; color:#666; margin-bottom:32px;
           border-bottom:2px solid #1a1a1a; padding-bottom:14px; }}
  .stat-row {{ display:flex; gap:32px; margin-bottom:32px; flex-wrap:wrap; }}
  .stat     {{ border-left:3px solid #1a1a1a; padding-left:12px; }}
  .stat-val {{ font-size:1.4rem; font-weight:700; }}
  .stat-lbl {{ font-size:11px; text-transform:uppercase; letter-spacing:.08em; color:#666; }}

  /* Tab bar */
  .tab-bar  {{ display:flex; gap:0; margin-bottom:16px; border-bottom:2px solid #1a1a1a; }}
  .tab-btn  {{ padding:8px 20px; font-family:inherit; font-size:13px; font-weight:700;
               cursor:pointer; border:none; background:transparent; color:#666;
               border-bottom:3px solid transparent; margin-bottom:-2px; }}
  .tab-btn.active {{ color:#1a1a1a; border-bottom-color:#1a1a1a; }}
  .tab-pane {{ display:none; }}
  .tab-pane.active {{ display:block; }}

  /* DataTables overrides to match NYT serif theme */
  table.dataTable {{ font-family:'Inter','Helvetica Neue',Arial,sans-serif;
                     font-size:11px; border-collapse:collapse; width:100% !important; }}
  table.dataTable thead th {{ background:#f5f5f5; color:#333; font-weight:700;
                               font-size:10px; text-transform:uppercase; letter-spacing:.04em;
                               border-bottom:2px solid #ccc; white-space:nowrap; padding:6px 10px; }}
  table.dataTable tbody td {{ padding:4px 10px; border-bottom:1px solid #eee;
                               white-space:nowrap; font-variant-numeric:tabular-nums; }}
  table.dataTable tbody tr:hover {{ background:#fffbe6; }}
  .dt-search input, .dt-length select {{ font-family:inherit; font-size:12px; }}
  .dt-buttons {{ margin-bottom:8px; }}
  .dt-button  {{ font-family:inherit !important; font-size:11px !important;
                 background:#1a1a1a !important; color:#fff !important;
                 border-radius:3px !important; padding:5px 12px !important;
                 border:none !important; cursor:pointer !important; margin-right:4px; }}
  .dt-button:hover {{ background:#444 !important; }}
  .null-val {{ color:#bbb; font-style:italic; }}

  footer {{ margin-top:60px; border-top:1px solid #ddd; padding-top:16px;
            font-size:12px; color:#999; }}
</style>
</head>
<body>

{eod_link_top}
<h1>DigitalTwinEngine — Session Report</h1>
<div class="meta">
  {ap_name} ({ap_model}) &nbsp;·&nbsp; {ts_label} &nbsp;·&nbsp;
  Duration: {duration} &nbsp;·&nbsp; Polls: {n_polls} &nbsp;·&nbsp;
  Session ID: {session_id}
</div>

<div class="stat-row">
  <div class="stat"><div class="stat-val">{n_polls}</div>
    <div class="stat-lbl">Polls</div></div>
  <div class="stat"><div class="stat-val">{len(radios_seen)}</div>
    <div class="stat-lbl">Radios</div></div>
  <div class="stat"><div class="stat-val">{duration}</div>
    <div class="stat-lbl">Duration</div></div>
  <div class="stat"><div class="stat-val">{n_radio_rows}</div>
    <div class="stat-lbl">DB Rows (radio)</div></div>
  <div class="stat"><div class="stat-val">{n_client_rows}</div>
    <div class="stat-lbl">DB Rows (clients)</div></div>
  <div class="stat"><div class="stat-val">{session_id}</div>
    <div class="stat-lbl">Session ID</div></div>
</div>

{chart_html}

<!-- ── Raw database data ─────────────────────────────────────────────────── -->
<h2>Raw Database — radio_polls &amp; client_polls</h2>
<p style="font-size:13px;color:#555;margin-bottom:16px;font-family:'Inter',sans-serif">
  All rows from <code>digital_twin.db</code> for this session.
  Sortable · Searchable · CSV export.
  Null fields shown as <span class="null-val">—</span>.
</p>

<div class="tab-bar">
  <button class="tab-btn active" onclick="switchTab('radio',this)">
    Radio Polls ({n_radio_rows} rows · {len(radio_cols)} cols)
  </button>
  <button class="tab-btn" onclick="switchTab('client',this)">
    Client Polls ({n_client_rows} rows · {len(client_cols)} cols)
  </button>
</div>

<div id="tab-radio" class="tab-pane active">
  <table id="tbl-radio" class="display" style="width:100%"></table>
</div>
<div id="tab-client" class="tab-pane">
  <table id="tbl-client" class="display" style="width:100%"></table>
</div>

<footer>
  © 2026 Khursheed Khan · DigitalTwinEngine · {ap_name} · {ap_model}{eod_link_footer}
</footer>

<script>
/* ── Embedded data from digital_twin.db ──────────────────────────────────── */
const RADIO_DATA   = {radio_json};
const CLIENT_DATA  = {client_json};
const RADIO_COLS   = {radio_dt_cols};
const CLIENT_COLS  = {client_dt_cols};

/* Render null as an italic dash */
function renderNull(data) {{
  if (data === null || data === undefined || data === '')
    return '<span class="null-val">—</span>';
  return data;
}}

/* Apply renderNull to all column definitions */
function addRender(cols) {{
  return cols.map(c => Object.assign({{}}, c, {{render: renderNull}}));
}}

/* ── DataTables init ─────────────────────────────────────────────────────── */
$(function() {{
  $('#tbl-radio').DataTable({{
    data:      RADIO_DATA,
    columns:   addRender(RADIO_COLS),
    pageLength: 25,
    scrollX:   true,
    dom:       'Bfrtip',
    buttons: [
      {{ extend: 'csvHtml5', text: '⬇ Download Radio CSV',
         filename: 'radio_polls_{slug}', exportOptions: {{columns: ':visible'}} }},
      {{ extend: 'colvis',   text: 'Columns ▾' }},
    ],
    order:  [[0, 'asc']],
    language: {{ search: 'Filter:' }},
  }});

  $('#tbl-client').DataTable({{
    data:      CLIENT_DATA,
    columns:   addRender(CLIENT_COLS),
    pageLength: 25,
    scrollX:   true,
    dom:       'Bfrtip',
    buttons: [
      {{ extend: 'csvHtml5', text: '⬇ Download Client CSV',
         filename: 'client_polls_{slug}', exportOptions: {{columns: ':visible'}} }},
      {{ extend: 'colvis',   text: 'Columns ▾' }},
    ],
    order:  [[0, 'asc']],
    language: {{ search: 'Filter:' }},
  }});
}});

function switchTab(name, btn) {{
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  btn.classList.add('active');
  /* Redraw so DataTables recalculates column widths after becoming visible */
  $.fn.dataTable.tables({{visible: true, api: true}}).columns.adjust();
}}
</script>

</body>
</html>"""

    with open(out_path, 'w') as f:
        f.write(html)

    return os.path.abspath(out_path)
