"""Tests for USB-serial port detection."""
from unittest.mock import patch
from agent.port_detector import scan_ports, select_port


def test_scan_ports_empty(tmp_path):
    """When no USB-serial devices present, scan returns empty list."""
    with patch('glob.glob', return_value=[]):
        ports = scan_ports()
    assert ports == []


def test_scan_ports_found():
    """Scan returns detected USB-serial ports."""
    mock_ports = ["/dev/cu.usbserial-A9VKJO11"]
    with patch('glob.glob', return_value=mock_ports):
        ports = scan_ports()
    assert "/dev/cu.usbserial-A9VKJO11" in ports


def test_select_port_single():
    """Single port is auto-selected without prompting."""
    result = select_port(["/dev/cu.usbserial-A9VKJO11"])
    assert result == "/dev/cu.usbserial-A9VKJO11"
