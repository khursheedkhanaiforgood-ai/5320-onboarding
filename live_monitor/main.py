"""
DigitalTwinEngine — Sprint 1
Live AP metric collector for AP3000 (HiveOS).
Polls wifi0 + wifi1 every POLL_INTERVAL seconds.
Outputs:
  • Rich terminal table (this window)
  • Plotly Dash browser dashboard → http://localhost:8050
  • CSV log per session (data/)
"""
import numpy as np  # noqa: F401 — must be first to avoid Py3.14 circular import with plotly
import csv
import os
import sys
import time
import signal
import threading
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from rich.console import Console, Group
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
from rich.columns import Columns
from rich import box

import config
import storage as _st
from agents.collector import CollectorAgent, PollResult, RadioMetrics
from dashboard import DataStore, create_app, find_free_port
from report import generate_session_html

console  = Console()
_running = True


def _handle_signal(sig, frame):
    global _running
    _running = False


signal.signal(signal.SIGINT,  _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ── CSV logging ───────────────────────────────────────────────────────────────

def _open_csv_writers(log_dir: str, session_ts: str):
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    slug = session_ts.replace(':', '').replace('.', '').replace('+', '')[:15]

    radio_path  = os.path.join(log_dir, f'radio_{slug}.csv')
    client_path = os.path.join(log_dir, f'client_{slug}.csv')

    radio_fh  = open(radio_path,  'w', newline='')
    client_fh = open(client_path, 'w', newline='')

    radio_fields = [
        'ts', 'ap_name', 'radio', 'band', 'channel', 'channel_width_mhz',
        'summary_state', 'noise_floor_dbm', 'tx_power_dbm', 'eirp_dbm',
        'crc_error_pct', 'crc_airtime_pct',
        'tx_cu_pct', 'rx_cu_pct', 'interference_cu_pct', 'total_cu_pct',
        'avg_tx_cu_pct', 'avg_rx_cu_pct', 'avg_interference_cu_pct', 'avg_noise_dbm',
        'station_count', 'spatial_streams', 'max_clients', 'beacon_interval_ms',
        'dynamic_chan_width', 'ofdma_dl', 'ofdma_ul', 'mu_mimo',
        'bss_color', 'twt', 'short_gi', 'beamforming', 'a_mpdu', 'frameburst',
        'a_msdu', 'dfs_enabled', 'acsp_state',
        'acsp_channel', 'acsp_channel_cost', 'acsp_neighbor_count',
        'cw_min_be', 'cw_min_vo', 'cw_min_vi',
        'radio_mac', 'bgscan_count',
        'radio_mac', 'tx_range_m', 'benchmark_11ax',
        'bgscan_count', 'bgscan_requested', 'bgscan_missed', 'radar_count',
        'rx_packets_total', 'tx_packets_total',
        'rx_pkt_errors', 'tx_pkt_errors', 'rx_pkt_dropped', 'tx_pkt_dropped',
        'tx_error_pct',
        'rx_airtime_sec', 'tx_airtime_sec', 'crc_airtime_sec',
        'rx_airtime_pct', 'tx_airtime_pct',
        'st_tx_cu_pct', 'st_rx_cu_pct', 'st_int_cu_pct', 'st_noise_dbm',
        'snap_tx_cu_pct', 'snap_rx_cu_pct', 'snap_int_cu_pct', 'snap_noise_dbm',
        'tx_bytes_total', 'rx_bytes_total',
        'tx_throughput_mbps', 'rx_throughput_mbps',
        'tx_retry_pct', 'tx_failed_pct', 'tx_retry_count', 'tx_failed_count',
        'link_score',
        # From show radio profile
        'wmm_cw_min_be', 'wmm_cw_max_be', 'wmm_aifs_be', 'wmm_txop_be',
        'wmm_aifs_vi', 'wmm_txop_vi', 'wmm_aifs_vo', 'wmm_txop_vo',
        'weak_snr_threshold_db', 'interference_switch_pct', 'crc_switch_pct',
        'cu_switch_pct', 'max_acsp_tx_power_dbm', 'power_floor_dbm',
        'lb_airtime_limit_pct', 'dcw_trigger_threshold',
        'high_density', 'band_steering_enabled', 'load_balance_enabled', 'safety_net_enabled',
    ]
    client_fields = [
        'ts', 'ap_name', 'radio', 'mac', 'ip_addr', 'chan', 'vlan_id',
        'snr_db', 'tx_rate_mbps', 'rx_rate_mbps', 'upid', 'chan_width_mhz',
        'a_mode', 'cipher', 'a_time_str', 'phymode', 'ldpc', 'station_state',
    ]

    radio_wr  = csv.DictWriter(radio_fh,  fieldnames=radio_fields,  extrasaction='ignore')
    client_wr = csv.DictWriter(client_fh, fieldnames=client_fields, extrasaction='ignore')
    radio_wr.writeheader()
    client_wr.writeheader()

    console.print(f'[dim]Radio log  → {radio_path}[/dim]')
    console.print(f'[dim]Client log → {client_path}[/dim]')

    return radio_fh, client_fh, radio_wr, client_wr


def _write_csv(result: PollResult, radio_wr, client_wr):
    for rm in result.radios:
        row = {k: getattr(rm, k, None) for k in radio_wr.fieldnames}
        row['ap_name'] = result.ap_name
        radio_wr.writerow(row)
    for cm in result.clients:
        row = {k: getattr(cm, k, None) for k in client_wr.fieldnames}
        row['ap_name'] = result.ap_name
        client_wr.writerow(row)


# ── Rich terminal display ─────────────────────────────────────────────────────

def _fmt_bool(v: bool | None, true_label='ON', false_label='OFF') -> str:
    if v is None:
        return '[dim]?[/dim]'
    return f'[green]{true_label}[/green]' if v else f'[red]{false_label}[/red]'


def _fmt_state(s: str | None) -> str:
    if not s:
        return '[dim]?[/dim]'
    c = 'green' if 'good' in s.lower() else ('yellow' if 'fair' in s.lower() else 'red')
    return f'[{c}]{s}[/{c}]'


def _fmt_crc(v: float | None) -> str:
    if v is None:
        return '[dim]?[/dim]'
    c = 'green' if v < 5 else ('yellow' if v < 15 else 'red')
    return f'[{c}]{v:.1f}%[/{c}]'


def _radio_table(rm: RadioMetrics) -> Table:
    """Compact 5-row table — fits two radios side-by-side in an 80-col terminal."""
    t = Table(
        title=(f'[bold]{rm.radio}[/bold]  [{rm.band}]  '
               f'Ch {rm.channel or "?"}  {rm.channel_width_mhz or "?"}MHz'),
        box=box.ROUNDED, show_header=False, min_width=48,
    )
    t.add_column('', style='dim', width=9, no_wrap=True)
    t.add_column('', width=37, no_wrap=True)

    # Row 1: RF state + noise + power
    nf  = f'NF:{rm.noise_floor_dbm:.0f}' if rm.noise_floor_dbm else 'NF:?'
    txp = f'Pwr:{rm.tx_power_dbm:.0f}dBm' if rm.tx_power_dbm else 'Pwr:?'
    ss  = f'{rm.spatial_streams}ss' if rm.spatial_streams else ''
    t.add_row('RF', f'{_fmt_state(rm.summary_state)}  {nf}  {txp}  {ss}')

    # Row 2: CRC + link score
    score_s = f'{rm.link_score:.0f}/100' if rm.link_score is not None else '?'
    score_c = 'green' if (rm.link_score or 0) >= 75 else ('yellow' if (rm.link_score or 0) >= 40 else 'red')
    crc_air = f'  air:{rm.crc_airtime_pct:.1f}%' if rm.crc_airtime_pct is not None else ''
    t.add_row('CRC',
              f'{_fmt_crc(rm.crc_error_pct)}{crc_air}  Score:[{score_c}]{score_s}[/{score_c}]')

    # Row 3: Channel utilization all four values
    def _pct(v): return f'{v:.0f}%' if v is not None else '?'
    t.add_row('CU',
              f'Tx:{_pct(rm.tx_cu_pct)}  Rx:{_pct(rm.rx_cu_pct)}'
              f'  Int:{_pct(rm.interference_cu_pct)}  Tot:{_pct(rm.total_cu_pct)}')

    # Row 4: Stations + ACSP
    sta  = str(rm.station_count or 0)
    acsp = (f'Ch{rm.acsp_channel}(c{rm.acsp_channel_cost})'
            if rm.acsp_channel else '?')
    nbrs = f'{rm.acsp_neighbor_count or 0}nb'
    t.add_row('RRM', f'{sta} sta  {acsp}  {nbrs}  BG:{rm.bgscan_count or 0}')

    # Row 5: WiFi6 flags inline
    def _f(label, val):
        if val is True:  return f'[green]{label}[/green]'
        if val is False: return f'[dim]{label}[/dim]'
        return f'[dim]{label}?[/dim]'
    flags = '  '.join([
        _f('DL',  rm.ofdma_dl),
        _f('UL',  rm.ofdma_ul),
        _f('MU',  rm.mu_mimo),
        _f('TWT', rm.twt),
        _f('GI',  getattr(rm, 'short_gi', None)),
        _f('DCW', rm.dynamic_chan_width),
    ])
    bss = rm.bss_color if rm.bss_color is not None else '?'
    t.add_row('WiFi6', f'{flags}  BSS:{bss}')

    return t


def _client_table(clients: list, max_rows: int = 8) -> Table:
    shown = clients[:max_rows]
    overflow = len(clients) - len(shown)
    title = f'[bold]Clients[/bold]  ({len(clients)} connected)'
    if overflow:
        title += f'  [dim]+{overflow} more[/dim]'

    t = Table(title=title, box=box.ROUNDED, show_header=True,
              header_style='bold cyan')
    t.add_column('Radio',   width=6)
    t.add_column('MAC',     width=18)
    t.add_column('IP',      width=15)
    t.add_column('VLAN',    width=5)
    t.add_column('SNR',     width=7)
    t.add_column('Tx',      width=7)
    t.add_column('Rx',      width=7)
    t.add_column('Mode',    width=6)
    t.add_column('Auth',    width=10)

    for c in shown:
        sc = 'green' if (c.snr_db or 0) > 25 else ('yellow' if (c.snr_db or 0) > 15 else 'red')
        t.add_row(
            c.radio, c.mac, c.ip_addr or '—', str(c.vlan_id or ''),
            f'[{sc}]{c.snr_db:.0f}dB[/{sc}]' if c.snr_db else '?',
            f'{c.tx_rate_mbps:.0f}' if c.tx_rate_mbps else '?',
            f'{c.rx_rate_mbps:.0f}' if c.rx_rate_mbps else '?',
            c.phymode or '—',
            c.a_mode or '—',
        )
    return t


def _build_display(result: PollResult, poll_num: int, port: int = 8050) -> Panel:
    radio_tables = [_radio_table(rm) for rm in result.radios]
    client_tbl   = _client_table(result.clients)
    ts_local     = datetime.now().strftime('%H:%M:%S')
    title = (
        f'[bold yellow]DigitalTwinEngine[/bold yellow] · '
        f'[bold]{result.ap_name}[/bold] ({result.ap_model}) · '
        f'Poll #{poll_num} · {ts_local} · '
        f'[dim]http://localhost:{port}[/dim]'
    )
    # Radios side-by-side on top row, client table below — avoids chopping
    body = Group(
        Columns(radio_tables, equal=False, expand=False),
        client_tbl,
    )
    return Panel(body, title=title, border_style='bright_blue')


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not config.AP_PASS:
        console.print('[red]AP_PASS not set. Copy .env.example → .env and fill password.[/red]')
        sys.exit(1)

    session_ts = datetime.now(timezone.utc).isoformat()
    slug       = session_ts.replace(':', '').replace('.', '').replace('+', '')[:15]
    radio_fh, client_fh, radio_wr, client_wr = _open_csv_writers(config.LOG_DIR, session_ts)

    debug_log_path = None
    if config.DEBUG_CLI:
        Path(config.LOG_DIR).mkdir(parents=True, exist_ok=True)
        debug_log_path = os.path.join(config.LOG_DIR, 'debug_cli.log')

    # ── SQLite storage — one DB file per session ──────────────────────────────
    # session_20260609_1744.db  ↔  session_20260609_1744.html  (always session_id=1)
    db_path    = os.path.join(config.LOG_DIR, f'session_{slug}.db')
    store      = _st.SQLiteStore(db_path)
    session_id = store.open_session(ap_ip=config.AP_IP)
    _st.init_health_clock()
    _st.update_health('starting', session_id=session_id)

    data_store = DataStore()
    port       = find_free_port(8050)

    # ── Dash + /health route ───────────────────────────────────────────────────
    dash_app = create_app(data_store)

    @dash_app.server.route('/health')
    def _health_route():
        import json
        from flask import Response
        return Response(json.dumps(_st.get_health()), mimetype='application/json')

    def _run_dash():
        import logging
        logging.getLogger('werkzeug').setLevel(logging.ERROR)
        dash_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

    dash_thread = threading.Thread(target=_run_dash, daemon=True, name='dash')
    dash_thread.start()

    # Open browser once the server has had a moment to bind
    def _open_browser():
        time.sleep(2.0)
        webbrowser.open(f'http://localhost:{port}')
    threading.Thread(target=_open_browser, daemon=True).start()

    agent = CollectorAgent(config.AP_IP, config.AP_USER, config.AP_PASS,
                           debug_log=debug_log_path)

    try:
        console.print(f'[bold]Connecting to {config.AP_IP}...[/bold]')
        agent.connect()
        console.print('[green]Connected.[/green]')
        agent.onboard()
        console.print(f'[green]AP identified: {agent.ap_name} ({agent.ap_model})[/green]')
        store.update_session_header(session_id, agent.ap_name, agent.ap_model)
        _st.update_health('ok', session_id=session_id)
        console.print(
            f'[dim]Poll interval: {config.POLL_INTERVAL}s · '
            f'Browser dashboard: http://localhost:{port} · Ctrl-C to stop[/dim]\n'
        )

        poll_num = 0
        with Live(console=console, refresh_per_second=4, screen=False) as live:
            while _running:
                try:
                    if not agent.is_connected():
                        console.print('[yellow]Reconnecting...[/yellow]')
                        agent.connect()

                    result = agent.poll()
                    poll_num += 1

                    _write_csv(result, radio_wr, client_wr)
                    radio_fh.flush()
                    client_fh.flush()

                    store.write_poll(session_id, result.radios, result.clients)
                    for tag, text, radio in result.raw_snapshots:
                        store.write_raw_snapshot(session_id, tag, text, radio=radio, ts=result.ts)
                    _st.update_health('ok', last_poll_ts=result.ts, session_id=session_id)

                    data_store.push(result)
                    live.update(_build_display(result, poll_num, port=port))

                except Exception as e:
                    console.print(f'[red]Poll error: {e}[/red]')

                time.sleep(config.POLL_INTERVAL)

    finally:
        try: _st.update_health('down')
        except Exception: pass
        try: agent.disconnect()
        except Exception: pass
        try: radio_fh.close()
        except Exception: pass
        try: client_fh.close()
        except Exception: pass
        console.print('\n[bold]Session ended. CSV logs saved.[/bold]')

        # ── SQLite close + session HTML report ────────────────────────────────
        try:
            store.close_session(session_id)
        except Exception: pass
        try:
            html_path = generate_session_html(session_id, db_path, config.LOG_DIR)
            console.print(f'\n[bold green]═══════════════════════════════════[/bold green]')
            console.print(f'[bold green]  Session report → {html_path}[/bold green]')
            console.print(f'[bold green]═══════════════════════════════════[/bold green]\n')
            webbrowser.open(f'file://{html_path}')
        except Exception as exc:
            console.print(f'[yellow]Report generation failed: {exc}[/yellow]')
        finally:
            try: store.close()
            except Exception: pass

        if debug_log_path:
            console.print(
                Panel(
                    f'[yellow]tail -f {debug_log_path}[/yellow]',
                    title='[bold]DEV — CLI debug log[/bold]',
                    border_style='yellow',
                )
            )


if __name__ == '__main__':
    main()
