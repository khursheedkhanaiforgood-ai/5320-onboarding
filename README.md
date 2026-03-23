# Extreme Networks 5320 Onboarding Agent

AI-assisted console agent for deploying Extreme Networks 5320 switches from scratch.
Reads serial console output in real-time and guides a human operator step-by-step through every phase.

---

## How It Works

```
You type commands in a second terminal window (screen session).
This agent watches that console and tells you exactly what to type and why.
The agent is READ-ONLY — it never touches the switch itself.
```

---

## Prerequisites

| Requirement | Notes |
|---|---|
| macOS (Apple Silicon or Intel) | Linux also works — port patterns differ (see below) |
| Python 3.11 or newer | `python3 --version` |
| USB-C serial console cable | e.g. FTDI-based, Prolific PL2303 |
| Anthropic API key | Get one at console.anthropic.com |
| Internet connection on the Mac | For Claude API calls |

---

## Setup — Step by Step

### 1. Clone the repo and switch to the agent branch

```bash
git clone https://github.com/khursheedkhanaiforgood-ai/5320-onboarding.git
cd 5320-onboarding
git checkout feature/auto-deploy-agent
```

### 2. Create and activate a Python virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

> You must activate this environment every time you open a new terminal session before running the agent.

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure your environment

```bash
cp .env.example .env
```

Open `.env` in any editor and fill in:

```ini
# Your Anthropic API key — required
ANTHROPIC_API_KEY=sk-ant-api03-...

# Leave SERIAL_PORT blank for auto-detection (recommended)
SERIAL_PORT=

# Baud rate — always 115200 for Extreme 5320
SERIAL_BAUD=115200

# Claude model — latest Sonnet recommended for speed + accuracy
CLAUDE_MODEL=claude-sonnet-4-6

# Console buffer sent to Claude (characters)
BUFFER_SIZE=4000
```

> `.env` is in `.gitignore` and must never be committed. Your API key stays local.

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
python -m agent.main
```

The agent will:
- Auto-detect the USB-serial port (or prompt you if multiple are found)
- Print the exact `screen` command to open in a second terminal
- Start watching the console and guiding you

**Optional flags:**

```bash
# Specify port manually
python -m agent.main --port /dev/cu.usbserial-A9VKJO11

# Verbose mode (shows raw pattern matching)
python -m agent.main --verbose
```

### 7. Open a second terminal for the screen session

The agent will print this command — copy it exactly:

```bash
TERM=vt100 screen /dev/cu.usbserial-XXXXXXXX 115200
```

This is where you type commands when the agent instructs you.

---

## Linux port names

On Linux, serial ports use different names. Update `SERIAL_PORT` in `.env`:

```ini
SERIAL_PORT=/dev/ttyUSB0
# or
SERIAL_PORT=/dev/ttyACM0
```

You may also need to add your user to the `dialout` group:
```bash
sudo usermod -aG dialout $USER
# log out and back in for this to take effect
```

---

## Already-Onboarded Switch Detection

If you connect to a switch that is already running EXOS (fully onboarded), the agent detects this automatically — it checks whether it reached an EXOS active state without seeing any ZTP+/DHCP/XIQ states that indicate onboarding in progress.

When detected, you will be prompted:

```
Switch detected: already running EXOS

  1  Monitor only   — watch console activity, no onboarding guidance
  2  Re-onboard     — guide through factory reset → ZTP+ → EXOS from scratch
  3  Exit           — quit the agent
```

Choose **1** to safely observe a live switch, **2** to start a fresh deployment, or **3** to quit.

---

## Running the Tests

```bash
python -m pytest tests/ -v
```

Expected output: **18 passed** covering all 20 state-machine states and port detection.

---

## Onboarding Reference (what the agent guides you through)

| Phase | What happens |
|---|---|
| Boot | U-Boot → kernel → Fabric Engine login prompt |
| Login | `rwa` / `rwa` (admin is blocked in FE Pre-GA by design) |
| Privilege | `enable` |
| Factory reset | `delete /intflash/config.cfg` → `boot` |
| ZTP+ | Switch boots into Zero Touch Deployment, DHCP acquires address |
| XIQ | IQAgent connects to ExtremeCloud IQ, EXOS firmware pushed automatically |
| EXOS boot | Switch reboots into SwitchEngine 33.5.2 |
| EXOS login | `admin` / *(blank password)* |
| Setup wizard | Agent answers each wizard question |
| Save | `save configuration` → switch appears in XIQ portal |

Full session detail: [index.html](index.html) (password: `admin` / `Extreme01!!`)

---

## Switch Reference

| Item | Value |
|---|---|
| Model | 5320-16P-2MXT-2X |
| Initial firmware | Fabric Engine 9.2.0.0_B888 (Pre-GA) |
| Final firmware | EXOS SwitchEngine 33.5.2b118 |
| Console baud rate | **115200** |
| Fabric Engine login | `rwa` / `rwa` |
| EXOS default login | `admin` / *(blank)* |
| Management port | Any RJ45 port (no dedicated MGMT port on this model) |

---

## Repository Layout

```
agent/
  main.py              Entry point — port detection, main loop, already-onboarded prompt
  config.py            Loads config from .env / CLI flags
  port_detector.py     Auto-scans USB-serial ports, guides human if none found
  serial_monitor.py    Read-only serial reader, rolling buffer (never writes to switch)
  state_machine.py     20-state regex engine, already-onboarded detection
  patterns.py          Compiled regex patterns for all states + OS context
  console_analyzer.py  Claude API integration — called on state transitions only
  operator_ui.py       Rich terminal UI — status panel + instruction panel
  prompts/
    system_prompt.md   Full switch knowledge base fed to Claude
tests/
  test_state_machine.py  15 state tests + full sequence test
  test_port_detector.py  Port scan and selection tests
.env.example           Environment variable template
requirements.txt       Python dependencies
CLAUDE.md              Claude Code project instructions
```

---

*Guide produced from a live session on March 23, 2026 — Extreme Networks 5320-16P-2MXT-2X*
*Agent scaffolded with Claude Code*
