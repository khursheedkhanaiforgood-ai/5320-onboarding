"""Entry point for the Extreme Networks 5320 automated onboarding agent."""
import time
import click

from agent.config import load_config
from agent.port_detector import wait_for_port, scan_ports, select_port
from agent.serial_monitor import SerialMonitor
from agent.state_machine import StateMachine, SwitchState
from agent.console_analyzer import ConsoleAnalyzer
from agent.operator_ui import OperatorUI, console


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
    console.print(f"[green]Connected to: {detected_port}[/green]")
    console.print(
        "[yellow]Open a second terminal and run:[/yellow]\n"
        f"[bold cyan]  TERM=vt100 screen {detected_port} {config.baud_rate}[/bold cyan]\n"
        "[dim]Type commands there when instructed. This window guides you.[/dim]\n"
    )

    # ── Step 2: Start Serial Monitor ────────────────────────────────────────
    monitor = SerialMonitor(detected_port, config.baud_rate, config.buffer_size)
    monitor.start()

    state_machine = StateMachine()
    analyzer = ConsoleAnalyzer(config.api_key, config.model)

    # ── Step 3: Main Loop ───────────────────────────────────────────────────
    PERIODIC_ANALYSIS_INTERVAL = 30.0  # seconds
    last_analysis_time = time.monotonic()

    try:
        while True:
            lines = monitor.get_lines()

            if lines:
                ui.add_console_lines(lines)
                transition = state_machine.process_lines(lines)

                if transition:
                    ui.update_state(state_machine.current_state, state_machine.os_context)
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
