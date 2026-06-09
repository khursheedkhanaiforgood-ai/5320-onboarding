"""
DigitalTwinEngine — Session HTML report generator.
Sprint 2a: one DB per session → one HTML per session.

Output: {out_dir}/session_SLUG.html  (self-contained, CDN only)

Sections:
  1. Stats header (polls, radios, duration, session ID)
  2. Main chart — Plotly 3-row: Throughput / CU+CRC+Link / Stations+SNR
  3. Metric Explorer — user selects any of the 65 stored fields; dual Y-axis
  4. Raw Data — DataTables with CSV export (radio_polls | client_polls tabs)
"""
import json
import math
import os
from datetime import datetime
from typing import Optional

from plotly.subplots import make_subplots
import plotly.graph_objects as go
import plotly.io as pio


# ── Metric groups for the Explorer panel ──────────────────────────────────────
# (field_name, display_label, 'left'|'right')  — right = dBm secondary y-axis

_EXPLORER_GROUPS = {
    'Throughput': [
        ('tx_throughput_mbps',      'Tx Throughput (Mbps)',  'left'),
        ('rx_throughput_mbps',      'Rx Throughput (Mbps)',  'left'),
    ],
    'Link Reliability': [
        ('link_score',              'Link Score (0–100)',     'left'),
        ('crc_error_pct',           'CRC Error %',           'left'),
        ('crc_airtime_pct',         'CRC Airtime %',         'left'),
    ],
    'RF Health': [
        ('noise_floor_dbm',         'Noise Floor',           'right'),
        ('st_noise_dbm',            'ST Noise Floor',        'right'),
        ('avg_noise_dbm',           'Avg Noise',             'right'),
    ],
    'Channel Utilization': [
        ('tx_cu_pct',               'Tx CU %',               'left'),
        ('rx_cu_pct',               'Rx CU %',               'left'),
        ('interference_cu_pct',     'Interference CU %',     'left'),
        ('total_cu_pct',            'Total CU %',            'left'),
    ],
    'Airtime Detail': [
        ('rx_airtime_pct',          'Rx Airtime %',          'left'),
        ('tx_airtime_pct',          'Tx Airtime %',          'left'),
        ('st_tx_cu_pct',            'ST Tx CU %',            'left'),
        ('st_rx_cu_pct',            'ST Rx CU %',            'left'),
        ('st_int_cu_pct',           'ST Int CU %',           'left'),
        ('snap_tx_cu_pct',          'Snap Tx CU %',          'left'),
        ('snap_rx_cu_pct',          'Snap Rx CU %',          'left'),
    ],
    'Running Averages': [
        ('avg_tx_cu_pct',           'Avg Tx CU %',           'left'),
        ('avg_rx_cu_pct',           'Avg Rx CU %',           'left'),
        ('avg_interference_cu_pct', 'Avg Int CU %',          'left'),
    ],
    'TX Power': [
        ('tx_power_dbm',            'Tx Power (dBm)',         'right'),
        ('eirp_dbm',                'EIRP (dBm)',             'right'),
    ],
    'Capacity & RRM': [
        ('station_count',           'Station Count',          'left'),
        ('channel_width_mhz',       'Chan Width (MHz)',        'left'),
        ('acsp_channel',            'ACSP Channel',           'left'),
        ('acsp_channel_cost',       'ACSP Cost',              'left'),
        ('acsp_neighbor_count',     'ACSP Neighbors',         'left'),
        ('beacon_interval_ms',      'Beacon Interval (ms)',   'left'),
    ],
    'EDCA Params': [
        ('wmm_txop_be',             'TXOP BE (µs)',            'left'),
        ('wmm_txop_vi',             'TXOP VI (µs)',            'left'),
        ('wmm_txop_vo',             'TXOP VO (µs)',            'left'),
        ('wmm_aifs_be',             'AIFS BE',                 'left'),
        ('wmm_cw_min_be',           'CW-min BE',               'left'),
        ('wmm_cw_max_be',           'CW-max BE',               'left'),
    ],
    'Policy Thresholds': [
        ('weak_snr_threshold_db',   'Weak SNR Thr (dB)',      'right'),
        ('max_acsp_tx_power_dbm',   'Max ACSP Pwr (dBm)',     'right'),
        ('power_floor_dbm',         'Power Floor (dBm)',      'right'),
        ('interference_switch_pct', 'Int-Switch %',           'left'),
        ('crc_switch_pct',          'CRC-Switch %',           'left'),
        ('cu_switch_pct',           'CU-Switch %',            'left'),
        ('lb_airtime_limit_pct',    'LB Airtime Limit %',     'left'),
    ],
    'BGSCAN & Radar': [
        ('bgscan_count',            'BGSCAN Count',           'left'),
        ('bgscan_missed',           'BGSCAN Missed',          'left'),
        ('radar_count',             'Radar Events',           'left'),
    ],
}

_DEFAULT_EXPLORER = {
    'tx_throughput_mbps', 'rx_throughput_mbps',
    'total_cu_pct', 'crc_error_pct', 'link_score', 'station_count',
}


def _safe(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def generate_session_html(session_id: int, db_path: str, out_dir: str,
                          eod_url: Optional[str] = None) -> str:
    """
    Build a full session HTML from digital_twin.db.
    eod_url: optional relative/absolute URL to the EOD session log HTML.
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

    # ── Plotly 3-row main chart ────────────────────────────────────────────────
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

        def _series(field, _r=rows_r):
            return [r.get(field) for r in _r]

        fig.add_trace(go.Scatter(x=ts_vals, y=_series('tx_throughput_mbps'),
            name=f'{name} Tx Mbps', legendgroup=radio,
            line=dict(color=c_main, width=2),
            hovertemplate='%{y:.1f} Mbps<extra>Tx</extra>'), row=1, col=1)
        fig.add_trace(go.Scatter(x=ts_vals, y=_series('rx_throughput_mbps'),
            name=f'{name} Rx Mbps', legendgroup=radio,
            line=dict(color=c_light, width=1.5, dash='dot'),
            hovertemplate='%{y:.1f} Mbps<extra>Rx</extra>'), row=1, col=1)

        fig.add_trace(go.Scatter(x=ts_vals, y=_series('total_cu_pct'),
            name=f'{name} Total CU%', legendgroup=radio,
            line=dict(color=c_main, width=2),
            hovertemplate='%{y:.1f}%<extra>Total CU</extra>'), row=2, col=1)
        fig.add_trace(go.Scatter(x=ts_vals, y=_series('crc_error_pct'),
            name=f'{name} CRC%', legendgroup=radio,
            line=dict(color='#dc2626', width=1.5),
            hovertemplate='%{y:.2f}%<extra>CRC Error</extra>'), row=2, col=1)
        fig.add_trace(go.Scatter(x=ts_vals, y=_series('link_score'),
            name=f'{name} Link Score', legendgroup=radio,
            line=dict(color=c_light, width=1.5, dash='dash'),
            hovertemplate='%{y:.0f}/100<extra>Link Score</extra>'), row=2, col=1)

        fig.add_trace(go.Bar(x=ts_vals, y=_series('station_count'),
            name=f'{name} Stations', legendgroup=radio,
            marker_color=c_main, opacity=0.6,
            hovertemplate='%{y} clients<extra>Stations</extra>'), row=3, col=1)
        if radio in snr_by_radio:
            snr_x, snr_y = snr_by_radio[radio]
            fig.add_trace(go.Scatter(x=snr_x, y=snr_y,
                name=f'{name} Avg SNR', legendgroup=radio,
                line=dict(color=c_light, width=2), yaxis='y6',
                hovertemplate='%{y:.1f} dB<extra>Avg SNR</extra>'), row=3, col=1)

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
            font=dict(size=18), x=0.02),
        height=700,
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
        hovermode='x unified',
        paper_bgcolor='#ffffff', plot_bgcolor='#fafafa',
        font=dict(family='Inter, Helvetica Neue, Arial, sans-serif', size=12),
        margin=dict(t=120, b=40, l=60, r=20),
    )
    fig.update_xaxes(showgrid=True, gridcolor='#e5e7eb', tickformat='%H:%M:%S')
    fig.update_yaxes(showgrid=True, gridcolor='#e5e7eb')

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs='cdn',
                             config={'displayModeBar': True, 'scrollZoom': True})

    # ── JSON data for Explorer + table search/CSV ─────────────────────────────
    radio_cols  = list(polls[0].keys())   if polls   else []
    client_cols = list(clients[0].keys()) if clients else []

    radio_json  = json.dumps([{c: _safe(row[c]) for c in radio_cols}  for row in polls])
    client_json = json.dumps([{c: _safe(row[c]) for c in client_cols} for row in clients])

    radio_dt_cols  = json.dumps([{"data": c, "title": c.replace('_', ' ')} for c in radio_cols])
    client_dt_cols = json.dumps([{"data": c, "title": c.replace('_', ' ')} for c in client_cols])

    n_radio_rows  = len(polls)
    n_client_rows = len(clients)

    # ── Server-side rendered table rows (no JS dependency for display) ─────────
    def _fmt_cell(v):
        if v is None:
            return '<td class="null-val">—</td>'
        return f'<td>{v}</td>'

    radio_thead = ''.join(f'<th>{c.replace("_"," ")}</th>' for c in radio_cols)
    radio_tbody = ''.join(
        '<tr>' + ''.join(_fmt_cell(row.get(c)) for c in radio_cols) + '</tr>'
        for row in polls
    )
    client_thead = ''.join(f'<th>{c.replace("_"," ")}</th>' for c in client_cols)
    client_tbody = ''.join(
        '<tr>' + ''.join(_fmt_cell(row.get(c)) for c in client_cols) + '</tr>'
        for row in clients
    )

    # ── Explorer groups → JS constant ─────────────────────────────────────────
    explorer_groups_js = json.dumps({
        grp: [[f, lbl, ax] for f, lbl, ax in fields]
        for grp, fields in _EXPLORER_GROUPS.items()
    })
    default_fields_js = json.dumps(list(_DEFAULT_EXPLORER))

    # ── EOD back-link ─────────────────────────────────────────────────────────
    eod_top = (f'<p style="margin-bottom:8px">'
               f'<a href="{eod_url}" style="color:#555;font-size:12px">'
               f'← Session Log / EOD</a></p>' if eod_url else '')
    eod_foot = (f'&nbsp;·&nbsp;<a href="{eod_url}" style="color:#999">Session Log ↗</a>'
                if eod_url else '')

    # ── Output path ───────────────────────────────────────────────────────────
    os.makedirs(out_dir, exist_ok=True)
    try:
        slug = datetime.fromisoformat(start_ts).strftime('%Y%m%d_%H%M')
    except Exception:
        from datetime import timezone
        slug = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')

    out_path = os.path.join(out_dir, f'session_{slug}.html')

    # ── Build checkbox panel HTML ──────────────────────────────────────────────
    panel_html_parts = []
    for grp, fields in _EXPLORER_GROUPS.items():
        checks = ''.join(
            f'<label style="display:flex;align-items:center;gap:5px;margin-bottom:4px;'
            f'font-size:11px;cursor:pointer;white-space:nowrap;">'
            f'<input type="checkbox" class="mx-cb" value="{f}" '
            f'{"checked" if f in _DEFAULT_EXPLORER else ""}> {lbl}'
            f'{"<span style=\'color:#999;font-size:9px\'> dBm</span>" if ax=="right" else ""}'
            f'</label>'
            for f, lbl, ax in fields
        )
        panel_html_parts.append(
            f'<div style="margin-bottom:14px">'
            f'<div style="font-size:9px;font-weight:700;text-transform:uppercase;'
            f'letter-spacing:.08em;color:#888;margin-bottom:5px">{grp}</div>'
            f'{checks}</div>'
        )
    panel_html = '\n'.join(panel_html_parts)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DT Session — {ap_name} · {slug}</title>
<link href="https://fonts.googleapis.com/css2?family=Libre+Baskerville:wght@400;700&display=swap" rel="stylesheet">
<style>
body{{font-family:'Libre Baskerville',Georgia,serif;background:#fff;color:#1a1a1a;
     max-width:1400px;margin:0 auto;padding:36px 28px 80px}}
h1{{font-size:1.5rem;font-weight:700;margin-bottom:4px}}
h2{{font-size:1.05rem;font-weight:700;margin:44px 0 12px;
    border-bottom:2px solid #1a1a1a;padding-bottom:8px}}
.meta{{font-size:13px;color:#666;margin-bottom:32px;
       border-bottom:2px solid #1a1a1a;padding-bottom:14px}}
.stat-row{{display:flex;gap:28px;margin-bottom:32px;flex-wrap:wrap}}
.stat{{border-left:3px solid #1a1a1a;padding-left:12px}}
.stat-val{{font-size:1.35rem;font-weight:700}}
.stat-lbl{{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:#666}}

/* Explorer layout */
.explorer-wrap{{display:flex;gap:16px;align-items:flex-start}}
.explorer-panel{{width:210px;min-width:210px;max-height:520px;overflow-y:auto;
                 border:1px solid #e5e7eb;border-radius:6px;padding:14px;
                 background:#fafafa;font-family:'Inter','Helvetica Neue',Arial,sans-serif}}
.explorer-panel input[type=checkbox]{{accent-color:#1a1a1a}}
.explorer-chart{{flex:1;min-width:0}}
.ex-btn{{font-family:inherit;font-size:11px;background:#1a1a1a;color:#fff;
         border:none;border-radius:3px;padding:4px 10px;cursor:pointer;margin-right:6px}}
.ex-btn:hover{{background:#444}}
.ex-hint{{font-size:11px;color:#888;margin-bottom:10px;
          font-family:'Inter','Helvetica Neue',Arial,sans-serif}}

/* Raw data tables */
.tab-bar{{display:flex;border-bottom:2px solid #1a1a1a;margin-bottom:16px}}
.tab-btn{{padding:8px 20px;font-family:inherit;font-size:13px;font-weight:700;
          cursor:pointer;border:none;background:transparent;color:#666;
          border-bottom:3px solid transparent;margin-bottom:-2px}}
.tab-btn.active{{color:#1a1a1a;border-bottom-color:#1a1a1a}}
.tab-pane{{display:none}}.tab-pane.active{{display:block}}
.raw-search{{padding:5px 10px;border:1px solid #ccc;border-radius:3px;
             font-size:12px;width:240px;font-family:inherit}}
.raw-tbl{{border-collapse:collapse;font-family:'Inter','Helvetica Neue',Arial,sans-serif;
          font-size:11px;width:100%}}
.raw-tbl thead th{{background:#f5f5f5;color:#333;font-weight:700;font-size:10px;
  text-transform:uppercase;letter-spacing:.04em;border-bottom:2px solid #ccc;
  white-space:nowrap;padding:6px 10px;cursor:pointer;user-select:none}}
.raw-tbl thead th:hover{{background:#ebebeb}}
.raw-tbl thead th.sort-asc::after{{content:' ▲';font-size:8px;color:#999}}
.raw-tbl thead th.sort-desc::after{{content:' ▼';font-size:8px;color:#999}}
.raw-tbl tbody td{{padding:4px 10px;border-bottom:1px solid #eee;
  white-space:nowrap;font-variant-numeric:tabular-nums}}
.raw-tbl tbody tr:hover{{background:#fffbe6}}
.raw-tbl tbody tr.hidden{{display:none}}
.null-val{{color:#bbb;font-style:italic}}
.pg-info{{font-size:11px;color:#888;font-family:'Inter',sans-serif}}
footer{{margin-top:60px;border-top:1px solid #ddd;padding-top:16px;font-size:12px;color:#999}}
</style>
</head>
<body>

{eod_top}
<h1>DigitalTwinEngine — Session Report</h1>
<div class="meta">
  {ap_name} ({ap_model}) &nbsp;·&nbsp; {ts_label} &nbsp;·&nbsp;
  Duration: {duration} &nbsp;·&nbsp; Polls: {n_polls} &nbsp;·&nbsp; Session ID: {session_id}
</div>

<div class="stat-row">
  <div class="stat"><div class="stat-val">{n_polls}</div><div class="stat-lbl">Polls</div></div>
  <div class="stat"><div class="stat-val">{len(radios_seen)}</div><div class="stat-lbl">Radios</div></div>
  <div class="stat"><div class="stat-val">{duration}</div><div class="stat-lbl">Duration</div></div>
  <div class="stat"><div class="stat-val">{n_radio_rows}</div><div class="stat-lbl">DB Rows (radio)</div></div>
  <div class="stat"><div class="stat-val">{n_client_rows}</div><div class="stat-lbl">DB Rows (clients)</div></div>
  <div class="stat"><div class="stat-val">{session_id}</div><div class="stat-lbl">Session ID</div></div>
</div>

<!-- ── §1 Main chart ──────────────────────────────────────────────────────── -->
<h2>Overview</h2>
{chart_html}

<!-- ── §2 Metric Explorer ────────────────────────────────────────────────── -->
<h2>Metric Explorer</h2>
<p class="ex-hint">
  Select any combination of the 65 stored fields.
  <strong>Left axis</strong> = % / count / score &nbsp;·&nbsp;
  <strong>Right axis</strong> = dBm fields (marked) &nbsp;·&nbsp;
  Both radios shown with distinct colours. Click legend to isolate a radio.
</p>
<div style="margin-bottom:10px">
  <button class="ex-btn" onclick="selectAll(true)">Select all</button>
  <button class="ex-btn" onclick="selectAll(false)">Clear</button>
  <button class="ex-btn" onclick="resetDefault()">Default</button>
</div>

<div class="explorer-wrap">
  <div class="explorer-panel" id="metric-panel">
{panel_html}
  </div>
  <div class="explorer-chart">
    <div id="explorer-chart"></div>
  </div>
</div>

<!-- ── §3 Raw Data ───────────────────────────────────────────────────────── -->
<h2>Raw Database &mdash; radio_polls &amp; client_polls</h2>
<p style="font-size:13px;color:#555;margin-bottom:16px;
   font-family:'Inter',sans-serif">
  All rows from <code>session_{slug}.db</code> for this session.
  Sortable · Searchable · CSV export. Null = <span class="null-val">—</span>.
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
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap">
    <input id="search-radio" class="raw-search" placeholder="Search radio data…"
           oninput="filterTable('search-radio','tbody-radio')">
    <button class="ex-btn" onclick="exportCSV('tbody-radio','thead-radio','radio_polls_{slug}')">⬇ CSV</button>
    <span class="pg-info" id="pg-tbody-radio">{n_radio_rows} rows</span>
  </div>
  <div style="overflow-x:auto">
    <table class="raw-tbl">
      <thead id="thead-radio"><tr>{radio_thead}</tr></thead>
      <tbody id="tbody-radio">{radio_tbody}</tbody>
    </table>
  </div>
</div>
<div id="tab-client" class="tab-pane">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap">
    <input id="search-client" class="raw-search" placeholder="Search client data…"
           oninput="filterTable('search-client','tbody-client')">
    <button class="ex-btn" onclick="exportCSV('tbody-client','thead-client','client_polls_{slug}')">⬇ CSV</button>
    <span class="pg-info" id="pg-tbody-client">{n_client_rows} rows</span>
  </div>
  <div style="overflow-x:auto">
    <table class="raw-tbl">
      <thead id="thead-client"><tr>{client_thead}</tr></thead>
      <tbody id="tbody-client">{client_tbody}</tbody>
    </table>
  </div>
</div>

<footer>
  © 2026 Khursheed Khan · DigitalTwinEngine · {ap_name} · {ap_model}{eod_foot}
</footer>

<!-- ── Scripts ───────────────────────────────────────────────────────────── -->
<script>
/* ── Embedded data ───────────────────────────────────────────────────────── */
const RADIO_DATA    = {radio_json};
const CLIENT_DATA   = {client_json};
const RADIO_COLS    = {radio_dt_cols};
const CLIENT_COLS   = {client_dt_cols};
const EX_GROUPS     = {explorer_groups_js};
const DEFAULT_FIELDS= new Set({default_fields_js});

/* ── Metric Explorer ─────────────────────────────────────────────────────── */
const RADIO_COLORS = {{
  wifi0: ['#2563eb','#93c5fd'],
  wifi1: ['#16a34a','#86efac'],
  wifi2: ['#7c3aed','#c4b5fd'],
}};

// flat map: field -> [label, axis]
const FIELD_META = {{}};
for (const [grp, rows] of Object.entries(EX_GROUPS))
  for (const [f,l,a] of rows) FIELD_META[f] = [l, a];

function buildExplorer() {{
  const selected = [...document.querySelectorAll('#metric-panel .mx-cb:checked')]
                   .map(i => i.value);
  if (!selected.length) {{
    Plotly.purge('explorer-chart');
    return;
  }}

  const radios = [...new Set(RADIO_DATA.map(r => r.radio))];
  const traces = [];
  const shownFields = new Set();

  for (const radio of radios) {{
    const rows  = RADIO_DATA.filter(r => r.radio === radio);
    const ts    = rows.map(r => r.ts);
    const [c1, c2] = RADIO_COLORS[radio] || ['#7c3aed','#c4b5fd'];

    selected.forEach((field, idx) => {{
      if (!FIELD_META[field]) return;
      const [label, axis] = FIELD_META[field];
      const vals = rows.map(r => r[field]);
      if (vals.every(v => v == null)) return;

      traces.push({{
        x: ts, y: vals,
        name: radio + ' — ' + label,
        type: 'scatter', mode: 'lines',
        yaxis: axis === 'right' ? 'y2' : 'y',
        line: {{ color: idx % 2 === 0 ? c1 : c2, width: 1.5 }},
        hovertemplate: '%{{y:.2f}}<extra>' + radio + ' ' + label + '</extra>',
        legendgroup: radio,
      }});
      shownFields.add(field);
    }});
  }}

  const leftFields  = selected.filter(f => FIELD_META[f] && FIELD_META[f][1] === 'left');
  const rightFields = selected.filter(f => FIELD_META[f] && FIELD_META[f][1] === 'right');

  Plotly.react('explorer-chart', traces, {{
    height: 420,
    margin: {{t:20, b:60, l:65, r: rightFields.length ? 65 : 20}},
    yaxis: {{
      title: leftFields.length  ? '% / count / score' : '',
      gridcolor: '#e5e7eb', zeroline: false, autorange: true,
    }},
    yaxis2: {{
      title: rightFields.length ? 'dBm' : '',
      overlaying: 'y', side: 'right',
      gridcolor: '#e5e7eb', zeroline: false, autorange: true,
      showgrid: false,
    }},
    xaxis: {{ gridcolor: '#e5e7eb', tickformat: '%H:%M:%S' }},
    hovermode: 'x unified',
    legend: {{ orientation: 'h', y: -0.18 }},
    paper_bgcolor: '#fff',
    plot_bgcolor:  '#fafafa',
    font: {{ family: 'Inter, Helvetica Neue, Arial, sans-serif', size: 12 }},
  }}, {{responsive: true}});
}}

function selectAll(val) {{
  document.querySelectorAll('#metric-panel .mx-cb').forEach(cb => cb.checked = val);
  buildExplorer();
}}

function resetDefault() {{
  document.querySelectorAll('#metric-panel .mx-cb').forEach(cb => {{
    cb.checked = DEFAULT_FIELDS.has(cb.value);
  }});
  buildExplorer();
}}

document.querySelectorAll('#metric-panel .mx-cb').forEach(cb =>
  cb.addEventListener('change', buildExplorer));

// defer so flex layout is painted before Plotly measures container width
requestAnimationFrame(() => requestAnimationFrame(buildExplorer));

/* ── Native tables (no CDN — works on file://) ───────────────────────────── */
function filterTable(inputId, tbodyId) {{
  const q = document.getElementById(inputId).value.toLowerCase();
  document.querySelectorAll('#' + tbodyId + ' tr').forEach(tr => {{
    tr.classList.toggle('hidden', q !== '' && !tr.textContent.toLowerCase().includes(q));
  }});
  updatePageInfo(tbodyId);
}}

function exportCSV(tbodyId, theadId, filename) {{
  const ths = [...document.querySelectorAll('#' + theadId + ' th')]
               .map(th => '"' + th.textContent.trim().replace(/[▲▼]/g,'').trim() + '"');
  const rows = [...document.querySelectorAll('#' + tbodyId + ' tr:not(.hidden)')]
    .map(tr => [...tr.querySelectorAll('td')].map(td => {{
      const v = td.textContent.trim();
      return v === '—' ? '' : '"' + v.replace(/"/g,'""') + '"';
    }}).join(','));
  const csv = [ths.join(','), ...rows].join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], {{type:'text/csv'}}));
  a.download = filename + '.csv';
  a.click();
}}

function updatePageInfo(tbodyId) {{
  const total   = document.querySelectorAll('#' + tbodyId + ' tr').length;
  const visible = document.querySelectorAll('#' + tbodyId + ' tr:not(.hidden)').length;
  const el = document.getElementById('pg-' + tbodyId);
  if (el) el.textContent = (visible < total ? visible + ' / ' : '') + total + ' rows';
}}

function switchTab(name, btn) {{
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  btn.classList.add('active');
  // resize explorer chart in case it was rendered while hidden
  if (document.getElementById('explorer-chart')?.data) {{
    Plotly.Plots.resize('explorer-chart');
  }}
}}

/* wire column-header sort on both tables */
['radio','client'].forEach(name => {{
  const theadId = 'thead-' + name;
  const tbodyId = 'tbody-' + name;
  let sortCol = -1, sortAsc = true;
  document.querySelectorAll('#' + theadId + ' th').forEach((th, i) => {{
    th.addEventListener('click', () => {{
      if (sortCol === i) sortAsc = !sortAsc; else {{ sortCol = i; sortAsc = true; }}
      document.querySelectorAll('#' + theadId + ' th').forEach(t =>
        t.classList.remove('sort-asc','sort-desc'));
      th.classList.add(sortAsc ? 'sort-asc' : 'sort-desc');
      const rows = [...document.querySelectorAll('#' + tbodyId + ' tr')];
      rows.sort((a, b) => {{
        const va = a.cells[i]?.textContent.trim() ?? '';
        const vb = b.cells[i]?.textContent.trim() ?? '';
        const na = parseFloat(va), nb = parseFloat(vb);
        const cmp = (!isNaN(na) && !isNaN(nb)) ? na - nb : va.localeCompare(vb);
        return sortAsc ? cmp : -cmp;
      }});
      const tbody = document.getElementById(tbodyId);
      rows.forEach(r => tbody.appendChild(r));
    }});
  }});
}});
</script>
</body>
</html>"""

    with open(out_path, 'w') as f:
        f.write(html)

    return os.path.abspath(out_path)
