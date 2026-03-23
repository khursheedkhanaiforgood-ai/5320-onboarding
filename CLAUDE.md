# 5320 Onboarding Agent — Claude Code Instructions

## Project Purpose
AI-assisted Extreme Networks 5320 switch onboarding agent. Reads serial console output in real-time, detects switch state, and instructs a human operator what to do next.

## Architecture
```
port_detector.py     → auto-detects USB-serial ports on Mac
serial_monitor.py    → reads serial output, maintains rolling buffer
state_machine.py     → regex-based state detection (no API calls)
console_analyzer.py  → calls Claude API on state transitions
operator_ui.py       → Rich terminal UI showing instructions to human
main.py              → entry point, wires everything together
```

## Critical Constraints
- Agent is READ-ONLY on the serial port. It NEVER sends commands to the switch.
- Human always types commands in a separate screen/minicom window.
- Regex patterns run locally first — Claude API only called on state transitions.
- Rolling buffer capped at 4000 chars to control token cost.

## Key Switch Facts (Extreme 5320)
- Baud rate: 115200
- Fabric Engine console login: rwa / rwa (admin is blocked by design in Pre-GA)
- Factory reset: delete /intflash/config.cfg → boot
- No dedicated MGMT port — plug any RJ45 into internet network for ZTP+
- After EXOS migration: login admin / blank password

## Dev Setup
```bash
pip install -r requirements.txt
cp .env.example .env
# Add your ANTHROPIC_API_KEY to .env
python -m agent.main
```

## Run
```bash
# Auto-detect port (recommended)
python -m agent.main

# Specify port manually
python -m agent.main --port /dev/cu.usbserial-A9VKJO11

# Verbose mode
python -m agent.main --verbose
```

## Test
```bash
pytest tests/
```

## Global Agents Available
- `planner` — before implementing new features
- `code-reviewer` — after writing code
- `python-reviewer` — after writing Python
