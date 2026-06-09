#!/usr/bin/env python3
"""
DigitalTwinEngine — macOS Menu Bar Widget
End-user launcher: no terminal needed.

Run:  .venv/bin/python live_monitor/widget.py
      (or double-click if you've set up a .command wrapper)

What it does:
  • Connects to the AP and starts collecting every POLL_INTERVAL seconds
  • Starts the Plotly Dash browser dashboard in the background
  • Auto-opens http://localhost:8050 in your browser
  • Shows live AP health in the macOS menu bar — click the icon to see details
  • Writes CSV logs to LOG_DIR (same as main.py)
"""
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

import rumps
import config
import storage as _st
from agents.collector import CollectorAgent, PollResult
from dashboard import DataStore, run_dashboard, find_free_port

# ── CSV helpers (identical to main.py) ───────────────────────────────────────

def _open_csv_writers(log_dir: str, session_ts: str):
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    slug = session_ts.replace(':', '').replace('.', '').replace('+', '')[:15]

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

    radio_path  = os.path.join(log_dir, f'radio_{slug}.csv')
    client_path = os.path.join(log_dir, f'client_{slug}.csv')

    radio_fh  = open(radio_path,  'w', newline='')
    client_fh = open(client_path, 'w', newline='')
    radio_wr  = csv.DictWriter(radio_fh,  fieldnames=radio_fields,  extrasaction='ignore')
    client_wr = csv.DictWriter(client_fh, fieldnames=client_fields, extrasaction='ignore')
    radio_wr.writeheader()
    client_wr.writeheader()
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


# ── Menu bar app ──────────────────────────────────────────────────────────────

class DTEWidget(rumps.App):

    def __init__(self, data_store: DataStore, port: int = 8050):
        super().__init__('📡', title='📡', quit_button=None)
        self._store = data_store
        self._port  = port

        # Dynamic menu items — titles are updated by _tick() every 5 s
        self._ap_item      = rumps.MenuItem('Connecting to AP…')
        self._sep0         = None
        self._wifi0_item   = rumps.MenuItem('wifi0  [2.4 GHz]  …')
        self._wifi1_item   = rumps.MenuItem('wifi1  [5 GHz]  …')
        self._sep1         = None
        self._clients_item = rumps.MenuItem('Clients: —')
        self._sep2         = None
        self._score_item   = rumps.MenuItem('Link Score: —')
        self._tput_item    = rumps.MenuItem('Throughput: —')
        self._crc_item     = rumps.MenuItem('CRC: —')
        self._sep3         = None
        self._dash_item    = rumps.MenuItem('Open Dashboard →',
                                            callback=self._open_dash)
        self._sep4         = None
        self._quit_item    = rumps.MenuItem('Quit', callback=self._quit)

        self.menu = [
            self._ap_item,
            self._sep0,
            self._wifi0_item,
            self._wifi1_item,
            self._sep1,
            self._clients_item,
            self._sep2,
            self._score_item,
            self._tput_item,
            self._crc_item,
            self._sep3,
            self._dash_item,
            self._sep4,
            self._quit_item,
        ]

    # Called every 5 s by the rumps timer
    @rumps.timer(5)
    def _tick(self, _):
        h     = self._store.header()
        polls = self._store.snapshot()

        if not polls:
            self.title = '📡'
            self._ap_item.title = 'Connecting to AP…'
            return

        latest   = polls[-1]
        ts_local = datetime.fromisoformat(h['ts']).astimezone().strftime('%H:%M:%S')
        self._ap_item.title = (
            f"● {h['ap_name']}  ·  {h['ap_model']}"
            f"  ·  Poll #{h['poll_count']}  ·  {ts_local}"
        )

        scores, crcs, tx_mbps, rx_mbps = [], [], [], []

        for rm in latest.radios:
            band  = rm.band
            ch    = rm.channel or '?'
            cw    = f"{rm.channel_width_mhz}MHz" if rm.channel_width_mhz else '?'
            state = (rm.summary_state or '—').split(';')[0]
            crc   = rm.crc_error_pct
            cu    = rm.total_cu_pct
            score = rm.link_score
            sta   = rm.station_count or 0

            crc_s   = f"{crc:.0f}%"   if crc   is not None else '?'
            cu_s    = f"{cu:.0f}%"    if cu    is not None else '?'
            score_s = f"{score:.0f}"  if score is not None else '?'

            label = (
                f"{rm.radio}  [{band}]  Ch{ch}  {cw}"
                f"    CRC:{crc_s}  CU:{cu_s}  Score:{score_s}/100"
                f"    {sta} client{'s' if sta != 1 else ''}"
            )

            if rm.radio == 'wifi0':
                self._wifi0_item.title = label
            elif rm.radio == 'wifi1':
                self._wifi1_item.title = label

            if score is not None:
                scores.append(score)
            if crc is not None:
                crcs.append(crc)
            if rm.tx_throughput_mbps is not None:
                tx_mbps.append(rm.tx_throughput_mbps)
            if rm.rx_throughput_mbps is not None:
                rx_mbps.append(rm.rx_throughput_mbps)

        # Client summary
        n = len(latest.clients)
        if n == 0:
            self._clients_item.title = 'Clients: none'
        else:
            ips = [c.ip_addr or c.mac for c in latest.clients[:4]]
            self._clients_item.title = f"Clients: {n}   {', '.join(ips)}"

        # Aggregate scores / metrics
        if scores:
            avg   = sum(scores) / len(scores)
            icon  = '🟢' if avg >= 75 else ('🟡' if avg >= 40 else '🔴')
            self._score_item.title = f"Link Score: {avg:.0f}/100  {icon}"
            self.title = f'{icon} {avg:.0f}'
        else:
            self.title = '📡'

        if tx_mbps or rx_mbps:
            tx_s = f"↑ {sum(tx_mbps):.1f} Mbps" if tx_mbps else ''
            rx_s = f"↓ {sum(rx_mbps):.1f} Mbps" if rx_mbps else ''
            self._tput_item.title = f"Throughput:  {tx_s}   {rx_s}".strip()

        if crcs:
            worst = max(crcs)
            emoji = '✅' if worst < 5 else ('⚠️' if worst < 15 else '🚨')
            self._crc_item.title = (
                f"CRC (worst radio):  {worst:.0f}%  {emoji}"
            )

    def _open_dash(self, _):
        webbrowser.open(f'http://localhost:{self._port}')

    def _quit(self, _):
        global _widget_running
        _widget_running = False
        rumps.quit_application()


# ── Collector thread ──────────────────────────────────────────────────────────

_widget_running = True


def _collector_loop(data_store: DataStore):
    session_ts = datetime.now(timezone.utc).isoformat()

    radio_fh, client_fh, radio_wr, client_wr = _open_csv_writers(
        config.LOG_DIR, session_ts)

    debug_path = None
    if config.DEBUG_CLI:
        Path(config.LOG_DIR).mkdir(parents=True, exist_ok=True)
        debug_path = os.path.join(config.LOG_DIR, 'debug_cli.log')

    # One DB file per session — matches session_SLUG.html naming
    slug       = session_ts.replace(':', '').replace('.', '').replace('+', '')[:15]
    db_path    = os.path.join(config.LOG_DIR, f'session_{slug}.db')
    store      = _st.SQLiteStore(db_path)
    session_id = store.open_session(ap_ip=config.AP_IP)
    _st.init_health_clock()
    _st.update_health('starting', session_id=session_id)

    agent = CollectorAgent(config.AP_IP, config.AP_USER, config.AP_PASS,
                           debug_log=debug_path)
    try:
        agent.connect()
        agent.onboard()
        store.update_session_header(session_id, agent.ap_name, agent.ap_model)
        _st.update_health('ok', session_id=session_id)

        while _widget_running:
            try:
                if not agent.is_connected():
                    agent.connect()
                result = agent.poll()
                _write_csv(result, radio_wr, client_wr)
                radio_fh.flush()
                client_fh.flush()
                store.write_poll(session_id, result.radios, result.clients)
                for tag, text, radio in result.raw_snapshots:
                    store.write_raw_snapshot(session_id, tag, text, radio=radio,
                                             ts=result.ts)
                _st.update_health('ok', last_poll_ts=result.ts, session_id=session_id)
                data_store.push(result)
            except Exception:
                pass
            time.sleep(config.POLL_INTERVAL)
    finally:
        _st.update_health('down')
        agent.disconnect()
        radio_fh.close()
        client_fh.close()
        try:
            store.close_session(session_id)
            from report import generate_session_html
            html_path = generate_session_html(session_id, db_path, config.LOG_DIR)
            webbrowser.open(f'file://{html_path}')
        except Exception:
            pass
        finally:
            store.close()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    if not config.AP_PASS:
        rumps.alert(
            title='DigitalTwinEngine',
            message='AP_PASS is not set.\nEdit live_monitor/.env and set AP_PASS.',
        )
        return

    data_store = DataStore()
    port       = find_free_port(8050)

    # Dash server in background
    threading.Thread(
        target=run_dashboard, args=(data_store, port), daemon=True, name='dash').start()

    # Collector in non-daemon thread so finally block runs on any exit
    collector_thread = threading.Thread(
        target=_collector_loop, args=(data_store,), daemon=False, name='collector')
    collector_thread.start()

    # Ctrl-C: set flag and wait for collector to finish cleanly
    def _sigint(sig, frame):
        global _widget_running
        _widget_running = False
        collector_thread.join(timeout=15)
        raise SystemExit(0)
    signal.signal(signal.SIGINT,  _sigint)
    signal.signal(signal.SIGTERM, _sigint)

    # Open browser after server has a moment to bind
    def _open():
        time.sleep(2.5)
        webbrowser.open(f'http://localhost:{port}')
    threading.Thread(target=_open, daemon=True).start()

    # rumps must run in the main thread
    DTEWidget(data_store, port=port).run()

    # After rumps exits (Quit from menu), wait for collector to finish
    _widget_running = False
    collector_thread.join(timeout=15)


if __name__ == '__main__':
    main()
