"""State machine for tracking Extreme 5320 switch onboarding progress."""
import time
from dataclasses import dataclass
from enum import Enum, auto


class SwitchState(Enum):
    UNKNOWN = auto()
    UBOOT = auto()
    BOOT_LOG_SCROLLING = auto()
    FE_LOGIN_PROMPT = auto()
    FE_LOGIN_BLOCKED = auto()
    FE_PASSWORD_CHANGE = auto()
    FE_LOGGED_IN = auto()
    FE_PRIVILEGED = auto()
    ZTD_MODE = auto()
    DHCP_ACQUIRING = auto()
    XIQ_CONNECTING = auto()
    FIRMWARE_DOWNLOADING = auto()
    FIRMWARE_INSTALLING = auto()
    EXOS_BOOT = auto()
    EXOS_LOGIN_PROMPT = auto()
    EXOS_SETUP_WIZARD = auto()
    EXOS_LOGGED_IN = auto()
    EXOS_SAVE_CONFIG = auto()
    ONBOARDED = auto()
    ERROR = auto()


class OSContext(Enum):
    UNKNOWN = auto()
    FABRIC_ENGINE = auto()
    EXOS = auto()


@dataclass
class StateTransition:
    old_state: SwitchState
    new_state: SwitchState
    trigger_line: str
    timestamp: float


# States that confirm active onboarding is in progress (not already-onboarded)
_ONBOARDING_IN_PROGRESS_STATES = frozenset({
    SwitchState.ZTD_MODE,
    SwitchState.DHCP_ACQUIRING,
    SwitchState.XIQ_CONNECTING,
    SwitchState.FIRMWARE_DOWNLOADING,
    SwitchState.FIRMWARE_INSTALLING,
})

# Landing in any of these without prior onboarding states = already onboarded
_EXOS_ACTIVE_STATES = frozenset({
    SwitchState.EXOS_LOGIN_PROMPT,
    SwitchState.EXOS_LOGGED_IN,
    SwitchState.EXOS_SETUP_WIZARD,
    SwitchState.EXOS_SAVE_CONFIG,
    SwitchState.ONBOARDED,
})


class StateMachine:
    """Regex-based state machine. No API calls — fast local pattern matching."""

    def __init__(self):
        self._state = SwitchState.UNKNOWN
        self._os_context = OSContext.UNKNOWN
        self._state_entered_at = time.monotonic()
        self._boot_complete = False
        self._seen_states: set[SwitchState] = set()
        # Import here to avoid circular imports
        self._patterns = None
        self._os_patterns = None
        self._boot_patterns = None

    def _load_patterns(self):
        if self._patterns is None:
            from .patterns import STATE_PATTERNS, OS_PATTERNS, BOOT_COMPLETE_PATTERNS
            self._patterns = STATE_PATTERNS
            self._os_patterns = OS_PATTERNS
            self._boot_patterns = BOOT_COMPLETE_PATTERNS

    @property
    def current_state(self) -> SwitchState:
        return self._state

    @property
    def os_context(self) -> OSContext:
        return self._os_context

    @property
    def boot_complete(self) -> bool:
        return self._boot_complete

    @property
    def likely_already_onboarded(self) -> bool:
        """True if the switch is already running EXOS with no onboarding journey seen.

        Detected when we land on an EXOS-active state without ever having seen
        ZTD, DHCP, XIQ, or firmware states that indicate onboarding in progress.
        """
        if self._state not in _EXOS_ACTIVE_STATES:
            return False
        return not self._seen_states.intersection(_ONBOARDING_IN_PROGRESS_STATES)

    def time_in_state(self) -> float:
        """Seconds since last state transition."""
        return time.monotonic() - self._state_entered_at

    def process_lines(self, lines: list[str]) -> StateTransition | None:
        """Process new console lines. Returns StateTransition if state changed."""
        self._load_patterns()

        transition = None
        for line in lines:
            # Update OS context
            self._update_os_context(line)

            # Check boot complete
            for pattern in self._boot_patterns:
                if pattern.search(line):
                    self._boot_complete = True

            # Detect new state
            new_state = self._detect_state(line)
            if new_state and new_state != self._state:
                transition = StateTransition(
                    old_state=self._state,
                    new_state=new_state,
                    trigger_line=line.strip(),
                    timestamp=time.monotonic(),
                )
                self._seen_states.add(new_state)
                self._state = new_state
                self._state_entered_at = time.monotonic()
                self._boot_complete = False  # reset on new state
                self._infer_os_from_state(new_state)

        return transition

    def _detect_state(self, line: str) -> SwitchState | None:
        for state, patterns in self._patterns.items():
            for pattern in patterns:
                if pattern.search(line):
                    return state
        return None

    def _update_os_context(self, line: str):
        if self._os_context != OSContext.UNKNOWN:
            return  # Once set, don't change
        for context, patterns in self._os_patterns.items():
            for pattern in patterns:
                if pattern.search(line):
                    self._os_context = context
                    return

    def _infer_os_from_state(self, state: SwitchState):
        """Infer OS context from state when line patterns don't match."""
        exos_states = {
            SwitchState.EXOS_BOOT, SwitchState.EXOS_LOGIN_PROMPT,
            SwitchState.EXOS_SETUP_WIZARD, SwitchState.EXOS_LOGGED_IN,
            SwitchState.EXOS_SAVE_CONFIG, SwitchState.ONBOARDED,
        }
        fe_states = {
            SwitchState.FE_LOGIN_PROMPT, SwitchState.FE_LOGIN_BLOCKED,
            SwitchState.FE_PASSWORD_CHANGE, SwitchState.FE_LOGGED_IN,
            SwitchState.FE_PRIVILEGED,
        }
        if self._os_context == OSContext.UNKNOWN:
            if state in exos_states:
                self._os_context = OSContext.EXOS
            elif state in fe_states:
                self._os_context = OSContext.FABRIC_ENGINE
