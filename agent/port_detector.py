"""Auto-detect USB-serial ports on macOS for Extreme 5320 console connection."""
import glob
import time
from typing import Callable

# Patterns for USB-serial adapters on macOS
USB_SERIAL_PATTERNS = [
    "/dev/cu.usbserial-*",
    "/dev/cu.usbmodem*",
    "/dev/cu.SLAB_USBtoUART*",
    "/dev/cu.wchusbserial*",
]


def scan_ports() -> list[str]:
    """Return list of all currently connected USB-serial ports."""
    ports = []
    for pattern in USB_SERIAL_PATTERNS:
        ports.extend(glob.glob(pattern))
    return sorted(set(ports))


def select_port(ports: list[str]) -> str:
    """If multiple ports found, let human choose. If one, return it."""
    if len(ports) == 1:
        return ports[0]
    print("\nMultiple USB-serial ports detected:")
    for i, port in enumerate(ports):
        print(f"  [{i + 1}] {port}")
    while True:
        choice = input("Select port number: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(ports):
            return ports[int(choice) - 1]
        print("Invalid choice. Try again.")


def wait_for_port(
    on_status: Callable[[str], None] | None = None,
    poll_interval: float = 2.0,
) -> str:
    """
    Block until a USB-serial port appears.
    Calls on_status(message) with guidance for the human while waiting.
    Returns the detected port path.
    """
    known_ports = set(scan_ports())

    if known_ports:
        # Ports already present
        ports = list(known_ports)
        if len(ports) == 1:
            return ports[0]
        return select_port(ports)

    # No port yet — guide the human
    if on_status:
        on_status(
            "No USB-serial port detected.\n\n"
            "Please connect your console cable:\n"
            "  1. Plug USB-C to USB-A adapter into your Mac\n"
            "  2. Connect USB-to-Serial (RJ45 rollover) cable\n"
            "  3. Plug RJ45 end into the switch CONSOLE port\n"
            "  4. Power on the switch if not already on\n\n"
            "Waiting for port to appear..."
        )

    while True:
        time.sleep(poll_interval)
        current_ports = set(scan_ports())
        new_ports = current_ports - known_ports
        if new_ports:
            port = list(new_ports)[0]
            if on_status:
                on_status(f"Port detected: {port}")
            return port
