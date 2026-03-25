"""Compiled regex patterns for switch state and OS context detection."""
import re
from agent.state_machine import SwitchState, OSContext

# State detection patterns — ordered by priority (first match wins within a state)
STATE_PATTERNS: dict[SwitchState, list[re.Pattern]] = {
    SwitchState.UBOOT: [
        re.compile(r'U-Boot\s+\d'),
        re.compile(r'Hit any key to stop autoboot'),
        re.compile(r'Running bootcommand'),
    ],
    SwitchState.BOOT_LOG_SCROLLING: [
        re.compile(r'Starting kernel'),
        re.compile(r'Loading KLM'),
        re.compile(r'Starting vsp application'),
        re.compile(r'LifeCycle: INFO'),
        re.compile(r'Partitioning Disk Device'),
    ],
    SwitchState.FE_LOGIN_PROMPT: [
        re.compile(r'^Login:\s*$', re.MULTILINE),
        re.compile(r'Login:\s*$'),
    ],
    SwitchState.FE_LOGIN_BLOCKED: [
        re.compile(r'Blocked unauthorized ACLI access'),
        re.compile(r'Lock out for \d+ seconds'),
        re.compile(r'Maximum number of login attempts reached'),
        re.compile(r'Error: Bad Login/Password'),
    ],
    SwitchState.FE_PASSWORD_CHANGE: [
        re.compile(r'Enter the New password'),
        re.compile(r'initial attempt using the default password'),
    ],
    SwitchState.FE_LOGGED_IN: [
        re.compile(r'FabricEngine:\d+>\s*$'),
    ],
    SwitchState.FE_PRIVILEGED: [
        re.compile(r'FabricEngine:\d+#\s*$'),
    ],
    SwitchState.ZTD_MODE: [
        re.compile(r'Zero Touch Deployment Mode'),
        re.compile(r'ZTP\+ is enabled'),
        re.compile(r'Booted with the default zero-touch'),
    ],
    SwitchState.DHCP_ACQUIRING: [
        re.compile(r'Starting DHCP Client'),
        re.compile(r'DHCP Address .+ added to interface'),
    ],
    SwitchState.XIQ_CONNECTING: [
        re.compile(r'Connecting to openapi server'),
        re.compile(r'IQAgent successfully connected to XIQ'),
        re.compile(r'Cloud IQ Agent is connected'),
    ],
    SwitchState.FIRMWARE_DOWNLOADING: [
        re.compile(r'Downloading .+\.xos started'),
        re.compile(r'Downloading of Image file is done'),
        re.compile(r'switch image .+ install succeeded'),
    ],
    SwitchState.FIRMWARE_INSTALLING: [
        re.compile(r'Chassis reboot initiated from ExtremeCloud IQ'),
        re.compile(r'Automated install of SwitchEngine'),
        re.compile(r'auto_install_exos'),
    ],
    SwitchState.EXOS_BOOT: [
        re.compile(r'Starting Extreme Networks Switch Engine'),
        re.compile(r'Authentication Service.*now available for login'),
    ],
    SwitchState.EXOS_LOGIN_PROMPT: [
        re.compile(r'^login:\s*$', re.MULTILINE | re.IGNORECASE),
    ],
    SwitchState.EXOS_SETUP_WIZARD: [
        re.compile(r'Would you like to.*\[y/N'),
        re.compile(r'Would you like to.*\[Y/n'),
        re.compile(r'default state.*management connectivity'),
        re.compile(r'enter failsafe user name'),
    ],
    SwitchState.EXOS_LOGGED_IN: [
        re.compile(r'SwitchEngine\.\d+\s+[#>]'),
        re.compile(r'\*\s+\S+SwitchEngine'),
    ],
    SwitchState.ONBOARDED: [
        re.compile(r'Configuration saved.*successfully'),
        re.compile(r'saved to primary\.cfg successfully'),
    ],
}

# OS context detection — runs independently of state
OS_PATTERNS: dict[OSContext, list[re.Pattern]] = {
    OSContext.FABRIC_ENGINE: [
        re.compile(r'FabricEngine'),
        re.compile(r'Fabric Engine'),
        re.compile(r'VOSS'),
        re.compile(r'vsp application'),
    ],
    OSContext.EXOS: [
        re.compile(r'SwitchEngine'),
        re.compile(r'Switch Engine'),
        re.compile(r'ExtremeXOS'),
        re.compile(r'summit_arm'),
    ],
}

# Boot complete marker — agent should wait for this before instructing login
BOOT_COMPLETE_PATTERNS = [
    re.compile(r'Boot sequence successful'),
    re.compile(r'The system is ready'),
    re.compile(r'Authentication Service.*now available for login'),
]

# Lockout timer pattern — extract seconds
LOCKOUT_PATTERN = re.compile(r'Lock out for (\d+) seconds')
