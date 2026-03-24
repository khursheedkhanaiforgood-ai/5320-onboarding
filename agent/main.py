"""Entry point for the Extreme Networks 5320 automated onboarding agent."""
import re
import time
import os
import subprocess
from pathlib import Path
import click

from agent.config import load_config
from agent.port_detector import wait_for_port, scan_ports, select_port
from agent.serial_monitor import LogfileMonitor
from agent.state_machine import StateMachine, SwitchState
from agent.console_analyzer import ConsoleAnalyzer, OperatorInstruction
from agent.operator_ui import OperatorUI, console

# Strip ANSI escape sequences when saving captured output to file
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[mGKHF]|\x1b\(B|\r')


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub('', text)


def _wait_for_state(monitor: LogfileMonitor, sm: StateMachine,
                    targets: set, timeout: float = 60.0) -> bool:
    """Drain monitor, run state machine, return True when a target state is reached.
    Returns immediately if already in a target state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if sm.current_state in targets:
            return True
        lines = monitor.get_lines()
        if lines:
            sm.process_lines(lines)
        time.sleep(0.1)
    return False


def _wait_for_settle(monitor: LogfileMonitor, settle: float = 2.5, timeout: float = 30.0):
    """Wait for console activity to START, then wait for it to settle.

    Does not start the settle timer until output has actually been seen —
    so it won't return prematurely if the operator hasn't typed the command yet.
    """
    deadline = time.monotonic() + timeout
    got_activity = False
    last_activity = time.monotonic()
    while time.monotonic() < deadline:
        lines = monitor.get_lines()
        if lines:
            got_activity = True
            last_activity = time.monotonic()
        elif got_activity and (time.monotonic() - last_activity) >= settle:
            return  # had output, now settled
        time.sleep(0.1)


def _find_screen_session(port: str) -> str | None:
    """Return the screen session ID (PID.name) that holds the given serial port."""
    pids = subprocess.run(["lsof", "-t", port], capture_output=True, text=True).stdout.strip().splitlines()
    if not pids:
        return None
    ls = subprocess.run(["screen", "-ls"], capture_output=True, text=True).stdout
    for line in ls.splitlines():
        line = line.strip()
        parts = line.split()
        if not parts:
            continue
        session_id = parts[0]   # e.g. "13864.5320console"
        if session_id.split('.')[0] in pids:
            return session_id
    return None


def _send_to_screen(session_id: str, cmd: str) -> bool:
    """Send a command + Enter to a screen session. Returns True on success."""
    r = subprocess.run(
        ["screen", "-X", "-S", session_id, "stuff", cmd + "\r"],
        capture_output=True,
    )
    return r.returncode == 0


def _run_command(session_id: str | None, monitor: LogfileMonitor,
                 logfile: str, cmd: str, settle: float = 3.0, timeout: float = 60,
                 auto_escape: bool = False) -> str:
    """Auto-send cmd to screen session, wait for output to settle, return captured text.

    If auto_escape=True, watches for --More-- pagination prompts and sends Escape
    to dismiss them automatically rather than waiting for user input.
    """
    while monitor.get_lines():
        pass  # flush stale queue
    start_pos = os.path.getsize(logfile)
    if session_id:
        # Send a blank CR first so the terminal is at a clean prompt,
        # then wait briefly before sending the real command — prevents
        # the first character being dropped by screen's input buffer
        _send_to_screen(session_id, "")
        time.sleep(0.4)
        while monitor.get_lines():
            pass  # flush the echoed blank CR
        start_pos = os.path.getsize(logfile)
        if _send_to_screen(session_id, cmd):
            console.print(f"[dim]  → sent: {cmd}[/dim]")
        else:
            console.print(f"[yellow]  Could not auto-send. Type in the switch window: [bold]{cmd}[/bold][/yellow]")
    else:
        console.print(f"[yellow]  Type in the switch window: [bold]{cmd}[/bold][/yellow]")

    if auto_escape and session_id:
        _wait_for_settle_escape(monitor, logfile, session_id, settle=settle, timeout=timeout)
    else:
        _wait_for_settle(monitor, settle=settle, timeout=timeout)
    return _read_logfile_from(logfile, start_pos)


def _wait_for_settle_escape(monitor: LogfileMonitor, logfile: str, session_id: str,
                             settle: float = 3.0, timeout: float = 90.0):
    """Like _wait_for_settle but sends Escape whenever --More-- appears in the logfile."""
    deadline = time.monotonic() + timeout
    got_activity = False
    last_activity = time.monotonic()
    last_check_pos = os.path.getsize(logfile)

    while time.monotonic() < deadline:
        lines = monitor.get_lines()
        if lines:
            got_activity = True
            last_activity = time.monotonic()

        # Check new logfile content for --More-- prompt
        current_size = os.path.getsize(logfile)
        if current_size > last_check_pos:
            chunk = _read_logfile_from(logfile, last_check_pos)
            last_check_pos = current_size
            if '--more--' in chunk.lower() or '-- more --' in chunk.lower():
                # Send Space to advance to the next page — captures all output
                subprocess.run(
                    ["screen", "-X", "-S", session_id, "stuff", " "],
                    capture_output=True,
                )
                got_activity = True
                last_activity = time.monotonic()

        elif got_activity and (time.monotonic() - last_activity) >= settle:
            return

        time.sleep(0.1)


def _read_logfile_from(logfile: str, start_pos: int) -> str:
    """Read logfile bytes from start_pos to end, return as cleaned text."""
    try:
        with open(logfile, 'rb') as f:
            f.seek(start_pos)
            raw = f.read().decode('utf-8', errors='replace')
        return _strip_ansi(raw)
    except Exception:
        return ""


def _filter_port_config(text: str) -> str:
    """Remove unconfigured/inactive port lines from 'show port configuration' output.

    Keeps header rows, separator rows, and any port line that has an active link
    or a non-default speed/duplex setting. Omits ports that are purely default
    (Auto speed, Auto/Full duplex, no flow control) with no active link.
    """
    lines = text.splitlines()
    result = []
    # Detect the link-state column by finding "Link" or "State" in the header
    link_col = None

    for line in lines:
        stripped = line.strip()

        # Always keep blank lines, headers, and separator rows
        if not stripped or stripped.startswith('=') or stripped.startswith('-'):
            result.append(line)
            continue

        # Try to identify the header row to locate the link-state column
        lower = stripped.lower()
        if 'port' in lower and ('speed' in lower or 'duplex' in lower or 'auto' in lower):
            result.append(line)
            # Record position of "link" or "state" in the header for column detection
            if link_col is None:
                idx = lower.rfind('link')
                if idx == -1:
                    idx = lower.rfind('state')
                if idx != -1:
                    link_col = idx
            continue

        # Check if this looks like a port data line (starts with a port number)
        parts = stripped.split()
        if parts and parts[0].isdigit():
            # If we have a link column, check if that column shows activity
            if link_col is not None and len(line) > link_col:
                link_field = line[link_col:].strip().split()[0] if line[link_col:].strip() else '-'
                # Keep the line only if the link is active
                if link_field.lower() in ('-', 'inactive', 'not present', 'notpresent', 'down', ''):
                    continue  # omit — no active link
            else:
                # Fallback: omit if line ends with ' -' (no link indicator)
                if stripped.endswith(' -') or stripped.endswith('\t-'):
                    continue
            result.append(line)
        else:
            # Non-port data line (sub-headers, notes, flags key) — keep
            result.append(line)

    return '\n'.join(result)


def _parse_switch_info(show_switch_output: str) -> dict:
    """Extract key fields from 'show switch' output."""
    info = {"name": "Unknown", "type": "5320", "firmware": "Unknown", "mac": "Unknown"}
    for line in show_switch_output.splitlines():
        line = line.strip()
        if line.lower().startswith("sysname"):
            info["name"] = line.split(":", 1)[-1].strip() or info["name"]
        elif line.lower().startswith("system type"):
            info["type"] = line.split(":", 1)[-1].strip() or info["type"]
        elif line.lower().startswith("image") or "switch engine" in line.lower():
            if "33." in line or "switch engine" in line.lower():
                info["firmware"] = line.split(":", 1)[-1].strip() or info["firmware"]
        elif line.lower().startswith("system mac") or line.lower().startswith("mac"):
            info["mac"] = line.split(":", 1)[-1].strip() or info["mac"]
    return info


def _build_html_report(
    commands: list[tuple[str, str]],
    sections: list[str],
    xiq_connected: bool,
) -> str:
    """Build a self-contained dark-themed HTML report with clickable tabs."""
    import html as _html
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    switch_info = _parse_switch_info(sections[0] if sections else "")
    xiq_badge = (
        '<span class="badge connected">&#10003; CONNECTED TO XIQ</span>'
        if xiq_connected else
        '<span class="badge not-connected">&#10007; NOT CONFIRMED ON XIQ</span>'
    )

    tab_buttons = ""
    tab_panels = ""
    for i, ((cmd, description), raw) in enumerate(zip(commands, sections)):
        tab_id = f"tab{i}"
        active = "active" if i == 0 else ""
        short = cmd.replace("show ", "").title()
        tab_buttons += f'<button class="tab-btn {active}" onclick="switchTab(\'{tab_id}\')" id="btn-{tab_id}">{short}</button>\n'
        lines = [l for l in raw.splitlines()
                 if not l.startswith('=') and not l.strip().startswith('SHOW ')
                 and l.strip() != description]
        content = _html.escape("\n".join(lines))
        display = "block" if i == 0 else "none"
        tab_panels += f"""
    <div class="tab-panel" id="{tab_id}" style="display:{display}">
      <p class="desc">{description}</p>
      <pre>{content}</pre>
    </div>
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{switch_info['name']} — Config Report</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #0f172a; color: #e2e8f0; display: flex; flex-direction: column; min-height: 100vh; }}
  header {{ background: #1e293b; padding: 20px 32px; border-bottom: 2px solid #7c3aed; flex-shrink: 0; }}
  header h1 {{ color: #a78bfa; font-size: 1.4rem; margin-bottom: 12px; }}
  .meta {{ display: flex; flex-wrap: wrap; gap: 20px; }}
  .meta-item {{ display: flex; flex-direction: column; }}
  .meta-item span:first-child {{ font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.07em; color: #64748b; margin-bottom: 2px; }}
  .meta-item span:last-child {{ font-size: 0.88rem; color: #94a3b8; }}
  .badge {{ padding: 3px 12px; border-radius: 4px; font-size: 0.82rem; font-weight: 600; }}
  .badge.connected {{ background: #166534; color: #86efac; }}
  .badge.not-connected {{ background: #7f1d1d; color: #fca5a5; }}
  .tabs {{ background: #1e293b; border-bottom: 1px solid #334155; padding: 0 32px; display: flex; gap: 4px; flex-wrap: wrap; flex-shrink: 0; }}
  .tab-btn {{ background: transparent; border: none; border-bottom: 3px solid transparent; color: #64748b; padding: 12px 18px; font-size: 0.88rem; cursor: pointer; transition: color 0.15s, border-color 0.15s; white-space: nowrap; }}
  .tab-btn:hover {{ color: #e2e8f0; }}
  .tab-btn.active {{ color: #a78bfa; border-bottom-color: #7c3aed; font-weight: 600; }}
  .content {{ flex: 1; padding: 24px 32px; overflow: auto; }}
  .tab-panel {{ height: 100%; display: flex; flex-direction: column; }}
  p.desc {{ color: #64748b; font-size: 0.88rem; margin-bottom: 14px; }}
  pre {{ background: #0a0f1e; border: 1px solid #334155; border-radius: 6px; padding: 16px 20px;
         font-family: 'Fira Mono', 'Courier New', monospace; font-size: 0.82rem;
         line-height: 1.6; color: #7dd3fc; white-space: pre-wrap; word-break: break-word;
         flex: 1; overflow: auto; }}
</style>
</head>
<body>
<header>
  <h1>Extreme Networks {switch_info['type']} — Configuration Report</h1>
  <div class="meta">
    <div class="meta-item"><span>Switch Name</span><span>{switch_info['name']}</span></div>
    <div class="meta-item"><span>Model</span><span>{switch_info['type']}</span></div>
    <div class="meta-item"><span>Firmware</span><span>{switch_info['firmware']}</span></div>
    <div class="meta-item"><span>MAC</span><span>{switch_info['mac']}</span></div>
    <div class="meta-item"><span>Generated</span><span>{timestamp}</span></div>
    <div class="meta-item"><span>XIQ Status</span><span>{xiq_badge}</span></div>
  </div>
</header>
<div class="tabs">
{tab_buttons}</div>
<div class="content">
{tab_panels}</div>
<script>
  function switchTab(id) {{
    document.querySelectorAll('.tab-panel').forEach(p => p.style.display = 'none');
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.getElementById(id).style.display = 'flex';
    document.getElementById('btn-' + id).classList.add('active');
  }}
</script>
</body>
</html>"""



def _run_onboarded_verification(monitor: LogfileMonitor, sm: StateMachine,
                                ui: OperatorUI, logfile: str, port: str):
    """Verify XIQ status, capture config, save to file, recommend exit."""
    console.print()
    console.print("[bold yellow]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold yellow]")
    console.print("[bold green]  Switch already running EXOS — verifying XIQ status[/bold green]")
    console.print("[bold yellow]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold yellow]")
    console.print()

    # Find screen session for auto-sending commands
    session_id = _find_screen_session(port)
    if session_id:
        console.print(f"[green]Console session found — commands will be sent automatically.[/green]")
    else:
        console.print("[yellow]Screen session not found — type each command manually when shown.[/yellow]")

    # ── 1. Login if needed ───────────────────────────────────────────────────
    if sm.current_state == SwitchState.EXOS_LOGGED_IN:
        console.print("[green]Already logged in.[/green]")
    else:
        console.print("[dim]Waiting for EXOS login prompt...[/dim]")
        _wait_for_state(monitor, sm, {SwitchState.EXOS_LOGIN_PROMPT, SwitchState.EXOS_LOGGED_IN}, timeout=20)
        if sm.current_state != SwitchState.EXOS_LOGGED_IN:
            console.print("[dim]  → sending username: admin[/dim]")
            _run_command(session_id, monitor, logfile, "admin", settle=1.5, timeout=15)
            # Wait to see if login succeeded on username alone (blank password)
            # before sending anything else
            _wait_for_state(monitor, sm, {SwitchState.EXOS_LOGGED_IN}, timeout=10)
            if sm.current_state != SwitchState.EXOS_LOGGED_IN:
                console.print("[dim]  → sending password[/dim]")
                _run_command(session_id, monitor, logfile, "Extreme01!!", settle=2.0, timeout=15)
                _wait_for_state(monitor, sm, {SwitchState.EXOS_LOGGED_IN}, timeout=20)

    # ── 2. XIQ STATUS BANNER — checked before anything else ─────────────────
    console.print("\n[dim]Running show iqagent...[/dim]")
    iqagent_out = _run_command(session_id, monitor, logfile, "show iqagent", settle=2.5, timeout=20)

    xiq_connected = bool(re.search(
        r'(state\s*:?\s*connected'
        r'|IQ Agent Status\s*:?\s*connected'
        r'|connected to ExtremeCloud'
        r'|connected to XIQ'
        r'|This system is connected to ExtremeCloud)',
        _read_logfile_from(logfile, 0), re.IGNORECASE
    ))

    console.print()
    if xiq_connected:
        console.print("[bold green]┌─────────────────────────────────────────────────┐[/bold green]")
        console.print("[bold green]│  ✓  SWITCH IS CONNECTED TO EXTREMECLOUD IQ      │[/bold green]")
        console.print("[bold green]└─────────────────────────────────────────────────┘[/bold green]")
    else:
        console.print("[bold red]┌─────────────────────────────────────────────────┐[/bold red]")
        console.print("[bold red]│  ✗  SWITCH IS NOT CONFIRMED ON EXTREMECLOUD IQ  │[/bold red]")
        console.print("[bold red]└─────────────────────────────────────────────────┘[/bold red]")
        console.print()
        console.print("[dim]── show iqagent raw output ──[/dim]")
        if iqagent_out.strip():
            for line in iqagent_out.strip().splitlines()[:30]:
                console.print(f"  {line}")
        else:
            console.print("  [red](no output captured)[/red]")
        console.print("[dim]────────────────────────────[/dim]")
        console.print()
        confirm = click.prompt("Is this switch showing as Connected in XIQ portal? (yes/no)", default="yes")
        xiq_connected = confirm.lower().startswith("y")
    console.print()

    # ── 3. Capture all XIQ-managed settings — auto-sent ─────────────────────
    CAPTURE_COMMANDS = [
        ("show switch",             "Switch identity, firmware, management IP and gateway"),
        ("show ipconfig",           "IP configuration — DHCP or static, mask, gateway, DNS"),
        ("show vlan detail",        "All VLANs configured by XIQ"),
        ("show port configuration", "Port modes and speeds set by XIQ"),
        ("show management",         "Management access — SSH, SNMP, Telnet, HTTP"),
        ("show configuration",      "Full static running configuration"),
    ]

    console.print()
    console.print("[bold]Capturing switch state — running all commands...[/bold]")

    # Disable EXOS paging — use current command name (old 'disable clipaging' is deprecated)
    _run_command(session_id, monitor, logfile, "disable cli paging", settle=2.0, timeout=10)
    # Extra pause so the info/prompt line is fully flushed before we set start_pos
    # for the first capture command — prevents the fast 'show switch' from being missed
    time.sleep(1.0)

    captured_sections: list[str] = []

    for cmd, description in CAPTURE_COMMANDS:
        auto_esc = cmd == "show port configuration"
        output = _run_command(session_id, monitor, logfile, cmd, settle=4.0, timeout=90,
                              auto_escape=auto_esc)
        if cmd == "show port configuration":
            output = _filter_port_config(output)
        header = f"\n{'='*60}\n  {cmd.upper()}\n  {description}\n{'='*60}\n"
        captured_sections.append(header + output)
        console.print(f"[green]  ✓ {cmd} ({len(output.splitlines())} lines)[/green]")

    # Re-enable paging now that capture is done
    _run_command(session_id, monitor, logfile, "enable cli paging", settle=1.0, timeout=10)

    # ── 4. Build and save HTML report ────────────────────────────────────────
    default_name = f"5320_config_{time.strftime('%Y%m%d_%H%M%S')}.html"
    console.print()
    filename = click.prompt(
        "Save report as (in ~/) — press Enter to accept default, or type a new name",
        default=default_name,
    )
    save_path = Path.home() / filename
    save_path.write_text(
        _build_html_report(CAPTURE_COMMANDS, captured_sections, xiq_connected),
        encoding='utf-8'
    )
    console.print(f"[bold green]Report saved → {save_path}[/bold green]")
    subprocess.run(["open", str(save_path)])

    # ── 5. Final recommendation ──────────────────────────────────────────────
    console.print()
    console.print("[bold yellow]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold yellow]")
    if xiq_connected:
        console.print("[bold green]  Switch is confirmed on XIQ.[/bold green]")
        console.print("  XIQ will handle all ongoing monitoring and configuration.")
        console.print("  [bold]Recommendation: close this agent session.[/bold]")
    else:
        console.print("[bold yellow]  Switch is NOT currently connected to XIQ.[/bold yellow]")
        console.print("  You may want to investigate connectivity before closing.")
    console.print("[bold yellow]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold yellow]")
    console.print()
    console.print("  [bold cyan]1[/bold cyan]  Exit agent       — switch is on XIQ, nothing more to do here")
    console.print("  [bold cyan]2[/bold cyan]  Direct config    — stay connected to push config directly (bypasses XIQ)")
    console.print()

    while True:
        choice = click.prompt("Enter choice", default="1")
        if choice in ("1", "2"):
            break
        console.print("[red]Please enter 1 or 2[/red]")

    if choice == "1":
        console.print("[green]Session complete. You can close both terminal windows.[/green]")
        return False  # signal main to exit
    else:
        console.print(
            "[yellow]Direct config mode.[/yellow] "
            "[dim]Type EXOS commands in the switch window. "
            "Note: changes made here may be overwritten by XIQ on next sync.[/dim]"
        )
        return True  # signal main to continue monitoring


LOGFILE = "/tmp/screenlog.0"


@click.command()
@click.option('--port', default=None, help='Serial port path (auto-detected if not set)')
@click.option('--verbose', is_flag=True, help='Verbose output')
def main(port: str | None, verbose: bool):
    """Extreme Networks 5320 Automated Onboarding Agent.

    Reads the switch console in real-time and guides you through onboarding.
    You type the commands — this agent tells you what to type and why.
    """
    config = load_config(port=port, verbose=verbose)
    ui = OperatorUI()

    console.print("[bold purple]Extreme Networks 5320 Onboarding Agent[/bold purple]")
    console.print("[dim]Reading console output and guiding you through onboarding...[/dim]\n")

    # ── Step 1: Port Detection ───────────────────────────────────────────────
    if config.serial_port:
        detected_port = config.serial_port
        console.print(f"[green]Using specified port: {detected_port}[/green]")
    else:
        ports = scan_ports()
        if ports:
            detected_port = select_port(ports)
        else:
            detected_port = wait_for_port(
                on_status=lambda msg: ui.show_waiting(msg)
            )

    ui.set_port(detected_port)
    console.print(f"[green]Port detected: {detected_port}[/green]")

    # ── Step 2: Open switch console in a new Terminal window ────────────────
    # Kill anything holding the port
    result = subprocess.run(["lsof", "-t", detected_port], capture_output=True, text=True)
    for pid in result.stdout.strip().splitlines():
        subprocess.run(["kill", pid.strip()], capture_output=True)
    time.sleep(1.5)

    # Remove stale logfile
    if os.path.exists(LOGFILE):
        os.remove(LOGFILE)

    # Open a new Terminal window running screen with logging.
    # Regular (non-detached) screen creates screenlog.0 in cwd immediately on start.
    screen_cmd = f"cd /tmp && TERM=vt100 screen -L {detected_port} {config.baud_rate}"
    subprocess.Popen(
        ["osascript", "-e", f'tell application "Terminal" to do script "{screen_cmd}"'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    # Wait for the logfile to appear (up to 20 seconds)
    console.print("[dim]Opening switch console window...[/dim]")
    for _ in range(40):
        if os.path.exists(LOGFILE):
            break
        time.sleep(0.5)
    else:
        console.print(
            "[red]Could not open console window automatically.[/red]\n"
            "[yellow]Please open a new terminal and run:[/yellow]\n"
            f"[bold cyan]  cd /tmp && TERM=vt100 screen -L {detected_port} {config.baud_rate}[/bold cyan]\n"
        )
        click.pause(info="Press Enter here once that screen session is open...")

    console.print(
        "[bold green]Switch console open.[/bold green] "
        "[dim]Type commands in the Terminal window that just appeared.[/dim]\n"
    )

    # ── Step 3: Start Logfile Monitor ───────────────────────────────────────
    monitor = LogfileMonitor(LOGFILE, config.buffer_size)
    monitor.start()

    state_machine = StateMachine()
    analyzer = ConsoleAnalyzer(config.api_key, config.model)

    # ── Step 4: Main Loop ───────────────────────────────────────────────────
    PERIODIC_ANALYSIS_INTERVAL = 30.0  # seconds
    last_analysis_time = time.monotonic()
    monitor_only = False
    verification_done = False  # prevents re-entering after choice 2

    try:
        while True:
            lines = monitor.get_lines()

            if lines:
                ui.add_console_lines(lines)
                transition = state_machine.process_lines(lines)

                if transition:
                    ui.update_state(state_machine.current_state, state_machine.os_context)

                    # ── Already-onboarded detection ──────────────────────────
                    if state_machine.likely_already_onboarded and not verification_done:
                        keep_going = _run_onboarded_verification(
                            monitor, state_machine, ui, LOGFILE, detected_port
                        )
                        verification_done = True
                        if not keep_going:
                            return
                        monitor_only = True
                        last_analysis_time = time.monotonic()
                        continue

                    if monitor_only:
                        continue

                    # State changed — ask Claude what to do
                    instruction = analyzer.analyze(
                        console_buffer=monitor.get_raw_buffer(config.buffer_size),
                        current_state=state_machine.current_state,
                        os_context=state_machine.os_context,
                        time_in_state=state_machine.time_in_state(),
                        boot_complete=state_machine.boot_complete,
                    )
                    ui.show_instruction(instruction)
                    last_analysis_time = time.monotonic()

                if state_machine.current_state == SwitchState.ONBOARDED:
                    console.print("[bold green]\nSwitch successfully onboarded! Check XIQ portal.[/bold green]")
                    break

            # Periodic analysis during long-running states
            elif (time.monotonic() - last_analysis_time) > PERIODIC_ANALYSIS_INTERVAL:
                if state_machine.current_state not in (
                    SwitchState.UNKNOWN, SwitchState.ONBOARDED
                ):
                    instruction = analyzer.analyze(
                        console_buffer=monitor.get_raw_buffer(config.buffer_size),
                        current_state=state_machine.current_state,
                        os_context=state_machine.os_context,
                        time_in_state=state_machine.time_in_state(),
                        boot_complete=state_machine.boot_complete,
                    )
                    ui.show_instruction(instruction)
                    last_analysis_time = time.monotonic()

            time.sleep(0.1)

    except KeyboardInterrupt:
        console.print("\n[yellow]Agent stopped.[/yellow]")
    finally:
        monitor.stop()
        # Kill the screen session and any process holding the serial port
        session_id = _find_screen_session(detected_port)
        if session_id:
            subprocess.run(["screen", "-X", "-S", session_id, "quit"], capture_output=True)
        pids = subprocess.run(["lsof", "-t", detected_port], capture_output=True, text=True).stdout.strip().splitlines()
        for pid in pids:
            subprocess.run(["kill", pid.strip()], capture_output=True)
        console.print("[dim]Console session closed.[/dim]")


if __name__ == "__main__":
    main()
