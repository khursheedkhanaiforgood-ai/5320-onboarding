"""Rich terminal UI for the 5320 onboarding agent operator."""
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.live import Live
from rich.layout import Layout
from rich.table import Table
from collections import deque

from agent.state_machine import SwitchState, OSContext
from agent.console_analyzer import OperatorInstruction


console = Console()


STATE_COLORS = {
    SwitchState.UNKNOWN: "grey50",
    SwitchState.UBOOT: "yellow",
    SwitchState.BOOT_LOG_SCROLLING: "yellow",
    SwitchState.FE_LOGIN_PROMPT: "cyan",
    SwitchState.FE_LOGIN_BLOCKED: "red",
    SwitchState.FE_PASSWORD_CHANGE: "orange3",
    SwitchState.FE_LOGGED_IN: "green",
    SwitchState.FE_PRIVILEGED: "bright_green",
    SwitchState.ZTD_MODE: "cyan",
    SwitchState.DHCP_ACQUIRING: "cyan",
    SwitchState.XIQ_CONNECTING: "blue",
    SwitchState.FIRMWARE_DOWNLOADING: "blue",
    SwitchState.FIRMWARE_INSTALLING: "magenta",
    SwitchState.EXOS_BOOT: "yellow",
    SwitchState.EXOS_LOGIN_PROMPT: "cyan",
    SwitchState.EXOS_SETUP_WIZARD: "orange3",
    SwitchState.EXOS_LOGGED_IN: "green",
    SwitchState.EXOS_SAVE_CONFIG: "green",
    SwitchState.ONBOARDED: "bright_green",
    SwitchState.ERROR: "red",
}


class OperatorUI:
    """Terminal UI showing console output and agent instructions side by side."""

    def __init__(self, max_console_lines: int = 50):
        self._console_lines: deque[str] = deque(maxlen=max_console_lines)
        self._current_state = SwitchState.UNKNOWN
        self._os_context = OSContext.UNKNOWN
        self._current_instruction: OperatorInstruction | None = None
        self._port = "detecting..."

    def set_port(self, port: str):
        self._port = port

    def add_console_lines(self, lines: list[str]):
        for line in lines:
            self._console_lines.append(line)

    def update_state(self, state: SwitchState, os_context: OSContext):
        self._current_state = state
        self._os_context = os_context

    def show_instruction(self, instruction: OperatorInstruction):
        self._current_instruction = instruction
        self._render()

    def show_waiting(self, message: str):
        console.print(Panel(f"[yellow]{message}[/yellow]", title="[bold]5320 Onboarding Agent[/bold]"))

    def _render(self):
        """Print current state and instruction to terminal."""
        color = STATE_COLORS.get(self._current_state, "white")
        state_text = f"[{color}]{self._current_state.name}[/{color}]"
        os_text = f"[cyan]{self._os_context.name}[/cyan]"

        table = Table.grid(padding=1)
        table.add_column(style="bold", width=20)
        table.add_column()
        table.add_row("Port:", self._port)
        table.add_row("Switch State:", state_text)
        table.add_row("OS:", os_text)

        console.print(Panel(table, title="[bold purple]5320 Onboarding Agent[/bold purple]"))

        if self._current_instruction:
            inst = self._current_instruction
            content = Text()
            if inst.physical:
                content.append("PHYSICAL ACTION REQUIRED\n", style="bold yellow")
            if inst.wait:
                content.append("WAIT ", style="bold yellow")
            content.append(f"{inst.action}\n", style="bold white")
            if inst.command:
                content.append(f"\nType this command:\n", style="dim")
                content.append(f"  {inst.command}\n", style="bold cyan")
            if inst.explanation:
                content.append(f"\nWhy: {inst.explanation}", style="dim")
            console.print(Panel(content, title="[bold green]What to do next[/bold green]"))
