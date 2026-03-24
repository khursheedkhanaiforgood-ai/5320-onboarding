# Extreme Networks 5320 Onboarding Agent

AI-assisted serial console agent for deploying and auditing Extreme Networks 5320 switches.
Reads the switch console in real time, detects each phase automatically, and either guides a human operator through onboarding or — if the switch is already running EXOS — autonomously captures its full configuration and presents it as a dark-themed HTML report.

---

## Architectural Principles

These four principles govern every design decision in this codebase:

**Read-only on the wire.**
The agent never opens the serial port for writing. It tails a logfile written by a `screen` session that the human operator controls. This eliminates `[Errno 16] Resource busy` port-conflict errors — only one process (`screen`) ever holds the port exclusively.

**Regex-first, Claude-second.**
A local 20-state machine classifies every console line with compiled regex patterns in microseconds. The Claude API is invoked only on state transitions — roughly once every few minutes during onboarding. This keeps token cost negligible and latency invisible during fast phases like U-Boot and firmware install.

**Human stays in control.**
The agent displays instructions; the human types commands in a separate Terminal window. Auto-send mode (`screen -X stuff`) is used only in the already-onboarded verification flow — a clearly bounded, read-only operation that carries no misconfiguration risk.

**Single rolling buffer.**
Console output is stored in a 4000-character rolling deque. Only this window is sent to Claude — never the full session log. This bounds token spend and keeps context focused on the current switch state.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  PHYSICAL LAYER                                                          │
│                                                                          │
│   ┌──────────────┐   RJ45 console cable   ┌─────────────────────────┐  │
│   │  Extreme     │ ───────────────────────▶│  Mac  /dev/cu.usbserial │  │
│   │  5320 Switch │                         │       USB-C adapter     │  │
│   └──────────────┘                         └────────────┬────────────┘  │
└────────────────────────────────────────────────────────│────────────────┘
                                                         │
┌────────────────────────────────────────────────────────│────────────────┐
│  PROCESS LAYER (macOS)                                  │                │
│                                                         ▼                │
│   ┌─────────────────────────┐   writes    ┌────────────────────────┐   │
│   │  screen -L              │────────────▶│  /tmp/screenlog.0      │   │
│   │  (holds port O_RDWR)    │             │  (rolling logfile)     │   │
│   │                         │             └────────────┬───────────┘   │
│   │  ◀── screen -X stuff ───│◀──────────────────────── │  (auto-send)  │
│   └─────────────────────────┘                          │                │
│          ▲  human types here                           │                │
└──────────│─────────────────────────────────────────────│────────────────┘
           │                                             │
┌──────────│─────────────────────────────────────────────│────────────────┐
│  AGENT LAYER                                           │                │
│                                                        ▼                │
│                                           ┌────────────────────────┐   │
│                                           │  LogfileMonitor        │   │
│                                           │  polls every 50ms      │   │
│                                           └────────────┬───────────┘   │
│                                                        │ lines[]        │
│                                                        ▼                │
│   ┌─────────────────────┐    transition   ┌────────────────────────┐   │
│   │  ConsoleAnalyzer    │◀───────────────│  StateMachine          │   │
│   │  Claude API         │                │  20 regex states       │   │
│   │  (on transition     │                │  _seen_states tracking │   │
│   │   only)             │                │  likely_already_       │   │
│   └──────────┬──────────┘                │  onboarded             │   │
│              │ instruction               └────────────────────────┘   │
│              ▼                                                          │
│   ┌─────────────────────┐                                              │
│   │  OperatorUI         │──▶  human reads instruction, types command   │
│   │  Rich terminal      │                                              │
│   └─────────────────────┘                                              │
└─────────────────────────────────────────────────────────────────────────┘
           │
           │  already-onboarded path
           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  VERIFICATION FLOW                                                       │
│                                                                          │
│   auto-login ──▶ show iqagent ──▶ XIQ banner                           │
│        │                                                                 │
│        ▼                                                                 │
│   disable cli paging                                                     │
│        │                                                                 │
│        ▼                                                                 │
│   show switch  ──▶  show ipconfig  ──▶  show vlan detail               │
│   show port configuration (auto-Space --More--)                         │
│   show management  ──▶  show configuration                              │
│        │                                                                 │
│        ▼                                                                 │
│   _filter_port_config()  ──▶  _build_html_report()  ──▶  open browser  │
└─────────────────────────────────────────────────────────────────────────┘
```

### Fresh Switch Path

The agent guides the operator through all 20 states from U-Boot to `ONBOARDED`. Claude generates plain-English instructions at each state transition explaining exactly what to type and why.

### Already-Onboarded Path

If the switch reaches an EXOS-active state without any ZTP+/DHCP/XIQ/firmware states having been seen — it was already running EXOS when the agent connected. The agent:

1. Auto-logs in as `admin` (with `Extreme01!!` password if blank password is not accepted)
2. Runs `show iqagent` → displays a green or red XIQ connectivity banner
3. Runs `disable cli paging` then auto-sends 6 show commands, paging through `--More--` automatically with Space
4. Filters inactive ports from port configuration output
5. Saves a dark-themed tabbed HTML report to `~/5320_config_YYYYMMDD_HHMMSS.html`
6. Opens the report in the default browser
7. Offers: **Exit** (screen session closed and port released) or **Direct config mode**

---

## Prerequisites

| Requirement | Notes |
|---|---|
| macOS (Apple Silicon or Intel) | Linux also works — serial port names differ (see below) |
| Python 3.11 or newer | `python3 --version` |
| USB-C serial console cable | FTDI-based or Prolific PL2303 |
| Anthropic API key | Required for Claude analysis on state transitions |
| Internet connection | For Claude API calls |

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/khursheedkhanaiforgood-ai/5320-onboarding.git
cd 5320-onboarding
```

### 2. Create and activate a Python virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

> Activate this environment every time you open a new terminal session before running the agent.

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure your environment

```bash
cp .env.example .env
```

Open `.env` and fill in your values:

```ini
# Required
ANTHROPIC_API_KEY=sk-ant-api03-...

# Leave blank for auto-detection (recommended)
SERIAL_PORT=

# Always 115200 for Extreme 5320
SERIAL_BAUD=115200

# Latest Sonnet recommended
CLAUDE_MODEL=claude-sonnet-4-6

# Rolling buffer sent to Claude (characters)
BUFFER_SIZE=4000
```

> `.env` is listed in `.gitignore` and must never be committed. Your API key stays local.

### 5. Connect the switch

1. Plug the USB-C serial cable into your Mac
2. Plug the RJ45 end into the **console port** on the 5320 (labeled CON or CONSOLE)
3. Verify the port appears:

```bash
ls /dev/cu.usbserial-* /dev/cu.usbmodem* 2>/dev/null
```

You should see something like `/dev/cu.usbserial-A9VKJO11`.

### 6. Run the agent

```bash
python3 -m agent.main
```

The agent will:
- Auto-detect the USB-serial port (or prompt if multiple are found)
- Kill any stale screen session holding the port
- Open a new Terminal window running `screen` with logging to `/tmp/screenlog.0`
- Start watching the console and guiding you

**Optional flags:**

```bash
# Specify port manually
python3 -m agent.main --port /dev/cu.usbserial-A9VKJO11

# Verbose output
python3 -m agent.main --verbose
```

---

## What the Agent Does — Step by Step

### Fresh Switch Onboarding

| Phase | State | What Happens |
|---|---|---|
| Boot | `UBOOT` → `BOOT_LOG_SCROLLING` | U-Boot banner → kernel loading |
| FE Login | `FE_LOGIN_PROMPT` | Fabric Engine login prompt appears |
| Login | `FE_LOGIN_BLOCKED` → `FE_LOGGED_IN` | Use `rwa`/`rwa` (admin is blocked in Pre-GA) |
| Privilege | `FE_PRIVILEGED` | Run `enable` |
| Factory reset | — | Delete `/intflash/config.cfg` → `boot` |
| ZTP+ | `ZTD_MODE` | Switch boots into Zero Touch Deployment |
| DHCP | `DHCP_ACQUIRING` | Management address assigned from DHCP |
| XIQ | `XIQ_CONNECTING` | IQAgent connects, EXOS firmware pushed |
| Firmware | `FIRMWARE_DOWNLOADING` → `FIRMWARE_INSTALLING` | EXOS image received, chassis reboot |
| EXOS boot | `EXOS_BOOT` | SwitchEngine 33.5.2 starting |
| EXOS login | `EXOS_LOGIN_PROMPT` → `EXOS_LOGGED_IN` | `admin` / blank password |
| Setup wizard | `EXOS_SETUP_WIZARD` | Agent answers each wizard question |
| Save | `EXOS_SAVE_CONFIG` → `ONBOARDED` | `save configuration` → appears in XIQ portal |

### Already-Onboarded Configuration Capture

The 6 commands run automatically in sequence:

| Command | Tab Label | What It Captures |
|---|---|---|
| `show switch` | Switch | Identity, firmware, management IP, uptime |
| `show ipconfig` | Ipconfig | DHCP/static IP, mask, gateway, DNS |
| `show vlan detail` | Vlan Detail | All VLANs configured by XIQ |
| `show port configuration` | Port Configuration | Port modes and speeds (inactive ports removed) |
| `show management` | Management | SSH, SNMP, Telnet, HTTP access settings |
| `show configuration` | Configuration | Full static running configuration |

---

## Module Reference

| Module | Purpose |
|---|---|
| `main.py` | Entry point — port detection, main loop, already-onboarded verification flow |
| `config.py` | Loads `.env` / CLI flags into a typed config object |
| `port_detector.py` | Auto-scans USB-serial ports on macOS and Linux |
| `serial_monitor.py` | `LogfileMonitor` (tails screen logfile) + `SerialMonitor` (direct serial, fallback) |
| `state_machine.py` | 20-state regex engine, `_seen_states` tracking, `likely_already_onboarded` detection |
| `patterns.py` | Compiled regex patterns for all 20 states + OS context |
| `console_analyzer.py` | Claude API integration — called on state transitions only |
| `operator_ui.py` | Rich terminal UI — status panel + instruction panel |

---

## Switch Reference

| Item | Value |
|---|---|
| Model | 5320-16P-2MXT-2X-SwitchEngine |
| Initial firmware | Fabric Engine 9.2.0.0_B888 (Pre-GA) |
| Final firmware | EXOS SwitchEngine 33.5.2b118 |
| Console baud rate | 115200 |
| Fabric Engine login | `rwa` / `rwa` |
| EXOS default login (fresh) | `admin` / *(blank password)* |
| EXOS login (already onboarded) | `admin` / `Extreme01!!` |
| Management port | Any RJ45 port — no dedicated MGMT port on this model |

---

## Testing

```bash
python3 -m pytest tests/ -v
```

**21 tests — all passing.** Covers:
- All 20 state detections
- `likely_already_onboarded` — three scenarios (detected, not triggered after ZTP+, not triggered before EXOS)
- Full 14-step onboarding sequence in order
- Boot-complete flag lifecycle
- Port scan and selection

---

## Cleanup

When the agent exits (Ctrl-C, choice 1, or any crash), the `finally` block automatically:
- Sends `screen quit` to close the console session — the Terminal window closes
- Runs `lsof -t port | kill` to release the serial port

No manual cleanup needed.

---

## Linux Notes

On Linux, serial ports use different names. Set `SERIAL_PORT` in `.env`:

```ini
SERIAL_PORT=/dev/ttyUSB0
# or
SERIAL_PORT=/dev/ttyACM0
```

You may need to add your user to the `dialout` group:

```bash
sudo usermod -aG dialout $USER
# log out and back in for this to take effect
```

---

## Documentation

| Guide | Description |
|---|---|
| [`docs/deploy.html`](docs/deploy.html) | Step-by-step deployment on a fresh MacBook — from clone to first run |
| [`docs/agent_tutorial.html`](docs/agent_tutorial.html) | Full build tutorial — every prompt, every bug fix, architecture walkthrough |
| [`docs/onboarding.html`](docs/onboarding.html) | Live session record — Fabric Engine factory reset through EXOS migration |

---

## Related Resources

- [Claude Code Best Practices](https://github.com/shanraisshan/claude-code-best-practice)
- [Everything Claude Code](https://github.com/affaan-m/everything-claude-code)
- [Anthropic API Console](https://console.anthropic.com)

---

*Agent built with Claude Code — March 2026*
*Extreme Networks 5320-16P-2MXT-2X — EXOS SwitchEngine 33.5.2b118*
