"""
DigitalTwinEngine — Live Browser Dashboard
Plotly Dash app. Reads from DataStore (thread-safe deque of PollResults).
Run via main.py or widget.py — do not run standalone.
"""
import re
import threading
from collections import deque
from datetime import datetime

from dash import Dash, dcc, html, Input, Output, dash_table
from plotly.subplots import make_subplots
import plotly.graph_objects as go


# ── Thread-safe data store ────────────────────────────────────────────────────

class DataStore:
    MAX_POLLS = 360   # 30 min at 5-second intervals

    def __init__(self):
        self._lock   = threading.Lock()
        self._polls  = deque(maxlen=self.MAX_POLLS)
        self._header: dict = {}

    def push(self, poll) -> None:
        with self._lock:
            self._polls.append(poll)
            self._header = {
                'ap_name':    poll.ap_name,
                'ap_model':   poll.ap_model,
                'ts':         poll.ts,
                'poll_count': len(self._polls),
            }

    def snapshot(self) -> list:
        with self._lock:
            return list(self._polls)

    def header(self) -> dict:
        with self._lock:
            return dict(self._header)


# ── Metric registry ───────────────────────────────────────────────────────────

#  (field_name, label, y_axis_type, default_on)
_GROUPS = {
    'Throughput': [
        ('tx_throughput_mbps',      'Tx Throughput (Mbps)', 'count', True),
        ('rx_throughput_mbps',      'Rx Throughput (Mbps)', 'count', True),
    ],
    'Link Reliability': [
        ('link_score',              'Link Score (0–100)',    'count', True),
        ('crc_error_pct',           'CRC Error %',          'pct',   True),
        ('tx_error_pct',            'Frame Failure %',      'pct',   True),
        ('tx_retry_pct',            'Tx Retry %',           'pct',   False),
        ('crc_airtime_pct',         'CRC Airtime %',        'pct',   False),
    ],
    'RF Health': [
        ('noise_floor_dbm',         'Noise Floor',          'dbm',   True),
    ],
    'Airtime Detail': [
        ('rx_airtime_pct',          'Rx Airtime %',         'pct',   False),
        ('tx_airtime_pct',          'Tx Airtime %',         'pct',   False),
        ('st_tx_cu_pct',            'ST Tx CU %',           'pct',   False),
        ('st_rx_cu_pct',            'ST Rx CU %',           'pct',   False),
        ('snap_tx_cu_pct',          'Snap Tx CU %',         'pct',   False),
        ('snap_rx_cu_pct',          'Snap Rx CU %',         'pct',   False),
    ],
    'Channel Utilization': [
        ('tx_cu_pct',               'Tx CU %',              'pct',   True),
        ('rx_cu_pct',               'Rx CU %',              'pct',   True),
        ('interference_cu_pct',     'Interference CU %',    'pct',   True),
        ('total_cu_pct',            'Total CU %',           'pct',   False),
    ],
    'Running Averages': [
        ('avg_tx_cu_pct',           'Avg Tx CU %',          'pct',   False),
        ('avg_rx_cu_pct',           'Avg Rx CU %',          'pct',   False),
        ('avg_interference_cu_pct', 'Avg Int CU %',         'pct',   False),
        ('avg_noise_dbm',           'Avg Noise',            'dbm',   False),
    ],
    'TX Power': [
        ('tx_power_dbm',            'Tx Power (dBm)',        'dbm',   False),
        ('eirp_dbm',                'EIRP (dBm)',            'dbm',   False),
    ],
    'Capacity & RRM': [
        ('station_count',           'Station Count',         'count', True),
        ('channel_width_mhz',       'Chan Width (MHz)',       'count', False),
        ('acsp_channel_cost',       'ACSP Cost',             'count', False),
        ('acsp_neighbor_count',     'ACSP Neighbors',        'count', False),
        ('bss_color',               'BSS Color',             'count', False),
    ],
    'EDCA Params': [
        ('wmm_txop_be',             'TXOP BE (us)',          'count', False),
        ('wmm_txop_vi',             'TXOP VI (us)',          'count', False),
        ('wmm_txop_vo',             'TXOP VO (us)',          'count', False),
        ('wmm_aifs_be',             'AIFS BE',               'count', False),
        ('wmm_cw_min_be',           'WMM CW-min BE',         'count', False),
        ('wmm_cw_max_be',           'WMM CW-max BE',         'count', False),
    ],
    'Policy Thresholds': [
        ('weak_snr_threshold_db',   'Weak SNR thr (dB)',     'count', False),
        ('interference_switch_pct', 'Int-Switch %',          'pct',   False),
        ('crc_switch_pct',          'CRC-Switch %',          'pct',   False),
        ('cu_switch_pct',           'CU-Switch %',           'pct',   False),
        ('max_acsp_tx_power_dbm',   'Max ACSP Pwr (dBm)',    'dbm',   False),
        ('power_floor_dbm',         'Pwr Floor (dBm)',       'dbm',   False),
        ('lb_airtime_limit_pct',    'LB Airtime Limit %',    'pct',   False),
    ],
}

_METRIC_META = {
    fname: (label, axis)
    for group in _GROUPS.values()
    for fname, label, axis, _ in group
}

_DEFAULT_ON = {
    fname
    for group in _GROUPS.values()
    for fname, _, _, default in group
    if default
}

_BOOL_FLAGS = [
    ('ofdma_dl',              'OFDMA DL'),
    ('ofdma_ul',              'OFDMA UL'),
    ('mu_mimo',               'MU-MIMO'),
    ('dynamic_chan_width',     'Dyn CW'),
    ('twt',                   'TWT'),
    ('short_gi',              'Short GI'),
    ('beamforming',           'Beamform'),
    ('a_mpdu',                'A-MPDU'),
    ('dfs_enabled',           'DFS'),
    ('a_msdu',                'A-MSDU'),
    ('high_density',          'Hi-Dens'),
    ('band_steering_enabled', 'BandSteer'),
    ('load_balance_enabled',  'LoadBal'),
    ('safety_net_enabled',    'SafetyNet'),
]


# ── Definitions ───────────────────────────────────────────────────────────────
# ? = field not reported by this AP hardware
# OFF = feature explicitly disabled  ON = feature enabled

_DEFINITIONS = {
    # Throughput
    'tx_throughput_mbps':       'Tx Throughput — Δ(Tx bytes)×8/Δt between polls. Computed, not from AP directly.',
    'rx_throughput_mbps':       'Rx Throughput — Δ(Rx bytes)×8/Δt between polls. Computed, not from AP directly.',
    # Link Reliability
    'link_score':               'Link Score 0–100. Four 25-pt buckets: CRC (<5%=25, <15%=12), Retry (<10%=25), '
                                'Frame Fail (<0.5%=25), CU (<50%=25). Unknown field = 12 pts (neutral).',
    'crc_error_pct':            'CRC Error Rate — % received frames with CRC errors. '
                                'Source: AP "CRC error rate=". Good <5%, Warning 5–15%, Bad ≥15%.',
    'tx_error_pct':             'Frame Failure Rate — tx_pkt_errors / tx_packets_total × 100. '
                                'Frames the AP sent with no ACK received. Good <0.5%, Bad ≥2%.',
    'tx_retry_pct':             'Tx Retry Rate — % transmitted frames retried before success. '
                                'Not exposed on AP3000 show interface; always ? on this hardware.',
    'crc_airtime_pct':          'CRC Airtime % — fraction of total airtime consumed by errored frames. '
                                'Source: AP "CRC error airtime percent=".',
    # RF Health
    'noise_floor_dbm':          'Noise Floor — ambient RF energy in dBm on the operating channel. '
                                'Lower is better (typical good: –95 dBm). Source: AP "Noise floor=".',
    # Airtime Detail
    'rx_airtime_pct':           'Rx Airtime % — cumulative fraction of time radio was receiving. '
                                'Source: AP "Rx airtime percent=".',
    'tx_airtime_pct':           'Tx Airtime % — cumulative fraction of time radio was transmitting. '
                                'Source: AP "Tx airtime percent=".',
    'st_tx_cu_pct':             'Short-Term Tx CU % — 10-second rolling average Tx CU. '
                                'Source: AP "Short term means average Tx CU=".',
    'st_rx_cu_pct':             'Short-Term Rx CU % — 10-second rolling average Rx CU.',
    'snap_tx_cu_pct':           'Snapshot Tx CU % — instantaneous Tx CU at this poll. '
                                'Source: AP "Snapshot Tx CU=".',
    'snap_rx_cu_pct':           'Snapshot Rx CU % — instantaneous Rx CU at this poll.',
    # Channel Utilization
    'tx_cu_pct':                'Tx CU % — current transmit channel utilization. '
                                'Source: AP "Tx utilization=".',
    'rx_cu_pct':                'Rx CU % — current receive channel utilization. '
                                'Source: AP "Rx utilization=".',
    'interference_cu_pct':      'Interference CU % — airtime consumed by non-WiFi sources '
                                '(radar, BT, microwave). Source: AP "Interference utilization=".',
    'total_cu_pct':             'Total CU % — Tx + Rx + Interference. '
                                'Source: AP "Total utilization=". Good <50%, Warning <80%.',
    # Running Averages
    'avg_tx_cu_pct':            'Avg Tx CU % — long-run rolling average. Source: AP "Running average Tx CU=".',
    'avg_rx_cu_pct':            'Avg Rx CU % — long-run rolling average.',
    'avg_interference_cu_pct':  'Avg Interference CU % — long-run rolling average.',
    'avg_noise_dbm':            'Avg Noise — long-run average noise floor in dBm.',
    # TX Power
    'tx_power_dbm':             'Tx Power — per-chain transmit power in dBm. '
                                'Source: AP "One Chain EIRP power=XX dBm(NNdBm…)".',
    'eirp_dbm':                 'EIRP — Effective Isotropic Radiated Power (chain power + antenna gain). '
                                'Source: AP "One Chain EIRP power=NNdBm".',
    # Capacity & RRM
    'station_count':            'Station Count — clients currently associated to this radio. '
                                'Source: show station, counted per radio.',
    'channel_width_mhz':        'Channel Width — operating bandwidth in MHz (20/40/80/160). '
                                'Source: AP "Channel width=".',
    'acsp_channel_cost':        'ACSP Cost — Automatic Channel Selection cost for the chosen channel. '
                                'Lower = cleaner channel. Source: show acsp channel-info.',
    'acsp_neighbor_count':      'ACSP Neighbors — co-channel APs seen during last ACSP scan.',
    'bss_color':                'BSS Color — 802.11ax spatial reuse tag (1–63). '
                                '0 = disabled. Reduces inter-BSS interference in dense deployments.',
    # EDCA Params (from show radio profile)
    'wmm_txop_be':              'TXOP BE — Transmission Opportunity limit for Best Effort AC in µs. '
                                '0 = unlimited. Optimizer knob txopBe. Source: show radio profile.',
    'wmm_txop_vi':              'TXOP VI — TXOP limit for Video AC in µs. Source: show radio profile.',
    'wmm_txop_vo':              'TXOP VO — TXOP limit for Voice AC in µs. Source: show radio profile.',
    'wmm_aifs_be':              'AIFS BE — Arbitration Inter-Frame Space for Best Effort. '
                                'Larger = more backoff = lower priority. Source: show radio profile.',
    'wmm_cw_min_be':            'CW-min BE — Minimum Contention Window exponent for BE AC. '
                                'Actual CW = 2^n − 1. Source: show radio profile.',
    'wmm_cw_max_be':            'CW-max BE — Maximum Contention Window exponent for BE AC.',
    # Policy Thresholds (from show radio profile)
    'weak_snr_threshold_db':    'Weak SNR Threshold — SNR (dB) below which a client is considered weak. '
                                'Proxy for minRSSI/mcsFloor optimizer knobs.',
    'interference_switch_pct':  'Int-Switch % — ACSP triggers channel change when interference CU exceeds this.',
    'crc_switch_pct':           'CRC-Switch % — ACSP triggers channel change when CRC rate exceeds this.',
    'cu_switch_pct':            'CU-Switch % — ACSP triggers channel change when total CU exceeds this.',
    'max_acsp_tx_power_dbm':    'Max ACSP Tx Power — ceiling for ACSP automatic power adjustment in dBm.',
    'power_floor_dbm':          'Power Floor — minimum TX power in dBm. AP will not transmit below this.',
    'lb_airtime_limit_pct':     'LB Airtime Limit % — per-client airtime cap via load balancing. '
                                'Proxy for atFair optimizer knob.',
}

_FLAG_DEFINITIONS = {
    'ofdma_dl':              'OFDMA DL — 802.11ax downlink OFDMA. AP serves multiple clients '
                             'simultaneously on orthogonal sub-channels.',
    'ofdma_ul':              'OFDMA UL — 802.11ax uplink OFDMA. Multiple clients transmit '
                             'simultaneously on assigned sub-channels.',
    'mu_mimo':               'MU-MIMO — Multi-User MIMO. Simultaneous spatial streams to '
                             'multiple clients using beamforming.',
    'dynamic_chan_width':     'Dyn CW — Dynamic Channel Width. AP narrows bandwidth to 20/40 MHz '
                             'when legacy clients or high interference detected.',
    'twt':                   'TWT — Target Wake Time (802.11ax). Clients sleep between '
                             'scheduled wake windows, reducing power and contention.',
    'short_gi':              'Short GI — Short Guard Interval (400 ns vs 800 ns). '
                             'Increases throughput ~10% in clean RF environments.',
    'beamforming':           'Beamform — Transmit Beamforming. Focuses RF energy toward '
                             'each client using antenna phase steering.',
    'a_mpdu':                'A-MPDU — Aggregate MPDU. Groups multiple frames into one '
                             'transmission burst, reducing per-frame overhead.',
    'dfs_enabled':           'DFS — Dynamic Frequency Selection. Required on radar-protected '
                             '5 GHz channels (52–144). AP monitors and vacates on radar detection.',
    'a_msdu':                'A-MSDU — Aggregate MSDU. Combines multiple MSDUs into one '
                             'MPDU before aggregation, further reducing overhead.',
    'high_density':          'Hi-Dens — High Density mode. Tightens admission thresholds '
                             'and reduces probe/beacon airtime for dense venues.',
    'band_steering_enabled': 'BandSteer — Band Steering. Pushes dual-band clients from '
                             '2.4 GHz to 5 GHz via probe/auth response manipulation.',
    'load_balance_enabled':  'LoadBal — Load Balancing. Redistributes clients across '
                             'radios/APs by airtime utilization.',
    'safety_net_enabled':    'SafetyNet — Safety Net. Disconnects clients below the weak '
                             'SNR threshold to prevent airtime drain on healthy clients.',
}

_CLIENT_COL_DEFS = {
    'Radio':    'Radio interface the client is associated to (wifi0=2.4GHz, wifi1=5GHz).',
    'MAC':      'Client MAC address (may be randomized on iOS/Android).',
    'IP':       'Client IP address (from ARP table).',
    'VLAN':     'VLAN the client is placed into (from PPSK/SSID policy).',
    'Chan':     'Operating channel the client is on.',
    'SNR (dB)': 'Signal-to-Noise Ratio in dB. Good >25 dB, warning 15–25 dB, poor <15 dB.',
    'Tx Mbps':  'Negotiated MCS transmit rate, not actual throughput. 802.11ax max: 2402 Mbps.',
    'Rx Mbps':  'Negotiated MCS receive rate, not actual throughput.',
    'Width':    'Negotiated channel bandwidth (20/40/80/160 MHz).',
    'Phymode':  '802.11 generation: 11ax=WiFi 6, 11ac=WiFi 5, 11n=WiFi 4, 11a/g=legacy.',
    'Auth':     'Authentication mode: wpa3-sae, wpa2-personal, open.',
    'Cipher':   'Encryption cipher: CCMP (AES), GCMP (WPA3), TKIP (legacy).',
    'A-Time':   'Association duration HH:MM:SS since the client joined.',
    'State':    'Station state: run=fully associated and data-forwarding.',
}

_RADIO_COLORS = {'wifi0': '#ff8c00', 'wifi1': '#1e90ff', 'wifi2': '#32cd32'}

# ── Colour palette / CSS ──────────────────────────────────────────────────────

_BG     = '#0e1117'
_CARD   = '#1a1f2e'
_BORDER = '#2d3347'
_TEXT   = '#e0e0e0'
_DIM    = '#8892a4'

_CSS_BASE = {
    'backgroundColor': _BG,
    'color': _TEXT,
    'fontFamily': '"Inter", "Helvetica Neue", Arial, sans-serif',
    'fontSize': '13px',
}


# ── Layout helpers ────────────────────────────────────────────────────────────

def _group_id(name: str) -> str:
    return 'chk-' + re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')


def _group_label(text: str):
    return html.Div(text, style={
        'color': _DIM, 'fontSize': '10px', 'fontWeight': '700',
        'letterSpacing': '0.1em', 'textTransform': 'uppercase',
        'marginTop': '18px', 'marginBottom': '6px',
    })


def _sidebar():
    """
    Sidebar with dcc.Checklist groups.
    Each metric gets a blue ⓘ icon with data-tip attribute.
    dt_tips.js renders a position:fixed floating tooltip on hover —
    this escapes the sidebar's overflow:auto clipping completely.
    """
    items = []

    for group_name, metrics in _GROUPS.items():
        items.append(_group_label(group_name))
        opts = []
        for fname, label, _, _ in metrics:
            defn = _DEFINITIONS.get(fname, '')
            if defn:
                lbl = html.Span([
                    f'  {label} ',
                    html.Span('ⓘ', **{'data-tip': defn}, style={
                        'cursor': 'help',
                        'color': '#4d9eff',
                        'fontSize': '10px',
                        'fontWeight': '700',
                        'verticalAlign': 'middle',
                    }),
                ])
            else:
                lbl = f'  {label}'
            opts.append({'label': lbl, 'value': fname})

        defs = [fname for fname, _, _, d in metrics if d]
        items.append(dcc.Checklist(
            id=_group_id(group_name),
            options=opts,
            value=defs,
            labelStyle={'display': 'flex', 'alignItems': 'center', 'gap': '4px',
                        'cursor': 'pointer', 'color': _TEXT, 'fontSize': '12px'},
            inputStyle={'accentColor': '#4d9eff'},
        ))

    return html.Div(items, style={
        'width': '220px', 'minWidth': '220px',
        'backgroundColor': _CARD,
        'borderRight': f'1px solid {_BORDER}',
        'padding': '16px',
        'overflowY': 'auto',
    })


def _card(children, style=None):
    s = {'backgroundColor': _CARD, 'border': f'1px solid {_BORDER}',
         'borderRadius': '8px', 'padding': '14px', 'marginBottom': '12px'}
    if style:
        s.update(style)
    return html.Div(children, style=s)


def _badge(label: str, value, is_good: bool | None = None,
           is_good_false: bool = False, tooltip: str = ''):
    bad = (is_good is False) or is_good_false
    if is_good is True:
        bg, border, tc = '#1a4731', '#22c55e', '#22c55e'
    elif bad:
        bg, border, tc = '#4a1a1a', '#ef4444', '#ef4444'
    else:
        bg, border, tc = '#1e2535', _BORDER, _TEXT

    # data-tip attribute → dt_tips.js floating tooltip (position:fixed, no clipping)
    extra = {'data-tip': tooltip} if tooltip else {}

    return html.Div([
        html.Span(label, style={'color': _DIM, 'fontSize': '10px',
                                'display': 'block', 'marginBottom': '2px'}),
        html.Span(str(value), style={'color': tc, 'fontWeight': '600',
                                     'fontSize': '14px'}),
    ], style={
        'backgroundColor': bg, 'border': f'1px solid {border}',
        'borderRadius': '6px', 'padding': '6px 10px',
        'minWidth': '90px', 'display': 'inline-block',
        'margin': '3px', 'textAlign': 'center',
        'cursor': 'help' if tooltip else 'default',
    }, **extra)


def _legend_row():
    items = [
        ('?', '#8892a4', 'Field not reported by this AP hardware'),
        ('OFF', '#ef4444', 'Feature explicitly disabled'),
        ('ON', '#22c55e', 'Feature enabled'),
    ]
    spans = []
    for sym, color, tip in items:
        spans.append(html.Span(
            [html.Span(sym, style={'color': color, 'fontWeight': '700'}),
             f' = {tip}'],
            title=tip,
            style={'marginRight': '18px', 'fontSize': '10px', 'color': _DIM},
        ))
    return html.Div(spans, style={'padding': '4px 3px 8px', 'display': 'flex',
                                   'flexWrap': 'wrap'})


# ── Dash app factory ──────────────────────────────────────────────────────────

def create_app(data_store: DataStore) -> Dash:
    app = Dash(
        __name__,
        title='DigitalTwinEngine',
        update_title=None,
        suppress_callback_exceptions=True,
    )

    app.index_string = app.index_string.replace(
        '<body>',
        f'<body style="background:{_BG};color:{_TEXT};margin:0;padding:0;">'
    )

    all_chk_ids = [_group_id(g) for g in _GROUPS]

    app.layout = html.Div([
        # ── Header ──
        html.Div([
            html.Div([
                html.Span('DigitalTwinEngine', style={
                    'fontWeight': '700', 'fontSize': '16px', 'color': '#fff'}),
                html.Span(' · Live AP Monitor', style={
                    'color': _DIM, 'fontSize': '13px'}),
            ]),
            html.Div(id='header-right', style={'color': _DIM, 'fontSize': '12px'}),
        ], style={
            'backgroundColor': _CARD,
            'borderBottom': f'1px solid {_BORDER}',
            'padding': '10px 20px',
            'display': 'flex',
            'alignItems': 'center',
            'justifyContent': 'space-between',
        }),

        # ── Body ──
        html.Div([
            _sidebar(),
            html.Div([
                _card(dcc.Graph(
                    id='main-chart',
                    config={'displayModeBar': True, 'scrollZoom': True},
                    style={'minHeight': '200px'},
                )),
                _card(html.Div(id='status-row')),
                _card([
                    _legend_row(),
                    html.Div(id='feature-flags'),
                ]),
                _card(html.Div(id='client-table')),
            ], id='main-area', style={'flex': '1', 'padding': '12px',
                                       'overflowX': 'hidden'}),
        ], style={'display': 'flex', 'height': 'calc(100vh - 45px)',
                  'overflow': 'hidden'}),

        # Store aggregates all checklist selections — decouples chart callback
        # from checklist count, preventing IndexError on layout changes
        dcc.Store(id='selected-metrics', data=list(_DEFAULT_ON)),
        dcc.Interval(id='interval', interval=5_000, n_intervals=0),
    ], style=_CSS_BASE)

    # ── Callbacks ─────────────────────────────────────────────────────────────

    @app.callback(
        Output('header-right', 'children'),
        Input('interval', 'n_intervals'),
    )
    def _update_header(_n):
        h = data_store.header()
        if not h:
            return 'Waiting for first poll…'
        ts_local = datetime.fromisoformat(h['ts']).astimezone().strftime('%H:%M:%S')
        return (f"{h['ap_name']}  ·  {h['ap_model']}  "
                f"·  Poll #{h['poll_count']}  ·  {ts_local}")

    # Aggregate all checklist values into a single store.
    # The chart callback depends only on this store + interval,
    # so adding/removing checklist groups never causes an IndexError.
    @app.callback(
        Output('selected-metrics', 'data'),
        [Input(cid, 'value') for cid in all_chk_ids],
    )
    def _update_store(*chk_values):
        selected = set()
        for vals in chk_values:
            if vals:
                selected.update(vals)
        return list(selected)

    @app.callback(
        Output('main-chart', 'figure'),
        Input('interval', 'n_intervals'),
        Input('selected-metrics', 'data'),
    )
    def _update_chart(_n, selected_list):
        selected = set(selected_list or [])
        polls    = data_store.snapshot()

        active_radios, radio_data = [], {}
        for radio in ('wifi0', 'wifi1', 'wifi2'):
            rows = [rm for p in polls for rm in p.radios if rm.radio == radio]
            if rows:
                active_radios.append(radio)
                radio_data[radio] = rows

        n_rows = max(len(active_radios), 1)
        _BAND  = {'wifi0': '2.4 GHz', 'wifi1': '5 GHz', 'wifi2': '6 GHz'}
        titles = [f'{r}  ·  {_BAND.get(r, "")}' for r in active_radios] or ['']

        fig = make_subplots(
            rows=n_rows, cols=1,
            shared_xaxes=True,
            subplot_titles=titles,
            specs=[[{'secondary_y': True}]] * n_rows,
            vertical_spacing=0.10,
        )

        if not polls or not selected or not active_radios:
            fig.update_layout(
                **_chart_layout(height=200),
                annotations=[{'text': 'Waiting for first poll…',
                               'x': 0.5, 'y': 0.5, 'xref': 'paper',
                               'yref': 'paper', 'showarrow': False,
                               'font': {'color': _DIM, 'size': 14}}],
            )
            return fig

        for row_idx, radio in enumerate(active_radios, start=1):
            rows  = radio_data[radio]
            color = _RADIO_COLORS.get(radio, '#aaa')
            ts    = [rm.ts for rm in rows]

            for fname in selected:
                if fname not in _METRIC_META:
                    continue
                label, axis = _METRIC_META[fname]
                vals = [getattr(rm, fname, None) for rm in rows]
                if all(v is None for v in vals):
                    continue

                secondary = axis == 'dbm'
                fig.add_trace(
                    go.Scatter(
                        x=ts, y=vals,
                        name=label,
                        legendgroup=label,
                        showlegend=(row_idx == 1),
                        mode='lines+markers',
                        line={'color': color, 'width': 2},
                        marker={'size': 5},
                        connectgaps=False,
                    ),
                    row=row_idx, col=1,
                    secondary_y=secondary,
                )

        chart_h = 220 * n_rows
        fig.update_layout(**_chart_layout(height=chart_h))

        for i in range(1, n_rows + 1):
            fig.update_yaxes(title_text='% / count', secondary_y=False,
                             autorange=True, rangemode='tozero',
                             gridcolor=_BORDER, zeroline=False,
                             row=i, col=1)
            fig.update_yaxes(title_text='dBm', secondary_y=True,
                             autorange=True,
                             gridcolor=_BORDER, zeroline=False,
                             row=i, col=1)

        fig.update_xaxes(gridcolor=_BORDER, showgrid=True)

        for ann in fig.layout.annotations:
            ann.font.color = _TEXT
            ann.font.size  = 12

        return fig

    @app.callback(
        Output('status-row', 'children'),
        Input('interval', 'n_intervals'),
    )
    def _update_status(_n):
        polls = data_store.snapshot()
        if not polls:
            return html.Div('No data yet.', style={'color': _DIM})

        latest = polls[-1]
        badges = []
        for rm in latest.radios:
            badges.append(html.Div(
                f'── {rm.radio}  [{rm.band}]  Ch {rm.channel or "?"}  '
                f'{rm.channel_width_mhz or "?"}MHz',
                style={'color': _DIM, 'fontSize': '11px',
                       'fontWeight': '600', 'margin': '6px 3px 2px'}
            ))

            nf      = f'{rm.noise_floor_dbm:.0f} dBm' if rm.noise_floor_dbm else '?'
            crc     = f'{rm.crc_error_pct:.1f}%'       if rm.crc_error_pct is not None else '?'
            txc     = f'{rm.tx_cu_pct:.0f}%'           if rm.tx_cu_pct is not None else '?'
            rxc     = f'{rm.rx_cu_pct:.0f}%'           if rm.rx_cu_pct is not None else '?'
            intc    = f'{rm.interference_cu_pct:.0f}%' if rm.interference_cu_pct is not None else '?'
            txp     = f'{rm.tx_power_dbm:.0f} dBm'     if rm.tx_power_dbm else '?'
            sta     = str(rm.station_count or 0)

            crc_v   = rm.crc_error_pct   or 0
            retry_v = rm.tx_retry_pct    or 0
            fail_v  = rm.tx_error_pct    or 0
            score_v = rm.link_score

            retry_s = f'{retry_v:.1f}%'    if rm.tx_retry_pct  is not None else '?'
            fail_s  = f'{fail_v:.1f}%'     if rm.tx_error_pct  is not None else '?'
            score_s = f'{score_v:.0f}/100' if score_v          is not None else '?'

            badges += [
                _badge('Link Score', score_s,
                       is_good=(True if (score_v or 0) >= 75 else None),
                       is_good_false=((score_v or 100) < 40),
                       tooltip=_DEFINITIONS['link_score']),
                _badge('Noise Floor', nf,
                       tooltip=_DEFINITIONS['noise_floor_dbm']),
                _badge('CRC Error', crc,
                       is_good=(True if crc_v < 5 else None),
                       is_good_false=(crc_v >= 15),
                       tooltip=_DEFINITIONS['crc_error_pct']),
                _badge('Tx Retry', retry_s,
                       is_good=(True if retry_v < 10 else None),
                       is_good_false=(retry_v >= 30),
                       tooltip=_DEFINITIONS['tx_retry_pct']),
                _badge('Frame Fail', fail_s,
                       is_good=(True if fail_v < 0.5 else None),
                       is_good_false=(fail_v >= 2.0),
                       tooltip=_DEFINITIONS['tx_error_pct']),
                _badge('Tx CU', txc,    tooltip=_DEFINITIONS['tx_cu_pct']),
                _badge('Rx CU', rxc,    tooltip=_DEFINITIONS['rx_cu_pct']),
                _badge('Int CU', intc,  tooltip=_DEFINITIONS['interference_cu_pct']),
                _badge('Tx Power', txp, tooltip=_DEFINITIONS['tx_power_dbm']),
                _badge('Stations', sta, tooltip=_DEFINITIONS['station_count']),
            ]
        return html.Div(badges)

    @app.callback(
        Output('feature-flags', 'children'),
        Input('interval', 'n_intervals'),
    )
    def _update_flags(_n):
        polls = data_store.snapshot()
        if not polls:
            return html.Div('No data yet.', style={'color': _DIM})

        rows = []
        for rm in polls[-1].radios:
            flags = []
            for field, label in _BOOL_FLAGS:
                val     = getattr(rm, field, None)
                tooltip = _FLAG_DEFINITIONS.get(field, '')
                if val is None:
                    flags.append(_badge(f'{rm.radio} {label}', '?', tooltip=tooltip))
                else:
                    flags.append(_badge(f'{rm.radio} {label}', 'ON' if val else 'OFF',
                                        is_good=val, tooltip=tooltip))
            bss = rm.bss_color
            flags.append(_badge(f'{rm.radio} BSS Color',
                                 str(bss) if bss is not None else '?',
                                 is_good=(bss is not None and bss > 0),
                                 tooltip=_DEFINITIONS.get('bss_color', '')))
            rows.append(html.Div(flags))
        return html.Div(rows)

    @app.callback(
        Output('client-table', 'children'),
        Input('interval', 'n_intervals'),
    )
    def _update_clients(_n):
        polls = data_store.snapshot()
        if not polls or not polls[-1].clients:
            return html.Div('No clients associated.', style={'color': _DIM})

        rows = []
        for c in polls[-1].clients:
            rows.append({
                'Radio':    c.radio,
                'MAC':      c.mac,
                'IP':       c.ip_addr or '—',
                'VLAN':     str(c.vlan_id or '—'),
                'Chan':     str(c.chan or '—'),
                'SNR (dB)': f'{c.snr_db:.0f}' if c.snr_db else '—',
                'Tx Mbps':  f'{c.tx_rate_mbps:.0f}' if c.tx_rate_mbps else '—',
                'Rx Mbps':  f'{c.rx_rate_mbps:.0f}' if c.rx_rate_mbps else '—',
                'Width':    f'{c.chan_width_mhz}MHz' if c.chan_width_mhz else '—',
                'Phymode':  c.phymode or '—',
                'Auth':     c.a_mode or '—',
                'Cipher':   c.cipher or '—',
                'A-Time':   c.a_time_str or '—',
                'State':    c.station_state or '—',
            })

        columns = [{'name': k, 'id': k} for k in rows[0].keys()]

        # Column header tooltips — plain strings work in Dash 4.x
        tooltip_header = {
            col: _CLIENT_COL_DEFS[col]
            for col in _CLIENT_COL_DEFS
            if col in {r['name'] for r in columns}
        }

        snr_conditional = [
            {'if': {'filter_query': '{SNR (dB)} > 25', 'column_id': 'SNR (dB)'},
             'color': '#22c55e'},
            {'if': {'filter_query': '{SNR (dB)} <= 15', 'column_id': 'SNR (dB)'},
             'color': '#ef4444'},
        ]

        return dash_table.DataTable(
            data=rows,
            columns=columns,
            tooltip_header=tooltip_header,
            tooltip_delay=400,
            tooltip_duration=None,
            style_header={
                'backgroundColor': '#0e1117', 'color': _DIM,
                'fontWeight': '600', 'fontSize': '11px',
                'border': f'1px solid {_BORDER}',
                'textTransform': 'uppercase', 'letterSpacing': '0.05em',
                'cursor': 'help',
            },
            style_cell={
                'backgroundColor': _CARD, 'color': _TEXT,
                'border': f'1px solid {_BORDER}',
                'fontSize': '12px', 'padding': '7px 10px',
                'fontFamily': '"Inter", Arial, sans-serif',
            },
            style_data_conditional=[
                {'if': {'row_index': 'odd'}, 'backgroundColor': '#1e2535'},
                *snr_conditional,
            ],
        )

    return app


def _chart_layout(height: int = 440) -> dict:
    return {
        'plot_bgcolor':  _CARD,
        'paper_bgcolor': _BG,
        'height':        height,
        'font':          {'color': _TEXT, 'size': 12},
        'legend':        {'bgcolor': _BG, 'bordercolor': _BORDER,
                          'borderwidth': 1, 'font': {'size': 12},
                          'orientation': 'h', 'y': -0.12},
        'hovermode':     'x unified',
        'margin':        {'l': 55, 'r': 55, 't': 30, 'b': 60},
    }


# ── Port helper ───────────────────────────────────────────────────────────────

def find_free_port(start: int = 8050) -> int:
    import socket
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(('', port))
                return port
            except OSError:
                continue
    return start


# ── Entry point ───────────────────────────────────────────────────────────────

def run_dashboard(data_store: DataStore, port: int = 8050) -> None:
    app = create_app(data_store)
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
