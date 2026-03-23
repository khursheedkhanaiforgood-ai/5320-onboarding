"""Entry point for the Extreme Networks 5320 automated onboarding agent."""
import time
import click

from agent.config import load_config
from agent.port_detector import wait_for_port, scan_ports, select_port
from agent.serial_monitor import LogfileMonitor
from agent.state_machine import StateMachine, SwitchState
from agent.console_analyzer import ConsoleAnalyzer
from agent.operator_ui import OperatorUI, console


def _prompt_already_onboarded(ui: OperatorUI) -> str:
    """Show menu when switch is detected as already onboarded. Returns chosen action."""
    console.print()
    console.print("[bold yellow]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold yellow]")
    console.print("[bold green]  Switch detected: already running EXOS[/bold green]")
    console.print("[bold yellow]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold yellow]")
    console.print()
    console.print("This switch appears to be fully onboarded (EXOS active, no ZTP+ journey seen).")
    console.print()
    console.print("What would you like to do?")
    console.print()
    console.print("  [bold cyan]1[/bold cyan]  Monitor only      — watch console activity, no onboarding guidance")
    console.print("  [bold cyan]2[/bold cyan]  Re-onboard        — guide through factory reset → ZTP+ → EXOS from scratch")
    console.print("  [bold cyan]3[/bold cyan]  Exit              — quit the agent")
    console.print()

    while True:
        choice = click.prompt("Enter choice", default="1")
        if choice in ("1", "2", "3"):
            return choice
        console.print("[red]Please enter 1, 2, or 3[/red]")


LOGFILE = LogfileMonitor.DEFAULT_LOGFILE


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
    console.print()
    console.print("[bold yellow]ACTION REQUIRED — open a second terminal and run:[/bold yellow]")
    console.print(
        f"[bold cyan]  TERM=vt100 screen -L -Logfile {LOGFILE} {detected_port} {config.baud_rate}[/bold cyan]"
    )
    console.print()
    console.print("[dim]That window is where you type commands on the switch.[/dim]")
    console.print(f"[dim]This agent reads the screen log at {LOGFILE}[/dim]")
    console.print()
    click.pause(info="Press Enter here once the screen session is open...")
    console.print()

    # ── Step 2: Start Logfile Monitor ───────────────────────────────────────
    monitor = LogfileMonitor(LOGFILE, config.buffer_size)
    monitor.start()

    state_machine = StateMachine()
    analyzer = ConsoleAnalyzer(config.api_key, config.model)

    # ── Step 3: Main Loop ───────────────────────────────────────────────────
    PERIODIC_ANALYSIS_INTERVAL = 30.0  # seconds
    last_analysis_time = time.monotonic()
    monitor_only = False  # set True if user chooses "monitor only" for already-onboarded switch

    try:
        while True:
            lines = monitor.get_lines()

            if lines:
                ui.add_console_lines(lines)
                transition = state_machine.process_lines(lines)

                if transition:
                    ui.update_state(state_machine.current_state, state_machine.os_context)

                    # ── Already-onboarded detection ──────────────────────────
                    if state_machine.likely_already_onboarded:
                        monitor.stop()
                        choice = _prompt_already_onboarded(ui)
                        if choice == "3":
                            console.print("[yellow]Exiting.[/yellow]")
                            return
                        elif choice == "2":
                            console.print()
                            console.print("[bold]Re-onboarding selected.[/bold] Resuming guidance from current state...")
                            console.print(
                                "[dim]To factory reset: log in as admin, then run:[/dim]\n"
                                "[bold cyan]  delete /intflash/primary.cfg[/bold cyan]\n"
                                "[dim]then[/dim] [bold cyan]reboot[/bold cyan]"
                            )
                            monitor_only = False
                        else:
                            monitor_only = True
                            console.print("[green]Monitor-only mode. Watching console...[/green]")
                        monitor.start()
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


if __name__ == "__main__":
    main()
