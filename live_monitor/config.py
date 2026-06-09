import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

AP_IP            = os.getenv('AP_IP', '192.168.0.12')
AP_USER          = os.getenv('AP_USER', 'admin')
AP_PASS          = os.getenv('AP_PASS', '')
POLL_INTERVAL    = int(os.getenv('POLL_INTERVAL', '5'))
LOG_DIR          = os.getenv('LOG_DIR', os.path.join(os.path.dirname(__file__), 'data'))
DEBUG_CLI        = os.getenv('DEBUG_CLI', 'false').lower() in ('1', 'true', 'yes')

# AP3000 hardware profile — static until ap_profiles DB table is live (Sprint 2)
AP3000_HARDWARE = {
    'model':              'AP3000',
    'wifi_gen':           '802.11ax (WiFi 6)',
    'radios':             ['wifi0', 'wifi1'],
    'spatial_streams':    {'wifi0': 2, 'wifi1': 2},
    'max_chan_width_mhz': {'wifi0': 40, 'wifi1': 80},
    'max_mcs':            11,
    'tx_power_max_dbm':   18,
    'max_clients':        100,
    'bands_ghz':          [2.4, 5.0],
    'features':           {
        'ofdma': True, 'mu_mimo': True, 'twt': True,
        'bss_color': True, 'beamforming': False,
    },
    'radio_profiles': {
        'wifi0': 'radio_ng_11ax-2g',
        'wifi1': 'radio_ng_11ax-5g',
    },
}

RADIO_BANDS = {'wifi0': '2.4GHz', 'wifi1': '5GHz', 'wifi2': '6GHz'}
