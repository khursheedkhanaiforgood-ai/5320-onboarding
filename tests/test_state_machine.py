"""Unit tests for the state machine using captured console output."""
import pytest
from agent.state_machine import StateMachine, SwitchState, OSContext


def test_initial_state():
    sm = StateMachine()
    assert sm.current_state == SwitchState.UNKNOWN
    assert sm.os_context == OSContext.UNKNOWN


def test_uboot_detection():
    sm = StateMachine()
    sm.process_lines(["U-Boot 2022.10 (Oct 28 2024 - 12:31:21 -0400)"])
    assert sm.current_state == SwitchState.UBOOT


def test_fe_login_detection():
    sm = StateMachine()
    sm.process_lines(["Login:"])
    assert sm.current_state == SwitchState.FE_LOGIN_PROMPT


def test_fe_login_blocked():
    sm = StateMachine()
    sm.process_lines(["Blocked unauthorized ACLI access for user admin from console port"])
    assert sm.current_state == SwitchState.FE_LOGIN_BLOCKED


def test_fe_logged_in():
    sm = StateMachine()
    sm.process_lines(["5320-16P-2MXT-2X-FabricEngine:1>"])
    assert sm.current_state == SwitchState.FE_LOGGED_IN
    assert sm.os_context == OSContext.FABRIC_ENGINE


def test_ztd_mode():
    sm = StateMachine()
    sm.process_lines(["Booting in Zero Touch Deployment Mode"])
    assert sm.current_state == SwitchState.ZTD_MODE


def test_dhcp_acquiring():
    sm = StateMachine()
    sm.process_lines(["DHCP Address 192.168.0.23 added to interface mgmt-vlan"])
    assert sm.current_state == SwitchState.DHCP_ACQUIRING


def test_xiq_connecting():
    sm = StateMachine()
    sm.process_lines(["IQAgent successfully connected to XIQ."])
    assert sm.current_state == SwitchState.XIQ_CONNECTING


def test_firmware_downloading():
    sm = StateMachine()
    sm.process_lines(["Downloading summit_arm-33.5.2.118.xos started."])
    assert sm.current_state == SwitchState.FIRMWARE_DOWNLOADING


def test_firmware_installing():
    sm = StateMachine()
    sm.process_lines(["Chassis reboot initiated from ExtremeCloud IQ"])
    assert sm.current_state == SwitchState.FIRMWARE_INSTALLING


def test_exos_boot():
    sm = StateMachine()
    sm.process_lines(["Starting Extreme Networks Switch Engine 33.5.2b118"])
    assert sm.current_state == SwitchState.EXOS_BOOT
    assert sm.os_context == OSContext.EXOS


def test_exos_login():
    sm = StateMachine()
    sm.process_lines(["Starting Extreme Networks Switch Engine 33.5.2b118"])
    sm.process_lines(["login:"])
    assert sm.current_state == SwitchState.EXOS_LOGIN_PROMPT


def test_onboarded():
    sm = StateMachine()
    sm.process_lines(["Configuration saved to primary.cfg successfully."])
    assert sm.current_state == SwitchState.ONBOARDED


def test_boot_complete_flag():
    sm = StateMachine()
    assert sm.boot_complete is False
    sm.process_lines(["Boot sequence successful"])
    assert sm.boot_complete is True


def test_already_onboarded_detected():
    """Switch lands on EXOS login prompt without any ZTP+/DHCP/XIQ states."""
    sm = StateMachine()
    # Simulate connecting to a switch already running EXOS
    sm.process_lines(["Starting Extreme Networks Switch Engine 33.5.2b118"])
    sm.process_lines(["login:"])
    assert sm.current_state == SwitchState.EXOS_LOGIN_PROMPT
    assert sm.likely_already_onboarded is True


def test_already_onboarded_not_triggered_after_fresh_onboarding():
    """Switch goes through full ZTP+ flow — should NOT be flagged as already onboarded."""
    sm = StateMachine()
    sm.process_lines(["Booting in Zero Touch Deployment Mode"])
    sm.process_lines(["IQAgent successfully connected to XIQ."])
    sm.process_lines(["Downloading summit_arm-33.5.2.118.xos started."])
    sm.process_lines(["Starting Extreme Networks Switch Engine 33.5.2b118"])
    sm.process_lines(["login:"])
    assert sm.current_state == SwitchState.EXOS_LOGIN_PROMPT
    assert sm.likely_already_onboarded is False


def test_already_onboarded_not_triggered_before_exos():
    """Property is False when state has not yet reached an EXOS active state."""
    sm = StateMachine()
    sm.process_lines(["U-Boot 2022.10"])
    assert sm.likely_already_onboarded is False


def test_full_sequence():
    """Simulate a full onboarding sequence."""
    sm = StateMachine()
    sequence = [
        ("U-Boot 2022.10", SwitchState.UBOOT),
        ("Starting vsp application...", SwitchState.BOOT_LOG_SCROLLING),
        ("Login:", SwitchState.FE_LOGIN_PROMPT),
        ("Blocked unauthorized ACLI access for user admin", SwitchState.FE_LOGIN_BLOCKED),
        ("5320-16P-2MXT-2X-FabricEngine:1>", SwitchState.FE_LOGGED_IN),
        ("5320-16P-2MXT-2X-FabricEngine:1#", SwitchState.FE_PRIVILEGED),
        ("Booting in Zero Touch Deployment Mode", SwitchState.ZTD_MODE),
        ("DHCP Address 192.168.0.23 added to interface mgmt-vlan", SwitchState.DHCP_ACQUIRING),
        ("IQAgent successfully connected to XIQ.", SwitchState.XIQ_CONNECTING),
        ("Downloading summit_arm-33.5.2.118.xos started.", SwitchState.FIRMWARE_DOWNLOADING),
        ("Chassis reboot initiated from ExtremeCloud IQ", SwitchState.FIRMWARE_INSTALLING),
        ("Starting Extreme Networks Switch Engine 33.5.2b118", SwitchState.EXOS_BOOT),
        ("login:", SwitchState.EXOS_LOGIN_PROMPT),
        ("Would you like to disable MSTP? [y/N/q]", SwitchState.EXOS_SETUP_WIZARD),
        ("Configuration saved to primary.cfg successfully.", SwitchState.ONBOARDED),
    ]
    for line, expected_state in sequence:
        sm.process_lines([line])
        assert sm.current_state == expected_state, f"Expected {expected_state} after: {line!r}"
