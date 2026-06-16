"""
DigitalTwinEngine — SQLite persistence layer.
Sprint 2a: structured storage for radio_polls, client_polls, knob_change_events.

Design:
  • WAL mode — writer (collector) and reader (report) never block each other
  • write_poll() never raises — storage errors silently logged, loop never crashes
  • Module-level health state — updated by main.py, read by /health Flask route
  • schema_version table — future ALTER TABLE migrations keyed on version number
"""
import sqlite3
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Iterable

_log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# ── Module-level health state (written by main.py, read by /health) ──────────

_health: dict = {
    "status": "starting",
    "last_poll_ts": None,
    "session_id": None,
    "collector_uptime_s": 0,
}
_health_start: float = 0.0


def init_health_clock() -> None:
    global _health_start
    _health_start = time.monotonic()


def update_health(status: str, last_poll_ts: str = None, session_id: int = None) -> None:
    global _health
    _health = {
        "status": status,
        "last_poll_ts": last_poll_ts or _health.get("last_poll_ts"),
        "session_id": session_id or _health.get("session_id"),
        "collector_uptime_s": int(time.monotonic() - _health_start) if _health_start else 0,
    }


def get_health() -> dict:
    return {
        **_health,
        "collector_uptime_s": int(time.monotonic() - _health_start) if _health_start else 0,
    }


# ── RadioMetrics fields stored in SQL (65 data fields) ────────────────────────
# CSV archive keeps all ~100 fields; SQL keeps the queryable/analysis subset.

_RADIO_SQL_FIELDS: list[str] = [
    # Identity
    'channel', 'channel_width_mhz', 'phymode', 'spatial_streams',
    # RF health
    'summary_state', 'noise_floor_dbm', 'crc_error_pct', 'crc_airtime_pct', 'link_score',
    # Airtime snapshot
    'tx_cu_pct', 'rx_cu_pct', 'interference_cu_pct', 'total_cu_pct',
    # Running averages (AP 30s rolling)
    'avg_tx_cu_pct', 'avg_rx_cu_pct', 'avg_interference_cu_pct', 'avg_noise_dbm',
    # TX power
    'tx_power_dbm', 'eirp_dbm',
    # Hardware / config
    'beacon_interval_ms',
    # Throughput (computed from byte-counter deltas)
    'tx_throughput_mbps', 'rx_throughput_mbps',
    # Error + retry rates
    'tx_error_pct', 'tx_retry_pct',
    # Capacity / RRM
    'station_count', 'acsp_channel', 'acsp_channel_cost', 'acsp_neighbor_count',
    # WiFi 6 feature flags (all OFF on AP3000 lab unit — optimizer will target these)
    'ofdma_dl', 'ofdma_ul', 'mu_mimo', 'bss_color', 'twt', 'beamforming',
    'dynamic_chan_width', 'acsp_state',
    # EDCA — full per-AC from show radio profile (calibration knob sources)
    'wmm_cw_min_be', 'wmm_cw_max_be', 'wmm_aifs_be', 'wmm_txop_be',
    'wmm_aifs_vi', 'wmm_txop_vi',
    'wmm_aifs_vo', 'wmm_txop_vo',
    # Policy thresholds — optimizer knob proxies
    'weak_snr_threshold_db', 'interference_switch_pct', 'crc_switch_pct',
    'cu_switch_pct', 'max_acsp_tx_power_dbm', 'power_floor_dbm',
    'lb_airtime_limit_pct', 'dcw_trigger_threshold',
    # Mode flags (change EDCA profile entirely — must know if active during tuning)
    'high_density', 'band_steering_enabled', 'load_balance_enabled', 'safety_net_enabled',
    # BGSCAN (service interruption indicators)
    'bgscan_count', 'bgscan_missed', 'radar_count',
    # Airtime percent (usable form of cumulative-sec counters)
    'rx_airtime_pct', 'tx_airtime_pct',
    # Short-term 10s rolling (tighter window than 30s avg — better for M/D/1 latency proxy)
    'st_tx_cu_pct', 'st_rx_cu_pct', 'st_int_cu_pct', 'st_noise_dbm',
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── SQLiteStore ────────────────────────────────────────────────────────────────

class SQLiteStore:
    """
    Persistent SQLite storage for DigitalTwinEngine sessions.

    Thread safety: WAL mode allows concurrent readers while the collector writes.
    The collector (main thread) is the sole writer. The report generator reads
    after close_session(); the /health route reads health state from module-level
    dict, never touching the DB.

    Error policy: write_poll() and write_knob_event() swallow all exceptions —
    a storage error must never crash the polling loop.
    """

    def __init__(self, db_path: str) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._path = db_path
        self._conn = sqlite3.connect(
            db_path,
            check_same_thread=False,   # collector + report reader share connection
            isolation_level=None,      # autocommit — we manage transactions explicitly
        )
        self._conn.row_factory = sqlite3.Row
        self._apply_pragmas()
        self._create_schema()

    # ── Setup ──────────────────────────────────────────────────────────────────

    def _apply_pragmas(self) -> None:
        c = self._conn
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA cache_size=-8000")   # 8 MB page cache

    def _create_schema(self) -> None:
        def _col_type(f):
            if f in _BOOL_COLS: return 'INTEGER'
            if f in _INT_COLS:  return 'INTEGER'
            if f in _REAL_COLS: return 'REAL'
            return 'TEXT'

        radio_col_defs = "\n".join(
            f"    {f:<35} {_col_type(f)},"
            for f in _RADIO_SQL_FIELDS
        )
        # Strip the trailing comma from the last data column so SQL is valid
        radio_col_defs = radio_col_defs.rstrip(',\n') + '\n'

        self._conn.executescript(f"""
        BEGIN;

        CREATE TABLE IF NOT EXISTS schema_version (
            version  INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            start_ts    TEXT    NOT NULL,
            end_ts      TEXT,
            ap_ip       TEXT,
            ap_mac      TEXT,
            ap_name     TEXT,
            ap_model    TEXT
        );

        CREATE TABLE IF NOT EXISTS radio_polls (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  INTEGER NOT NULL REFERENCES sessions(id),
            ts          TEXT    NOT NULL,
            radio       TEXT    NOT NULL,
            band        TEXT,
{radio_col_defs}
        );

        CREATE INDEX IF NOT EXISTS ix_rp_session_ts  ON radio_polls(session_id, ts);
        CREATE INDEX IF NOT EXISTS ix_rp_radio       ON radio_polls(session_id, radio);

        CREATE TABLE IF NOT EXISTS client_polls (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      INTEGER NOT NULL REFERENCES sessions(id),
            ts              TEXT    NOT NULL,
            radio           TEXT,
            mac             TEXT    NOT NULL,
            ip_addr         TEXT,
            vlan_id         INTEGER,
            snr_db          REAL,
            tx_rate_mbps    REAL,
            rx_rate_mbps    REAL,
            chan_width_mhz  INTEGER,
            auth_mode       TEXT,
            phymode         TEXT,
            station_state   TEXT
        );

        CREATE INDEX IF NOT EXISTS ix_cp_session_ts  ON client_polls(session_id, ts);
        CREATE INDEX IF NOT EXISTS ix_cp_mac         ON client_polls(session_id, mac);

        CREATE TABLE IF NOT EXISTS knob_change_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  INTEGER NOT NULL REFERENCES sessions(id),
            ts          TEXT    NOT NULL,
            knob_name   TEXT    NOT NULL,
            old_value   TEXT,
            new_value   TEXT,
            source      TEXT    -- 'manual' | 'calibrator' | 'operator'
        );

        CREATE INDEX IF NOT EXISTS ix_kce_session_ts ON knob_change_events(session_id, ts);

        -- Raw CLI snapshots — verbatim SSH output for regression / replay testing.
        -- Each poll stores every CLI command's raw text keyed by command_tag.
        -- Replay: read rows for a session, pipe raw_text back through the parser,
        -- compare against stored radio_polls to verify parser parity.
        CREATE TABLE IF NOT EXISTS raw_cli_snapshots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  INTEGER NOT NULL REFERENCES sessions(id),
            ts          TEXT    NOT NULL,
            command_tag TEXT    NOT NULL,
            radio       TEXT,
            raw_text    TEXT    NOT NULL
        );

        CREATE INDEX IF NOT EXISTS ix_rcs_session_ts  ON raw_cli_snapshots(session_id, ts);
        CREATE INDEX IF NOT EXISTS ix_rcs_cmd         ON raw_cli_snapshots(session_id, command_tag);

        CREATE TABLE IF NOT EXISTS pcap_captures (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   INTEGER NOT NULL REFERENCES sessions(id),
            ts           TEXT    NOT NULL,
            interface    TEXT    NOT NULL,
            duration_s   INTEGER NOT NULL,
            filter_desc  TEXT,
            local_path   TEXT    NOT NULL,
            file_bytes   INTEGER,
            trigger      TEXT    NOT NULL DEFAULT 'manual',
            error        TEXT
        );

        CREATE INDEX IF NOT EXISTS ix_pcap_session ON pcap_captures(session_id, ts);

        COMMIT;
        """)

        # Insert schema version if table is empty
        row = self._conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()
        if row[0] == 0:
            self._conn.execute(
                "INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))

    # ── Session lifecycle ──────────────────────────────────────────────────────

    def open_session(self, ap_ip: str, ap_mac: str = None,
                     ap_name: str = '', ap_model: str = '') -> int:
        cur = self._conn.execute(
            "INSERT INTO sessions(start_ts, ap_ip, ap_mac, ap_name, ap_model) "
            "VALUES (?,?,?,?,?)",
            (_now_iso(), ap_ip, ap_mac, ap_name, ap_model),
        )
        return cur.lastrowid

    def update_session_header(self, session_id: int,
                              ap_name: str, ap_model: str) -> None:
        self._conn.execute(
            "UPDATE sessions SET ap_name=?, ap_model=? WHERE id=?",
            (ap_name, ap_model, session_id),
        )

    def close_session(self, session_id: int) -> None:
        self._conn.execute(
            "UPDATE sessions SET end_ts=? WHERE id=?",
            (_now_iso(), session_id),
        )

    def get_session(self, session_id: int) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        return dict(row) if row else None

    # ── Write ──────────────────────────────────────────────────────────────────

    def write_poll(self, session_id: int, radios: Iterable, clients: Iterable) -> None:
        try:
            ts = _now_iso()
            with self._conn:
                # radio rows
                # Build column list once (constant for all radios in a poll)
                _fixed = ['session_id', 'ts', 'radio', 'band']
                _cols  = _fixed + _RADIO_SQL_FIELDS
                _ph    = ', '.join(['?'] * len(_cols))
                _sql   = (f"INSERT INTO radio_polls "
                          f"({', '.join(_cols)}) VALUES ({_ph})")

                for rm in radios:
                    vals = [
                        session_id,
                        getattr(rm, 'ts', ts),
                        getattr(rm, 'radio', ''),
                        getattr(rm, 'band', None),
                    ]
                    for f in _RADIO_SQL_FIELDS:
                        v = getattr(rm, f, None)
                        vals.append(int(v) if isinstance(v, bool) else v)
                    self._conn.execute(_sql, vals)

                # client rows
                for cm in clients:
                    self._conn.execute(
                        "INSERT INTO client_polls"
                        "(session_id,ts,radio,mac,ip_addr,vlan_id,snr_db,"
                        " tx_rate_mbps,rx_rate_mbps,chan_width_mhz,auth_mode,"
                        " phymode,station_state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (session_id,
                         getattr(cm, 'ts', ts),
                         getattr(cm, 'radio', None),
                         getattr(cm, 'mac', ''),
                         getattr(cm, 'ip_addr', None),
                         getattr(cm, 'vlan_id', None),
                         getattr(cm, 'snr_db', None),
                         getattr(cm, 'tx_rate_mbps', None),
                         getattr(cm, 'rx_rate_mbps', None),
                         getattr(cm, 'chan_width_mhz', None),
                         getattr(cm, 'a_mode', None),
                         getattr(cm, 'phymode', None),
                         getattr(cm, 'station_state', None)),
                    )
        except Exception:
            _log.error("write_poll failed (session %d) — polling continues", session_id,
                       exc_info=True)

    def write_knob_event(self, session_id: int, knob_name: str,
                         old_value, new_value, source: str = 'manual') -> None:
        try:
            self._conn.execute(
                "INSERT INTO knob_change_events"
                "(session_id,ts,knob_name,old_value,new_value,source) "
                "VALUES (?,?,?,?,?,?)",
                (session_id, _now_iso(), knob_name,
                 str(old_value) if old_value is not None else None,
                 str(new_value) if new_value is not None else None,
                 source),
            )
        except Exception:
            _log.error("write_knob_event failed", exc_info=True)

    def write_pcap_capture(self, session_id: int, result) -> None:
        """Record a completed (or failed) packet capture burst."""
        try:
            self._conn.execute(
                "INSERT INTO pcap_captures"
                "(session_id,ts,interface,duration_s,filter_desc,"
                " local_path,file_bytes,trigger,error) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (session_id, result.ts, result.interface, result.duration_s,
                 result.filter_desc, result.local_path, result.file_bytes,
                 result.trigger, result.error),
            )
        except Exception:
            _log.error("write_pcap_capture failed", exc_info=True)

    def list_pcap_captures(self, session_id: int) -> list:
        rows = self._conn.execute(
            "SELECT ts,interface,duration_s,filter_desc,local_path,"
            "file_bytes,trigger,error "
            "FROM pcap_captures WHERE session_id=? ORDER BY ts DESC",
            (session_id,),
        ).fetchall()
        keys = ('ts','interface','duration_s','filter_desc','local_path',
                'file_bytes','trigger','error')
        return [dict(zip(keys, r)) for r in rows]

    def write_raw_snapshot(self, session_id: int, command_tag: str,
                           raw_text: str, radio: str = None,
                           ts: str = None) -> None:
        """Store verbatim CLI output for regression / replay testing.

        command_tag identifies which SSH command produced the text
        (e.g. 'show_interface_wifi0', 'show_station', 'show_acsp').
        Never raises — a storage failure must not stop the polling loop.
        """
        try:
            self._conn.execute(
                "INSERT INTO raw_cli_snapshots"
                "(session_id,ts,command_tag,radio,raw_text) VALUES (?,?,?,?,?)",
                (session_id, ts or _now_iso(), command_tag, radio, raw_text),
            )
        except Exception:
            _log.error("write_raw_snapshot failed", exc_info=True)

    # ── Read (for report.py) ───────────────────────────────────────────────────

    def iter_radio_polls(self, session_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM radio_polls WHERE session_id=? ORDER BY ts, radio",
            (session_id,)).fetchall()
        return [dict(r) for r in rows]

    def iter_client_polls(self, session_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM client_polls WHERE session_id=? ORDER BY ts",
            (session_id,)).fetchall()
        return [dict(r) for r in rows]

    def iter_raw_snapshots(self, session_id: int,
                           command_tag: str = None) -> list[dict]:
        """Return raw CLI snapshots for a session, optionally filtered by tag.

        Use for regression testing: read raw_text rows, re-parse, compare
        against stored radio_polls to verify parser correctness after changes.
        """
        if command_tag:
            rows = self._conn.execute(
                "SELECT * FROM raw_cli_snapshots "
                "WHERE session_id=? AND command_tag=? ORDER BY ts",
                (session_id, command_tag)).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM raw_cli_snapshots WHERE session_id=? ORDER BY ts",
                (session_id,)).fetchall()
        return [dict(r) for r in rows]

    def avg_snr_by_ts_radio(self, session_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT ts, radio, AVG(snr_db) AS avg_snr, COUNT(*) AS n_clients "
            "FROM client_polls WHERE session_id=? AND snr_db IS NOT NULL "
            "GROUP BY ts, radio ORDER BY ts",
            (session_id,)).fetchall()
        return [dict(r) for r in rows]

    # ── Close ──────────────────────────────────────────────────────────────────

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


# ── Column type classification (for CREATE TABLE DDL) ─────────────────────────

_BOOL_COLS = frozenset({
    'ofdma_dl', 'ofdma_ul', 'mu_mimo', 'twt', 'beamforming',
    'dynamic_chan_width', 'high_density', 'band_steering_enabled',
    'load_balance_enabled', 'safety_net_enabled',
})

_INT_COLS = frozenset({
    'channel', 'channel_width_mhz', 'spatial_streams', 'beacon_interval_ms',
    'station_count', 'acsp_channel', 'acsp_channel_cost', 'acsp_neighbor_count',
    'bss_color',
    'wmm_cw_min_be', 'wmm_cw_max_be', 'wmm_aifs_be', 'wmm_txop_be',
    'wmm_aifs_vi', 'wmm_txop_vi', 'wmm_aifs_vo', 'wmm_txop_vo',
    'bgscan_count', 'bgscan_missed', 'radar_count',
})

_REAL_COLS = frozenset({
    'noise_floor_dbm', 'crc_error_pct', 'crc_airtime_pct', 'link_score',
    'tx_cu_pct', 'rx_cu_pct', 'interference_cu_pct', 'total_cu_pct',
    'avg_tx_cu_pct', 'avg_rx_cu_pct', 'avg_interference_cu_pct', 'avg_noise_dbm',
    'tx_power_dbm', 'eirp_dbm',
    'tx_throughput_mbps', 'rx_throughput_mbps',
    'tx_error_pct', 'tx_retry_pct',
    'weak_snr_threshold_db', 'interference_switch_pct', 'crc_switch_pct',
    'cu_switch_pct', 'max_acsp_tx_power_dbm', 'power_floor_dbm', 'lb_airtime_limit_pct',
    'rx_airtime_pct', 'tx_airtime_pct',
    'st_tx_cu_pct', 'st_rx_cu_pct', 'st_int_cu_pct', 'st_noise_dbm',
})
# All remaining fields default to TEXT (summary_state, phymode, acsp_state, dcw_trigger_threshold)


# ── Self-test ──────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import tempfile, os
    db = os.path.join(tempfile.mkdtemp(), 'test.db')
    print(f'Testing SQLiteStore at {db}')
    s = SQLiteStore(db)

    sid = s.open_session(ap_ip='192.168.0.12', ap_name='AH-556680', ap_model='AP3000')
    print(f'  open_session → session_id={sid}')

    # minimal RadioMetrics-like object
    class _FakeRM:
        radio = 'wifi1'; band = '5GHz'; ts = _now_iso()
        channel = 149; channel_width_mhz = 80; noise_floor_dbm = -95.0
        crc_error_pct = 0.3; total_cu_pct = 12.0; link_score = 87.5
        tx_throughput_mbps = 22.1; ofdma_dl = False; mu_mimo = False
        def __getattr__(self, _): return None

    class _FakeCM:
        ts = _now_iso(); radio = 'wifi1'; mac = 'aa:bb:cc:dd:ee:ff'
        ip_addr = '192.168.1.5'; vlan_id = 1; snr_db = 38.5
        tx_rate_mbps = 433.0; rx_rate_mbps = 144.0; chan_width_mhz = 80
        a_mode = 'wpa3-sae'; phymode = '11ax'; station_state = 'run'

    s.write_poll(sid, [_FakeRM()], [_FakeCM()])

    polls = s.iter_radio_polls(sid)
    clients = s.iter_client_polls(sid)
    print(f'  radio_polls → {len(polls)} row(s), {len(polls[0])} columns')
    print(f'  client_polls → {len(clients)} row(s)')
    print(f'  channel={polls[0]["channel"]}, link_score={polls[0]["link_score"]}')

    # Raw CLI snapshot
    fake_cli = "show interface wifi1\nNoise floor=-95dBm\nTotal utilization=12\n"
    s.write_raw_snapshot(sid, 'show_interface_wifi1', fake_cli, radio='wifi1')
    snaps = s.iter_raw_snapshots(sid)
    print(f'  raw_cli_snapshots → {len(snaps)} row(s)')
    print(f'  command_tag={snaps[0]["command_tag"]}, radio={snaps[0]["radio"]}')
    assert 'Noise floor' in snaps[0]['raw_text'], "raw_text not stored"

    s.close_session(sid)
    sess = s.get_session(sid)
    print(f'  session end_ts set: {sess["end_ts"] is not None}')

    ver = sqlite3.connect(db).execute("SELECT version FROM schema_version").fetchone()[0]
    print(f'  schema_version={ver}')

    s.close()
    print('PASS')
