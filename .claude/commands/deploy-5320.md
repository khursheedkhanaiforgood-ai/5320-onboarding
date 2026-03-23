# /deploy-5320

Launch the 5320 automated onboarding agent.

## What this does
1. Checks for USB-serial ports on Mac (`/dev/cu.usbserial-*`)
2. Validates `.env` configuration (ANTHROPIC_API_KEY)
3. Launches the agent — it will guide you through the full onboarding

## Steps

```bash
# 1. Ensure dependencies installed
pip install -r requirements.txt

# 2. Ensure API key is set
cat .env | grep ANTHROPIC_API_KEY

# 3. Launch agent
python -m agent.main
```

## If console cable not yet connected
The agent will detect no port and guide you to:
1. Connect USB-C to USB-A adapter to your Mac
2. Connect USB-to-Serial (RJ45 rollover) cable to switch console port
3. Agent auto-detects when cable is plugged in and connects

## Open a second terminal for typing commands
The agent is read-only. Open a separate terminal and run:
```bash
TERM=vt100 screen /dev/cu.usbserial-XXXXXXXX 115200
```
The agent will tell you exactly what to type there.
