"""
DigitalTwinEngine — Session HTML report generator.
Sprint 2a: called at session end (Ctrl-C) to produce a standalone Plotly report.

Output: {out_dir}/session_YYYYMMDD_HHMM.html
        Self-contained (Plotly loaded from CDN), open in any browser, email, share.
"""
import os
from datetime import datetime
from typing import Optional

from plotly.subplots import make_subplots
import plotly.graph_objects as go
import plotly.io as pio


def generate_session_html(session_id: int, db_path: str, out_dir: str) -> str:
    """
    Query session_id from digital_twin.db and write a Plotly time-series report.
    Returns the absolute path to the generated HTML file.
    """
    from storage import SQLiteStore

    store = SQLiteStore(db_path)
    try:
        sess   = store.get_session(session_id)
        polls  = store.iter_radio_polls(session_id)
        snr_ts = store.avg_snr_by_ts_radio(session_id)
    finally:
        store.close()

    if not polls:
        raise ValueError(f"No radio_polls found for session_id={session_id}")

    # ── Organise polls by radio ────────────────────────────────────────────────
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

    n_radios = len(radios_seen)

    # ── Three-row subplot layout ───────────────────────────────────────────────
    # Row 1: Throughput (Mbps)
    # Row 2: Channel utilization + CRC error + Link score
    # Row 3: Station count + Avg client SNR
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
        band    = rows_r[0].get('band', '')
        name    = f'{radio} ({band})'

        def _series(field):
            return [r.get(field) for r in rows_r]

        showleg = (radio == radios_seen[0])   # only first radio adds legend group header

        # Row 1 — Throughput
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

        # Row 2 — CU / CRC / Link Score
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

        # Row 3 — Station count + SNR
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

    # ── Metadata for title ─────────────────────────────────────────────────────
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
            font=dict(size=18),
            x=0.02,
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

    # ── Write HTML ─────────────────────────────────────────────────────────────
    os.makedirs(out_dir, exist_ok=True)

    try:
        slug = datetime.fromisoformat(start_ts).strftime('%Y%m%d_%H%M')
    except Exception:
        from datetime import timezone
        slug = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')

    out_path = os.path.join(out_dir, f'session_{slug}.html')

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs='cdn',
                             config={'displayModeBar': True, 'scrollZoom': True})

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DT Session — {ap_name} · {slug}</title>
<link href="https://fonts.googleapis.com/css2?family=Libre+Baskerville:wght@400;700&display=swap" rel="stylesheet">
<style>
  body {{ font-family:'Libre Baskerville',Georgia,serif; background:#fff; color:#1a1a1a;
         max-width:1100px; margin:0 auto; padding:36px 28px 80px; }}
  h1 {{ font-size:1.5rem; font-weight:700; margin-bottom:4px; }}
  .meta {{ font-size:13px; color:#666; margin-bottom:32px; border-bottom:2px solid #1a1a1a; padding-bottom:14px; }}
  .stat-row {{ display:flex; gap:32px; margin-bottom:32px; }}
  .stat {{ border-left:3px solid #1a1a1a; padding-left:12px; }}
  .stat-val {{ font-size:1.4rem; font-weight:700; }}
  .stat-lbl {{ font-size:11px; text-transform:uppercase; letter-spacing:.08em; color:#666; }}
  footer {{ margin-top:60px; border-top:1px solid #ddd; padding-top:16px;
            font-size:12px; color:#999; }}
</style>
</head>
<body>
<h1>DigitalTwinEngine — Session Report</h1>
<div class="meta">{ap_name} ({ap_model}) &nbsp;·&nbsp; {ts_label} &nbsp;·&nbsp;
  Duration: {duration} &nbsp;·&nbsp; Polls: {n_polls} &nbsp;·&nbsp;
  Session ID: {session_id}</div>

<div class="stat-row">
  <div class="stat"><div class="stat-val">{n_polls}</div><div class="stat-lbl">Polls</div></div>
  <div class="stat"><div class="stat-val">{len(radios_seen)}</div><div class="stat-lbl">Radios</div></div>
  <div class="stat"><div class="stat-val">{duration}</div><div class="stat-lbl">Duration</div></div>
  <div class="stat"><div class="stat-val">{session_id}</div><div class="stat-lbl">Session ID</div></div>
</div>

{chart_html}

<footer>© 2026 Khursheed Khan · DigitalTwinEngine · AP3000 AH-556680</footer>
</body>
</html>"""

    with open(out_path, 'w') as f:
        f.write(html)

    return os.path.abspath(out_path)
