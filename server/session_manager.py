"""
SessionManager — async port of main.py orchestration logic.
Runs on the Railway server. Uses SerialProxy instead of LogfileMonitor.
Broadcasts state/instructions to connected web UI clients via ws_ui.
"""
import asyncio
import html as _html
import re
import time
from typing import Callable, Awaitable

from .agent.state_machine import StateMachine, SwitchState
from .agent.console_analyzer import ConsoleAnalyzer
from .serial_proxy import SerialProxy
from . import config as cfg

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[mGKHF]|\x1b\(B|\r')


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub('', text)


# Type alias for broadcast callback
Broadcaster = Callable[[dict], Awaitable[None]]

CAPTURE_COMMANDS = [
    ("show switch",             "Switch identity, firmware, management IP and gateway"),
    ("show ipconfig",           "IP configuration — DHCP or static, mask, gateway, DNS"),
    ("show vlan detail",        "All VLANs configured by XIQ"),
    ("show port configuration", "Port modes and speeds set by XIQ"),
    ("show management",         "Management access — SSH, SNMP, Telnet, HTTP"),
    ("show configuration",      "Full static running configuration"),
]


class SessionManager:
    def __init__(self, proxy: SerialProxy, broadcast: Broadcaster):
        self._proxy = proxy
        self._broadcast = broadcast
        self._sm = StateMachine()
        self._analyzer = ConsoleAnalyzer(cfg.ANTHROPIC_API_KEY, cfg.CLAUDE_MODEL)
        self._verification_done = False
        self._monitor_only = False
        self._last_analysis = time.monotonic()
        self._prompt_events: dict[str, asyncio.Event] = {}
        self._prompt_responses: dict[str, str] = {}
        self._running = True
        self._raw_capture: list[str] = []  # for _run_command capture

    # ── Prompt helpers ────────────────────────────────────────────────────────

    async def _ask_ui(self, prompt_id: str, text: str, options: list[str] = None) -> str:
        """Send a prompt to the web UI and await the user's response."""
        event = asyncio.Event()
        self._prompt_events[prompt_id] = event
        await self._broadcast({
            "type": "prompt",
            "prompt_id": prompt_id,
            "text": text,
            "options": options or [],
        })
        await asyncio.wait_for(event.wait(), timeout=300)
        return self._prompt_responses.get(prompt_id, options[0] if options else "")

    def receive_prompt_response(self, prompt_id: str, value: str):
        """Called by ws_ui when the user responds to a prompt."""
        self._prompt_responses[prompt_id] = value
        if prompt_id in self._prompt_events:
            self._prompt_events[prompt_id].set()

    # ── Wait helpers ──────────────────────────────────────────────────────────

    async def _wait_for_state(self, targets: set, timeout: float = 60.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._sm.current_state in targets:
                return True
            lines = self._proxy.get_lines()
            if lines:
                self._sm.process_lines(lines)
                await self._broadcast({
                    "type": "state_update",
                    "state": self._sm.current_state.name,
                    "os": self._sm.os_context,
                })
            await asyncio.sleep(0.1)
        return False

    async def _wait_for_settle(self, settle: float = 2.5, timeout: float = 30.0):
        deadline = time.monotonic() + timeout
        got_activity = False
        last_activity = time.monotonic()
        while time.monotonic() < deadline:
            lines = self._proxy.get_lines()
            if lines:
                got_activity = True
                last_activity = time.monotonic()
                self._raw_capture.extend(lines)
            elif got_activity and (time.monotonic() - last_activity) >= settle:
                return
            await asyncio.sleep(0.1)

    async def _wait_for_settle_paging(self, settle: float = 4.0, timeout: float = 90.0):
        """Like _wait_for_settle but sends Space on --More-- prompts."""
        deadline = time.monotonic() + timeout
        got_activity = False
        last_activity = time.monotonic()
        buf = ""
        while time.monotonic() < deadline:
            lines = self._proxy.get_lines()
            if lines:
                got_activity = True
                last_activity = time.monotonic()
                self._raw_capture.extend(lines)
                buf += "\n".join(lines)
                if '--more--' in buf.lower() or '-- more --' in buf.lower():
                    await self._proxy.send_command(" ")
                    buf = ""
                    got_activity = True
                    last_activity = time.monotonic()
            elif got_activity and (time.monotonic() - last_activity) >= settle:
                return
            await asyncio.sleep(0.1)

    # ── Command runner ────────────────────────────────────────────────────────

    async def _run_command(self, cmd: str, settle: float = 3.0, timeout: float = 60.0,
                           auto_page: bool = False) -> str:
        """Send command, wait for output to settle, return captured text."""
        # Flush stale queue
        self._proxy.get_lines()
        self._raw_capture = []

        await self._proxy.send_command(cmd)
        await self._broadcast({"type": "log", "text": f"→ sent: {cmd}"})

        if auto_page:
            await self._wait_for_settle_paging(settle=settle, timeout=timeout)
        else:
            await self._wait_for_settle(settle=settle, timeout=timeout)

        return _strip_ansi("\n".join(self._raw_capture))

    # ── Port config filter ────────────────────────────────────────────────────

    def _filter_port_config(self, text: str) -> str:
        lines = text.splitlines()
        result = []
        link_col = None
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith('=') or stripped.startswith('-'):
                result.append(line)
                continue
            lower = stripped.lower()
            if 'port' in lower and ('speed' in lower or 'duplex' in lower or 'auto' in lower):
                result.append(line)
                if link_col is None:
                    idx = lower.rfind('link')
                    if idx == -1:
                        idx = lower.rfind('state')
                    if idx != -1:
                        link_col = idx
                continue
            parts = stripped.split()
            if parts and parts[0].isdigit():
                if link_col is not None and len(line) > link_col:
                    lf = line[link_col:].strip().split()[0] if line[link_col:].strip() else '-'
                    if lf.lower() in ('-', 'inactive', 'not present', 'notpresent', 'down', ''):
                        continue
                else:
                    if stripped.endswith(' -') or stripped.endswith('\t-'):
                        continue
                result.append(line)
            else:
                result.append(line)
        return '\n'.join(result)

    # ── Switch info + HTML report ─────────────────────────────────────────────

    def _parse_switch_info(self, text: str) -> dict:
        info = {"name": "Unknown", "type": "5320", "firmware": "Unknown", "mac": "Unknown"}
        for line in text.splitlines():
            line = line.strip()
            if line.lower().startswith("sysname"):
                info["name"] = line.split(":", 1)[-1].strip() or info["name"]
            elif line.lower().startswith("system type"):
                info["type"] = line.split(":", 1)[-1].strip() or info["type"]
            elif "switch engine" in line.lower() or "33." in line:
                info["firmware"] = line.split(":", 1)[-1].strip() or info["firmware"]
            elif line.lower().startswith("system mac") or line.lower().startswith("mac"):
                info["mac"] = line.split(":", 1)[-1].strip() or info["mac"]
        return info

    def _build_html_report(self, sections: list[str], xiq_connected: bool) -> str:
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        switch_info = self._parse_switch_info(sections[0] if sections else "")
        xiq_badge = (
            '<span class="badge connected">&#10003; CONNECTED TO XIQ</span>'
            if xiq_connected else
            '<span class="badge not-connected">&#10007; NOT CONFIRMED ON XIQ</span>'
        )
        tab_buttons = ""
        tab_panels = ""
        for i, ((cmd, description), raw) in enumerate(zip(CAPTURE_COMMANDS, sections)):
            tab_id = f"tab{i}"
            active = "active" if i == 0 else ""
            short = cmd.replace("show ", "").title()
            tab_buttons += f'<button class="tab-btn {active}" onclick="switchTab(\'{tab_id}\')" id="btn-{tab_id}">{short}</button>\n'
            content = _html.escape(raw)
            display = "flex" if i == 0 else "none"
            tab_panels += f'<div class="tab-panel" id="{tab_id}" style="display:{display}"><p class="desc">{description}</p><pre>{content}</pre></div>\n'

        return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>{switch_info['name']} — Config Report</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:'Segoe UI',Arial,sans-serif;background:#0f172a;color:#e2e8f0;display:flex;flex-direction:column;min-height:100vh}}header{{background:#1e293b;padding:20px 32px;border-bottom:2px solid #7c3aed}}header h1{{color:#a78bfa;font-size:1.4rem;margin-bottom:12px}}.meta{{display:flex;flex-wrap:wrap;gap:20px}}.meta-item span:first-child{{font-size:0.7rem;text-transform:uppercase;color:#64748b}}.meta-item span:last-child{{font-size:0.88rem;color:#94a3b8}}.badge{{padding:3px 12px;border-radius:4px;font-size:0.82rem;font-weight:600}}.badge.connected{{background:#166534;color:#86efac}}.badge.not-connected{{background:#7f1d1d;color:#fca5a5}}.tabs{{background:#1e293b;border-bottom:1px solid #334155;padding:0 32px;display:flex;gap:4px;flex-wrap:wrap}}.tab-btn{{background:transparent;border:none;border-bottom:3px solid transparent;color:#64748b;padding:12px 18px;font-size:0.88rem;cursor:pointer}}.tab-btn.active{{color:#a78bfa;border-bottom-color:#7c3aed;font-weight:600}}.content{{flex:1;padding:24px 32px}}.tab-panel{{display:flex;flex-direction:column}}p.desc{{color:#64748b;font-size:0.88rem;margin-bottom:14px}}pre{{background:#0a0f1e;border:1px solid #334155;border-radius:6px;padding:16px;font-family:monospace;font-size:0.82rem;line-height:1.6;color:#7dd3fc;white-space:pre-wrap}}</style>
</head><body>
<header><h1>Extreme Networks {switch_info['type']} — Configuration Report</h1>
<div class="meta">
<div class="meta-item"><span>Switch Name</span><span>{switch_info['name']}</span></div>
<div class="meta-item"><span>Model</span><span>{switch_info['type']}</span></div>
<div class="meta-item"><span>Firmware</span><span>{switch_info['firmware']}</span></div>
<div class="meta-item"><span>Generated</span><span>{timestamp}</span></div>
<div class="meta-item"><span>XIQ Status</span><span>{xiq_badge}</span></div>
</div></header>
<div class="tabs">{tab_buttons}</div>
<div class="content">{tab_panels}</div>
<script>function switchTab(id){{document.querySelectorAll('.tab-panel').forEach(p=>p.style.display='none');document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));document.getElementById(id).style.display='flex';document.getElementById('btn-'+id).classList.add('active');}}</script>
</body></html>"""

    # ── Already-onboarded verification flow ──────────────────────────────────

    async def _run_onboarded_verification(self):
        await self._broadcast({"type": "log", "text": "Switch already running EXOS — verifying XIQ status"})

        # 1. Login
        if self._sm.current_state != SwitchState.EXOS_LOGGED_IN:
            await self._wait_for_state({SwitchState.EXOS_LOGIN_PROMPT, SwitchState.EXOS_LOGGED_IN}, timeout=20)
            if self._sm.current_state != SwitchState.EXOS_LOGGED_IN:
                await self._run_command("admin", settle=1.5, timeout=15)
                await self._wait_for_state({SwitchState.EXOS_LOGGED_IN}, timeout=10)
                if self._sm.current_state != SwitchState.EXOS_LOGGED_IN:
                    await self._run_command("Extreme01!!", settle=2.0, timeout=15)
                    await self._wait_for_state({SwitchState.EXOS_LOGGED_IN}, timeout=20)

        # 2. XIQ status
        await self._broadcast({"type": "log", "text": "Running show iqagent..."})
        iqagent_out = await self._run_command("show iqagent", settle=2.5, timeout=20)
        xiq_connected = bool(re.search(
            r'(state\s*:?\s*connected|connected to ExtremeCloud|connected to XIQ|This system is connected)',
            iqagent_out, re.IGNORECASE
        ))

        if not xiq_connected:
            confirm = await self._ask_ui(
                "xiq_confirm",
                "Switch is not confirmed connected to XIQ. Is it showing as Connected in the XIQ portal?",
                ["yes", "no"],
            )
            xiq_connected = confirm.lower().startswith("y")

        await self._broadcast({"type": "xiq_status", "connected": xiq_connected})

        # 3. Capture commands
        await self._broadcast({"type": "log", "text": "Capturing switch state..."})
        await self._run_command("disable cli paging", settle=2.0, timeout=10)
        await asyncio.sleep(1.0)

        sections: list[str] = []
        for cmd, description in CAPTURE_COMMANDS:
            output = await self._run_command(cmd, settle=4.0, timeout=90,
                                             auto_page=(cmd == "show port configuration"))
            if cmd == "show port configuration":
                output = self._filter_port_config(output)
            sections.append(output)
            await self._broadcast({"type": "log", "text": f"✓ {cmd} ({len(output.splitlines())} lines)"})

        await self._run_command("enable cli paging", settle=1.0, timeout=10)

        # 4. Build report and send to UI as download
        report_html = self._build_html_report(sections, xiq_connected)
        await self._broadcast({
            "type": "report_ready",
            "html": report_html,
            "filename": f"5320_config_{time.strftime('%Y%m%d_%H%M%S')}.html",
        })

        # 5. Ask what to do next
        choice = await self._ask_ui(
            "next_action",
            "Report ready. What would you like to do?",
            ["Exit session", "Stay in direct config mode"],
        )
        if choice.startswith("Exit"):
            await self._broadcast({"type": "session_complete"})
            self._running = False
        else:
            await self._broadcast({"type": "log", "text": "Direct config mode — type commands below."})
            self._monitor_only = True

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self):
        PERIODIC_INTERVAL = 30.0
        self._last_analysis = time.monotonic()

        while self._running:
            lines = self._proxy.get_lines()

            if lines:
                for line in lines:
                    await self._broadcast({"type": "console_line", "line": line})

                transition = self._sm.process_lines(lines)

                if transition:
                    await self._broadcast({
                        "type": "state_update",
                        "state": self._sm.current_state.name,
                        "os": self._sm.os_context,
                    })

                    if self._sm.likely_already_onboarded and not self._verification_done:
                        self._verification_done = True
                        await self._run_onboarded_verification()
                        self._last_analysis = time.monotonic()
                        continue

                    if self._monitor_only:
                        continue

                    # Call Claude on state transition
                    try:
                        instruction = self._analyzer.analyze(
                            console_buffer=self._proxy.get_raw_buffer(cfg.BUFFER_SIZE),
                            current_state=self._sm.current_state,
                            os_context=self._sm.os_context,
                            time_in_state=self._sm.time_in_state(),
                            boot_complete=self._sm.boot_complete,
                        )
                        await self._broadcast({
                            "type": "instruction",
                            "action": instruction.action,
                            "command": instruction.command,
                            "explanation": instruction.explanation,
                            "wait": instruction.wait,
                            "physical": instruction.physical,
                        })
                    except Exception as e:
                        await self._broadcast({"type": "log", "text": f"Claude error: {e}"})

                    self._last_analysis = time.monotonic()

                if self._sm.current_state == SwitchState.ONBOARDED:
                    await self._broadcast({"type": "session_complete"})
                    self._running = False
                    break

            elif (time.monotonic() - self._last_analysis) > PERIODIC_INTERVAL:
                if self._sm.current_state not in (SwitchState.UNKNOWN, SwitchState.ONBOARDED):
                    try:
                        instruction = self._analyzer.analyze(
                            console_buffer=self._proxy.get_raw_buffer(cfg.BUFFER_SIZE),
                            current_state=self._sm.current_state,
                            os_context=self._sm.os_context,
                            time_in_state=self._sm.time_in_state(),
                            boot_complete=self._sm.boot_complete,
                        )
                        await self._broadcast({
                            "type": "instruction",
                            "action": instruction.action,
                            "command": instruction.command,
                            "explanation": instruction.explanation,
                            "wait": instruction.wait,
                            "physical": instruction.physical,
                        })
                    except Exception:
                        pass
                    self._last_analysis = time.monotonic()

            await asyncio.sleep(0.05)
