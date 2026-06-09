"""
CollectorAgent — SSH into AP3000, parse wifi0+wifi1, return structured PollResult.
Confirmed field names from AH-556680 session logs June 5 2026.
"""
import re
import time
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import paramiko


# ── Data contracts ────────────────────────────────────────────────────────────

@dataclass
class RadioMetrics:
    radio:                  str              # 'wifi0' or 'wifi1'
    band:                   str              # '2.4GHz' or '5GHz'
    ts:                     str              # ISO-8601 UTC

    # Identity
    channel:                Optional[int]   = None
    channel_width_mhz:      Optional[int]   = None
    phymode:                Optional[str]   = None

    # RF health
    summary_state:          Optional[str]   = None
    noise_floor_dbm:        Optional[float] = None
    crc_error_pct:          Optional[float] = None   # CRC error rate (short-term)
    crc_airtime_pct:        Optional[float] = None   # CRC error airtime percent

    # Airtime (current snapshot)
    tx_cu_pct:              Optional[float] = None
    rx_cu_pct:              Optional[float] = None
    interference_cu_pct:    Optional[float] = None
    total_cu_pct:           Optional[float] = None

    # Running averages
    avg_tx_cu_pct:          Optional[float] = None
    avg_rx_cu_pct:          Optional[float] = None
    avg_interference_cu_pct:Optional[float] = None
    avg_noise_dbm:          Optional[float] = None

    # TX power
    tx_power_dbm:           Optional[float] = None   # per-chain dBm
    eirp_dbm:               Optional[float] = None   # total EIRP

    # Hardware / config
    spatial_streams:        Optional[int]   = None   # Tx Chain count
    max_clients:            Optional[int]   = None
    beacon_interval_ms:     Optional[int]   = None
    station_count:          Optional[int]   = None   # filled from show station

    # Search knobs — WiFi 6 features (confirmed field names from show interface)
    dynamic_chan_width:     Optional[bool]  = None   # Dynamic channel width=
    ofdma_dl:               Optional[bool]  = None   # HE OFDMA downlink=
    ofdma_ul:               Optional[bool]  = None   # HE OFDMA uplink=
    mu_mimo:                Optional[bool]  = None   # MU-MIMO=
    bss_color:              Optional[int]   = None   # BSS Color= (0=disabled)
    twt:                    Optional[bool]  = None   # TWT=
    short_gi:               Optional[bool]  = None   # Short guard interval=
    beamforming:            Optional[bool]  = None   # Tx beamforming=
    a_mpdu:                 Optional[bool]  = None   # A-MPDU=
    frameburst:             Optional[bool]  = None   # Frameburst=
    dfs_enabled:            Optional[bool]  = None   # DFS=
    acsp_state:             Optional[str]   = None   # ACSP RUN/STOP

    # EDCA (WMM) queues
    cw_min_be:              Optional[int]   = None   # AC=be CWmin
    cw_min_vo:              Optional[int]   = None   # AC=vo CWmin
    cw_min_vi:              Optional[int]   = None   # AC=vi CWmin

    # ACSP (from show acsp channel-info)
    acsp_channel:           Optional[int]   = None
    acsp_channel_cost:      Optional[int]   = None
    acsp_neighbor_count:    Optional[int]   = None

    # Extended identity / config
    radio_mac:          Optional[str]   = None   # radio hardware MAC  (MAC addr=)
    tx_range_m:         Optional[int]   = None   # Tx range in metres
    a_msdu:             Optional[bool]  = None   # A-MSDU aggregation
    spectral_scan:      Optional[bool]  = None   # Spectral scan on/off
    benchmark_11ax:     Optional[int]   = None   # Benchmark 11ax score

    # BGSCAN detail
    bgscan_count:       Optional[int]   = None   # Number of BGSCAN=
    bgscan_requested:   Optional[int]   = None   # Number of BGSCAN requested=
    bgscan_missed:      Optional[int]   = None   # Number of BGSCAN missed=
    radar_count:        Optional[int]   = None   # Number of detected radar signals=

    # Packet counters (raw cumulative; compute error rates from these)
    rx_packets_total:   Optional[int]   = None
    tx_packets_total:   Optional[int]   = None
    rx_pkt_errors:      Optional[int]   = None   # Rx packets … errors=N
    tx_pkt_errors:      Optional[int]   = None   # Tx packets … errors=N (failed frames)
    rx_pkt_dropped:     Optional[int]   = None
    tx_pkt_dropped:     Optional[int]   = None
    tx_error_pct:       Optional[float] = None   # tx_pkt_errors/tx_packets × 100

    # Absolute airtime (seconds, cumulative since boot)
    rx_airtime_sec:     Optional[float] = None   # Rx airtime=N s
    tx_airtime_sec:     Optional[float] = None   # Tx airtime=N s
    crc_airtime_sec:    Optional[float] = None   # CRC error airtime=N s
    rx_airtime_pct:     Optional[float] = None   # Rx airtime percent=
    tx_airtime_pct:     Optional[float] = None   # Tx airtime percent=

    # Short-term (10 s rolling) and snapshot averages
    st_tx_cu_pct:       Optional[float] = None   # Short term means average Tx CU
    st_rx_cu_pct:       Optional[float] = None
    st_int_cu_pct:      Optional[float] = None
    st_noise_dbm:       Optional[float] = None
    snap_tx_cu_pct:     Optional[float] = None   # Snapshot Tx CU
    snap_rx_cu_pct:     Optional[float] = None
    snap_int_cu_pct:    Optional[float] = None
    snap_noise_dbm:     Optional[float] = None

    # Throughput — computed from byte-counter deltas between polls
    tx_bytes_total:     Optional[int]   = None   # cumulative Tx bytes
    rx_bytes_total:     Optional[int]   = None   # cumulative Rx bytes
    tx_throughput_mbps: Optional[float] = None   # Δbytes×8/Δt
    rx_throughput_mbps: Optional[float] = None

    # L2 retries & frame failures
    # Note: AP3000 show interface exposes packet errors (failed frames), not retry rate.
    # tx_error_pct above = frame failure rate. tx_retry_pct may appear on other HiveOS builds.
    tx_retry_pct:       Optional[float] = None   # Tx retry rate % (if AP exposes it)
    tx_failed_pct:      Optional[float] = None   # Tx failed rate % (if AP exposes it)
    tx_retry_count:     Optional[int]   = None
    tx_failed_count:    Optional[int]   = None

    # Composite link quality score 0–100 (computed each poll)
    link_score:         Optional[float] = None

    # ── From show radio profile ───────────────────────────────────────────────

    # Full EDCA per-AC (WMM exponent representation from radio profile)
    wmm_cw_min_be:          Optional[int]   = None   # WMM min CW exponent BE
    wmm_cw_max_be:          Optional[int]   = None   # WMM max CW exponent BE
    wmm_aifs_be:            Optional[int]   = None   # AIFS BE
    wmm_txop_be:            Optional[int]   = None   # TXOP limit µs BE  ← optimizer txopBe
    wmm_aifs_vi:            Optional[int]   = None   # AIFS VI
    wmm_txop_vi:            Optional[int]   = None   # TXOP limit µs VI
    wmm_aifs_vo:            Optional[int]   = None   # AIFS VO
    wmm_txop_vo:            Optional[int]   = None   # TXOP limit µs VO

    # Policy thresholds  ← closest AP coverage for optimizer knobs 7–12
    weak_snr_threshold_db:  Optional[float] = None   # safety-net SNR floor (minRSSI proxy)
    interference_switch_pct:Optional[float] = None   # ACSP interference channel-switch trigger
    crc_switch_pct:         Optional[float] = None   # ACSP CRC channel-switch trigger
    cu_switch_pct:          Optional[float] = None   # ACSP CU channel-switch trigger
    max_acsp_tx_power_dbm:  Optional[float] = None   # Max ACSP TX power ceiling
    power_floor_dbm:        Optional[float] = None   # Minimum TX power
    lb_airtime_limit_pct:   Optional[float] = None   # LB airtime limit (atFair proxy)
    dcw_trigger_threshold:  Optional[str]   = None   # DCW trigger: low/medium/high

    # Mode flags (from radio profile)
    high_density:           Optional[bool]  = None
    band_steering_enabled:  Optional[bool]  = None
    load_balance_enabled:   Optional[bool]  = None
    safety_net_enabled:     Optional[bool]  = None


@dataclass
class ClientMetrics:
    ts:             str
    radio:          str
    mac:            str
    ip_addr:        Optional[str]   = None
    chan:            Optional[int]   = None
    vlan_id:        Optional[int]   = None
    snr_db:         Optional[float] = None
    tx_rate_mbps:   Optional[float] = None
    rx_rate_mbps:   Optional[float] = None
    upid:           Optional[int]   = None
    chan_width_mhz: Optional[int]   = None
    a_mode:         Optional[str]   = None   # wpa2-personal, wpa3-sae, open
    cipher:         Optional[str]   = None   # CCMP, GCMP, TKIP
    a_time_str:     Optional[str]   = None   # HH:MM:SS association time
    phymode:        Optional[str]   = None   # 11ax, 11ac, 11n
    ldpc:           Optional[bool]  = None
    station_state:  Optional[str]   = None   # run, auth, assoc


@dataclass
class PollResult:
    ts:       str
    ap_name:  str
    ap_model: str
    radios:   list = field(default_factory=list)   # list[RadioMetrics]
    clients:  list = field(default_factory=list)   # list[ClientMetrics]
    # list of (command_tag, raw_text, radio_or_None) for raw_cli_snapshots table
    raw_snapshots: list = field(default_factory=list)


# ── Link score ────────────────────────────────────────────────────────────────

def _compute_link_score(m) -> float:
    """
    Composite link quality score 0–100. Four equal 25-pt buckets:
      CRC errors · Retry rate · Failed frames · Channel utilization
    Each bucket: 25 pts if excellent, 12 if fair, 0 if poor.
    Returns None components as neutral (12 pts) so partial data still scores.
    """
    def _bucket(val, excellent, fair):
        if val is None:
            return 12.0           # unknown → neutral
        if val <= excellent:
            return 25.0
        if val <= fair:
            return 12.0
        return 0.0

    crc_pts   = _bucket(m.crc_error_pct,  5.0,  15.0)   # <5% excellent, <15% fair
    retry_pts = _bucket(m.tx_retry_pct,  10.0,  30.0)   # <10% excellent (if available)
    fail_pts  = _bucket(m.tx_error_pct,   0.5,   2.0)   # frame failure: <0.5% exc, <2% fair
    cu_pts    = _bucket(m.total_cu_pct,  50.0,  80.0)   # <50% excellent, <80% fair
    return round(crc_pts + retry_pts + fail_pts + cu_pts, 1)


# ── CollectorAgent ────────────────────────────────────────────────────────────

class CollectorAgent:
    """
    Maintains a persistent SSH shell to the AP.
    Disables pagination once at connect time, then sends show commands each poll.
    """

    PROMPT = '#'
    CMD_TIMEOUT = 8.0    # seconds to wait for prompt after each command
    RECV_CHUNK  = 65535

    def __init__(self, ap_ip: str, username: str, password: str,
                 debug_log: Optional[str] = None):
        self.ap_ip     = ap_ip
        self.username  = username
        self.password  = password
        self._client:    Optional[paramiko.SSHClient] = None
        self._shell:     Optional[paramiko.Channel]   = None
        self.ap_name   = 'unknown'
        self.ap_model  = 'unknown'
        self._debug_fh  = open(debug_log, 'w', buffering=1) if debug_log else None
        # {radio: {'tx': bytes, 'rx': bytes, 'ts': monotonic_float}}
        self._prev_bytes: dict = {}

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self) -> None:
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._client.connect(
            hostname=self.ap_ip,
            username=self.username,
            password=self.password,
            look_for_keys=False,
            allow_agent=False,
            timeout=15,
        )
        self._shell = self._client.invoke_shell(width=220, height=50)
        time.sleep(1.5)
        self._drain()                      # clear login banner
        self._exec('console page 0')       # disable --More-- pagination

    def disconnect(self) -> None:
        try:
            if self._shell:
                self._shell.close()
            if self._client:
                self._client.close()
            if self._debug_fh:
                self._debug_fh.close()
        except Exception:
            pass

    def is_connected(self) -> bool:
        try:
            return (
                self._client is not None
                and self._client.get_transport() is not None
                and self._client.get_transport().is_active()
            )
        except Exception:
            return False

    # ── AP Onboarding ─────────────────────────────────────────────────────────

    def onboard(self) -> dict:
        """Run show version once. Detect hostname, model, firmware."""
        raw = self._exec('show version')
        info = {}
        for line in raw.splitlines():
            if 'HiveOS' in line or 'Version' in line:
                info['firmware'] = line.strip()
            if 'Platform' in line:
                m = re.search(r'Platform\s*[=:]\s*(\S+)', line)
                if m:
                    info['platform'] = m.group(1)
        # Hostname comes from the prompt: AH-556680#
        prompt_match = re.search(r'([\w\-]+)#', raw)
        if prompt_match:
            self.ap_name = prompt_match.group(1)
        self.ap_model = info.get('platform', 'AP3000')
        return info

    # ── Main Poll ─────────────────────────────────────────────────────────────

    def poll(self) -> PollResult:
        ts = datetime.now(timezone.utc).isoformat()
        result = PollResult(ts=ts, ap_name=self.ap_name, ap_model=self.ap_model)

        # Poll each radio
        now = time.monotonic()
        from config import RADIO_BANDS, AP3000_HARDWARE
        _radio_profiles = AP3000_HARDWARE.get('radio_profiles', {})
        for radio, band in RADIO_BANDS.items():
            raw = self._exec(f'show interface {radio}')
            result.raw_snapshots.append((f'show_interface_{radio}', raw, radio))
            if 'unknown keyword' in raw.lower() or 'invalid input' in raw.lower():
                continue  # radio not present on this AP
            metrics = self._parse_interface(raw, radio, band, ts)

            # Radio profile — EDCA params + policy thresholds (static config, polled each cycle)
            profile_name = _radio_profiles.get(radio)
            if profile_name:
                raw_profile = self._exec(f'show radio profile {profile_name}')
                result.raw_snapshots.append((f'show_radio_profile_{radio}', raw_profile, radio))
                if 'unknown keyword' not in raw_profile.lower() and 'invalid' not in raw_profile.lower():
                    self._parse_radio_profile(raw_profile, metrics)

            # Throughput: Δbytes × 8 / Δt_seconds
            prev = self._prev_bytes.get(radio)
            if (prev
                    and metrics.tx_bytes_total is not None
                    and metrics.rx_bytes_total is not None):
                dt = now - prev['ts']
                if dt > 0:
                    tx_delta = metrics.tx_bytes_total - prev['tx']
                    rx_delta = metrics.rx_bytes_total - prev['rx']
                    if tx_delta >= 0:   # guard against counter reset on AP reboot
                        metrics.tx_throughput_mbps = tx_delta * 8 / dt / 1_000_000
                    if rx_delta >= 0:
                        metrics.rx_throughput_mbps = rx_delta * 8 / dt / 1_000_000
            if metrics.tx_bytes_total is not None:
                self._prev_bytes[radio] = {
                    'tx': metrics.tx_bytes_total,
                    'rx': metrics.rx_bytes_total or 0,
                    'ts': now,
                }

            # Link score 0–100 (higher = better)
            # Penalise CRC errors, retries, failed frames, high channel load
            metrics.link_score = _compute_link_score(metrics)

            result.radios.append(metrics)

        # Stations
        raw_sta = self._exec('show station')
        result.raw_snapshots.append(('show_station', raw_sta, None))
        clients = self._parse_stations(raw_sta, ts)
        result.clients = clients

        # Update station count per radio
        for rm in result.radios:
            rm.station_count = sum(1 for c in clients if c.radio == rm.radio)

        # ACSP channel info
        raw_acsp = self._exec('show acsp channel-info')
        result.raw_snapshots.append(('show_acsp_channel_info', raw_acsp, None))
        acsp = self._parse_acsp(raw_acsp)
        for rm in result.radios:
            if rm.radio in acsp:
                rm.acsp_channel      = acsp[rm.radio].get('channel')
                rm.acsp_channel_cost = acsp[rm.radio].get('cost')
                rm.acsp_neighbor_count = acsp[rm.radio].get('neighbor_count')

        return result

    # ── Parsers ───────────────────────────────────────────────────────────────

    def _parse_interface(self, raw: str, radio: str, band: str, ts: str) -> RadioMetrics:
        m = RadioMetrics(radio=radio, band=band, ts=ts)

        def _float(pattern):
            hit = re.search(pattern, raw)
            return float(hit.group(1)) if hit else None

        def _int(pattern):
            hit = re.search(pattern, raw)
            return int(hit.group(1)) if hit else None

        def _bool_en(pattern):
            hit = re.search(pattern, raw, re.IGNORECASE)
            if not hit:
                return None
            return hit.group(1).strip().lower() == 'enabled'

        def _str(pattern):
            hit = re.search(pattern, raw)
            return hit.group(1).strip() if hit else None

        # Summary state
        m.summary_state = _str(r'Summary state=([^;]+);')

        # Channel + width
        # Freq(Chan)=5745Mhz(149);  OR  Freq(Chan)=<ACSP>;
        chan_hit = re.search(r'Freq\(Chan\)=\S*\((\d+)\)', raw)
        m.channel = int(chan_hit.group(1)) if chan_hit else None
        cw_hit = re.search(r'Channel width=(\d+)Mhz', raw, re.IGNORECASE)
        m.channel_width_mhz = int(cw_hit.group(1)) if cw_hit else None

        # Phymode
        m.phymode = _str(r'Phymode=([^;]+);')

        # TX power: One Chain EIRP power=22.00dBm(18dBm + 4.00dBi)
        eirp_hit = re.search(r'One Chain EIRP power=([\d.]+)dBm\(([\d.]+)dBm', raw)
        if eirp_hit:
            m.eirp_dbm    = float(eirp_hit.group(1))
            m.tx_power_dbm = float(eirp_hit.group(2))

        # Noise floor
        m.noise_floor_dbm = _float(r'Noise floor=([-\d.]+)dBm')

        # Spatial streams: Tx Chain=static 2
        ss_hit = re.search(r'Tx Chain=static (\d+)', raw)
        m.spatial_streams = int(ss_hit.group(1)) if ss_hit else None

        # Max clients
        m.max_clients = _int(r'Max clients number=(\d+)')

        # Beacon interval
        m.beacon_interval_ms = _int(r'Beacon interval=(\d+)')

        # Airtime snapshot
        m.tx_cu_pct           = _float(r'Tx utilization=([\d.]+)%')
        m.rx_cu_pct           = _float(r'Rx utilization=([\d.]+)%')
        m.interference_cu_pct = _float(r'Interference utilization=([\d.]+)%')
        m.total_cu_pct        = _float(r'Total utilization=([\d.]+)%')

        # CRC
        m.crc_error_pct   = _float(r'CRC error rate=([\d.]+)%')
        m.crc_airtime_pct = _float(r'CRC error airtime percent=([\d.]+)%')

        # Running averages: Running average Tx CU=3%; Rx CU=3%; Interference CU=0%; Noise=-95dBm
        ra = re.search(
            r'Running average Tx CU=([\d.]+)%.*?Rx CU=([\d.]+)%.*?Interference CU=([\d.]+)%.*?Noise=([-\d.]+)dBm',
            raw, re.DOTALL
        )
        if ra:
            m.avg_tx_cu_pct            = float(ra.group(1))
            m.avg_rx_cu_pct            = float(ra.group(2))
            m.avg_interference_cu_pct  = float(ra.group(3))
            m.avg_noise_dbm            = float(ra.group(4))

        # WiFi 6 feature flags — exact field names from show interface
        # Note: re.MULTILINE needed so ^ anchors match line starts in multi-line output
        m.ofdma_dl         = _bool_en(r'HE OFDMA downlink=(enabled|disabled)')
        m.ofdma_ul         = _bool_en(r'HE OFDMA uplink=(enabled|disabled)')
        m.twt              = _bool_en(r'(?m)^\s*TWT\s*=\s*(enabled|disabled)')
        m.mu_mimo          = _bool_en(r'MU-MIMO=(enabled|disabled)')
        m.dynamic_chan_width= _bool_en(r'Dynamic channel width=(enabled|disabled)')
        m.short_gi         = _bool_en(r'Short guard interval=(enabled|disabled)')
        m.beamforming      = _bool_en(r'Tx beamforming=(enabled|disabled)')
        m.a_mpdu           = _bool_en(r'A-MPDU=(enabled|disabled)')
        m.frameburst       = _bool_en(r'Frameburst=(enabled|disabled)')
        m.dfs_enabled      = _bool_en(r'(?m)^DFS=(enabled|disabled)')

        # BSS Color=0 (0 = disabled)
        bss_hit = re.search(r'BSS Color=(\d+)', raw)
        m.bss_color = int(bss_hit.group(1)) if bss_hit else None

        # ACSP state from BGSCAN/ACSP line
        acsp_hit = re.search(r'ACSP use last selection=(enabled|disabled)', raw, re.IGNORECASE)
        m.acsp_state = acsp_hit.group(1) if acsp_hit else None

        # EDCA CWmin per AC
        cw_be = re.search(r'AC=be;.*?CWmin=(\d+)', raw)
        cw_vo = re.search(r'AC=vo;.*?CWmin=(\d+)', raw)
        cw_vi = re.search(r'AC=vi;.*?CWmin=(\d+)', raw)
        m.cw_min_be = int(cw_be.group(1)) if cw_be else None
        m.cw_min_vo = int(cw_vo.group(1)) if cw_vo else None
        m.cw_min_vi = int(cw_vi.group(1)) if cw_vi else None

        # Radio hardware MAC  (actual line: "MAC addr=00e6:0e55:66a0;")
        mac_hit = re.search(r'\bMAC addr=([0-9a-fA-F]{2}[0-9a-fA-F:]+)', raw)
        m.radio_mac = mac_hit.group(1).rstrip(';') if mac_hit else None

        # Tx range
        m.tx_range_m = _int(r'Tx range=(\d+)m')

        # A-MSDU / A-MPDU limit
        m.a_msdu = _bool_en(r'A-MSDU=(enabled|disabled)')

        # Spectral scan
        ss_hit = re.search(r'Spectral scan=(on|off)', raw, re.IGNORECASE)
        m.spectral_scan = (ss_hit.group(1).lower() == 'on') if ss_hit else None

        # Benchmark score
        m.benchmark_11ax = _int(r'11ax score=(\d+)')

        # BGSCAN detail  (actual: "Number of BGSCAN=6466; Number of BGSCAN requested=7239; ...")
        m.bgscan_count     = _int(r'Number of BGSCAN=(\d+)')
        m.bgscan_requested = _int(r'Number of BGSCAN requested=(\d+)')
        m.bgscan_missed    = _int(r'Number of BGSCAN missed=(\d+)')
        m.radar_count      = _int(r'Number of detected radar signals=(\d+)')

        # Packet counters  (actual: "Rx packets=3667797; errors=  12; dropped=178;")
        rx_pkt = re.search(r'Rx packets=(\d+);\s*errors=\s*(\d+);\s*dropped=\s*(\d+)', raw)
        if rx_pkt:
            m.rx_packets_total = int(rx_pkt.group(1))
            m.rx_pkt_errors    = int(rx_pkt.group(2))
            m.rx_pkt_dropped   = int(rx_pkt.group(3))

        tx_pkt = re.search(r'Tx packets=(\d+);\s*errors=\s*(\d+);\s*dropped=\s*(\d+)', raw)
        if tx_pkt:
            m.tx_packets_total = int(tx_pkt.group(1))
            m.tx_pkt_errors    = int(tx_pkt.group(2))
            m.tx_pkt_dropped   = int(tx_pkt.group(3))
            if m.tx_packets_total > 0:
                m.tx_error_pct = round(m.tx_pkt_errors / m.tx_packets_total * 100, 4)

        # Byte counters (cumulative since boot; delta computed in poll())
        # Actual: "Rx bytes=1497231552 (1.394 GB); Tx bytes=1608668127 (1.498 GB);"
        m.rx_bytes_total = _int(r'Rx bytes=(\d+)')
        m.tx_bytes_total = _int(r'Tx bytes=(\d+)')

        # Absolute airtime seconds  ("Rx airtime=75.23 s; Tx airtime=128.48 s; CRC error airtime=320.35 s;")
        m.rx_airtime_sec  = _float(r'Rx airtime=([\d.]+)\s*s')
        m.tx_airtime_sec  = _float(r'Tx airtime=([\d.]+)\s*s')
        m.crc_airtime_sec = _float(r'CRC error airtime=([\d.]+)\s*s')

        # Airtime percentages  ("Rx airtime percent=0.12%; Tx airtime percent=0.50%;")
        m.rx_airtime_pct = _float(r'Rx airtime percent=([\d.]+)%')
        m.tx_airtime_pct = _float(r'Tx airtime percent=([\d.]+)%')

        # Short-term (10 s rolling) averages
        st = re.search(
            r'Short term means average Tx CU=([\d.]+)%.*?Rx CU=([\d.]+)%'
            r'.*?Interference CU=([\d.]+)%.*?Noise=([-\d.]+)dBm',
            raw, re.DOTALL
        )
        if st:
            m.st_tx_cu_pct  = float(st.group(1))
            m.st_rx_cu_pct  = float(st.group(2))
            m.st_int_cu_pct = float(st.group(3))
            m.st_noise_dbm  = float(st.group(4))

        # Snapshot averages
        snap = re.search(
            r'Snapshot Tx CU=([\d.]+)%.*?Rx CU=([\d.]+)%'
            r'.*?Interference CU=([\d.]+)%.*?Noise=([-\d.]+)dBm',
            raw, re.DOTALL
        )
        if snap:
            m.snap_tx_cu_pct  = float(snap.group(1))
            m.snap_rx_cu_pct  = float(snap.group(2))
            m.snap_int_cu_pct = float(snap.group(3))
            m.snap_noise_dbm  = float(snap.group(4))

        # L2 retry/failed — if AP exposes (not present on AP3000 show interface but kept for other builds)
        m.tx_retry_pct   = _float(r'Tx retry rate=([\d.]+)%')
        m.tx_failed_pct  = _float(r'Tx failed rate=([\d.]+)%')
        m.tx_retry_count = _int(r'Tx retry count=(\d+)')
        m.tx_failed_count= _int(r'Tx failed count=(\d+)')

        return m

    def _parse_radio_profile(self, raw: str, m: RadioMetrics) -> None:
        """
        Parse `show radio profile <name>` into an existing RadioMetrics object.
        Adds EDCA params, policy thresholds, and mode flags not in show interface.
        Actual field format confirmed from AH-556680 June 5 2026.
        """
        def _float(pattern):
            hit = re.search(pattern, raw)
            return float(hit.group(1)) if hit else None

        def _int(pattern):
            hit = re.search(pattern, raw)
            return int(hit.group(1)) if hit else None

        def _bool_en(pattern):
            hit = re.search(pattern, raw, re.IGNORECASE)
            if not hit:
                return None
            return hit.group(1).strip().lower() == 'enabled'

        def _str(pattern):
            hit = re.search(pattern, raw)
            return hit.group(1).strip() if hit else None

        # EDCA per-AC: "AC=be; WMM min CW=4; max CW=6; AIFS=3; txoplimit=0;"
        for ac, (f_min, f_max, f_aifs, f_txop) in (
            ('be', ('wmm_cw_min_be', 'wmm_cw_max_be', 'wmm_aifs_be', 'wmm_txop_be')),
            ('vi', (None,            None,             'wmm_aifs_vi', 'wmm_txop_vi')),
            ('vo', (None,            None,             'wmm_aifs_vo', 'wmm_txop_vo')),
        ):
            hit = re.search(
                rf'AC={ac};\s*WMM min CW=(\d+);\s*max CW=(\d+);\s*AIFS=(\d+);\s*txoplimit=(\d+)',
                raw
            )
            if hit:
                if f_min:  setattr(m, f_min,  int(hit.group(1)))
                if f_max:  setattr(m, f_max,  int(hit.group(2)))
                if f_aifs: setattr(m, f_aifs, int(hit.group(3)))
                if f_txop: setattr(m, f_txop, int(hit.group(4)))

        # Safety-net SNR floor — closest AP3000 proxy for minRSSI/MCS floor admission
        m.weak_snr_threshold_db = _float(r'Weak SNR threshold=([\d.]+)\s*dB')

        # ACSP channel-switch triggers (first occurrence = Interference-Switch block)
        m.interference_switch_pct = _float(r'Interference threshold=([\d.]+)%')
        crc_hit = re.search(r'CRC error threshold=([\d.]+)%', raw)
        m.crc_switch_pct = float(crc_hit.group(1)) if crc_hit else None
        m.cu_switch_pct  = _float(r'Channel utilization threshold=([\d.]+)%')

        # TX power envelope
        m.max_acsp_tx_power_dbm = _float(r'Max ACSP tx power=([\d.]+)dBm')
        m.power_floor_dbm       = _float(r'Power floor=([\d.]+)dBm')

        # Mode flags
        m.high_density          = _bool_en(r'High density=(enabled|disabled)')
        m.band_steering_enabled = _bool_en(r'Band steering=(enabled|disabled)')
        m.load_balance_enabled  = _bool_en(r'Load balance=(enabled|disabled)')
        m.safety_net_enabled    = _bool_en(r'Safety net=(enabled|disabled)')

        # Airtime fairness proxy
        m.lb_airtime_limit_pct  = _float(r'LB station airtime limit=([\d.]+)%')

        # DCW trigger level
        m.dcw_trigger_threshold = _str(r'Trigger threshold=(\w+)')

    def _parse_stations(self, raw: str, ts: str) -> list:
        """
        Parse `show station` output (HiveOS AP3000 format).
        Section header: Ifname=wifi1.1, Ifindex=26, SSID=WC_Seattle-PPSK:
        Data row column order (positional):
          0:MAC  1:IP  2:Chan  3:TxRate  4:RxRate  5:Pow(SNR)  6:A-Mode  7:Cipher
          8:A-Time  9:VLAN  10:Auth  11:UPID  12:Phymode  13:LDPC  ...
          17:Chan-width  ...  -1:Station-State
        """
        clients = []
        current_radio = None

        for line in raw.splitlines():
            ifname = re.match(r'Ifname=(wifi\d)', line)
            if ifname:
                current_radio = ifname.group(1)
                continue

            if current_radio is None:
                continue

            mac_hit = re.match(r'^([0-9a-fA-F]{4}:[0-9a-fA-F]{4}:[0-9a-fA-F]{4})\s+', line)
            if not mac_hit:
                continue

            mac = mac_hit.group(1)
            parts = line.split()

            # SNR from Pow(SNR): -42(48) → 48
            snr_hit = re.search(r'-?\d+\((\d+)\)', line)
            snr = float(snr_hit.group(1)) if snr_hit else None

            # Tx/Rx rates: first two NNNMHz patterns (in first 85 chars)
            rate_hits = re.findall(r'([\d.]+)M\b', line[:85])
            tx_rate = float(rate_hits[0]) if len(rate_hits) >= 1 else None
            rx_rate = float(rate_hits[1]) if len(rate_hits) >= 2 else None

            # VLAN from HH:MM:SS <vlan>
            vlan_hit = re.search(r'\d{2}:\d{2}:\d{2}\s+(\d+)\s+', line)
            vlan = int(vlan_hit.group(1)) if vlan_hit else None

            # UPID after Auth Yes/No
            upid_hit = re.search(r'\d{2}:\d{2}:\d{2}\s+\d+\s+(?:Yes|No)\s+(\d+)', line)
            upid = int(upid_hit.group(1)) if upid_hit else None

            # Chan-width: NNNMHz
            cw_hit = re.search(r'(\d+)MHz\s+(?:Yes|No)', line)
            chan_width = int(cw_hit.group(1)) if cw_hit else None

            # Extended fields (positional)
            ip_addr      = parts[1]  if len(parts) >  1 and re.match(r'\d+\.\d+', parts[1]) else None
            chan         = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
            a_mode       = parts[6]  if len(parts) >  6 else None
            cipher       = parts[7]  if len(parts) >  7 else None
            a_time_str   = parts[8]  if len(parts) >  8 and re.match(r'\d{2}:\d{2}:\d{2}', parts[8]) else None
            phymode      = parts[12] if len(parts) > 12 else None
            ldpc         = (parts[13].lower() == 'yes') if len(parts) > 13 else None
            station_state= parts[-1] if len(parts) >  0 else None

            clients.append(ClientMetrics(
                ts=ts, radio=current_radio, mac=mac,
                ip_addr=ip_addr, chan=chan,
                vlan_id=vlan, snr_db=snr,
                tx_rate_mbps=tx_rate, rx_rate_mbps=rx_rate,
                upid=upid, chan_width_mhz=chan_width,
                a_mode=a_mode, cipher=cipher, a_time_str=a_time_str,
                phymode=phymode, ldpc=ldpc, station_state=station_state,
            ))

        return clients

    def _parse_acsp(self, raw: str) -> dict:
        """
        Parse `show acsp channel-info`.
        Returns {radio: {channel, cost, neighbor_count}}
        """
        result = {}
        current_radio = None
        current_channel = None
        current_cost = None
        neighbor_count = 0

        for line in raw.splitlines():
            radio_hit = re.match(r'^(wifi\d)', line)
            if radio_hit:
                if current_radio:
                    result[current_radio] = {
                        'channel': current_channel,
                        'cost': current_cost,
                        'neighbor_count': neighbor_count,
                    }
                current_radio = radio_hit.group(1)
                current_channel = None
                current_cost = None
                neighbor_count = 0
                continue

            lowest = re.search(r'Lowest cost channel:\s*(\d+),\s*lowest-cost:\s*(\d+)', line)
            if lowest:
                current_channel = int(lowest.group(1))
                current_cost    = int(lowest.group(2))
                continue

            if re.search(r'Channel\s+\d+\s+Cost:', line):
                neighbor_count += 1

        if current_radio:
            result[current_radio] = {
                'channel': current_channel,
                'cost': current_cost,
                'neighbor_count': neighbor_count,
            }

        return result

    # ── SSH helpers ───────────────────────────────────────────────────────────

    def _exec(self, cmd: str) -> str:
        self._shell.send(cmd + '\n')
        output = self._wait_for_prompt()
        if self._debug_fh:
            sep = '─' * 60
            self._debug_fh.write(
                f'\n{sep}\nCMD: {cmd}\n{sep}\n{output}\n'
            )
        return output

    def _wait_for_prompt(self) -> str:
        output = ''
        deadline = time.time() + self.CMD_TIMEOUT
        while time.time() < deadline:
            if self._shell.recv_ready():
                chunk = self._shell.recv(self.RECV_CHUNK).decode('utf-8', errors='replace')
                output += chunk
                # Stop when we see the # prompt on the last line
                last_line = output.rstrip('\n').split('\n')[-1]
                if last_line.strip().endswith('#'):
                    break
            else:
                time.sleep(0.1)
        return output

    def _drain(self) -> None:
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if self._shell.recv_ready():
                self._shell.recv(self.RECV_CHUNK)
            else:
                time.sleep(0.2)
