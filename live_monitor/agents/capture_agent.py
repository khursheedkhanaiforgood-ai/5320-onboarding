"""
PacketCaptureAgent — Sub-Sprint 2e
Triggers 60-second pcap bursts on AP wifi0/wifi1/eth0 via the existing
paramiko SSH pattern. Pulls the file via SFTP. Opens Wireshark at completion.

Triggers:
  1. Manual  — CaptureAgent.start(trigger='manual')
  2. Auto    — called by calibration loop when δ_calibration > threshold (Sprint 3)

Thread model: capture runs in a daemon thread; on_complete() is called from
that thread. All public state is protected by self._lock.
"""
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import paramiko


# ── Result contract ────────────────────────────────────────────────────────────

@dataclass
class CaptureResult:
    ts:         str             # ISO-8601 start time
    interface:  str             # wifi0 / wifi1 / eth0
    duration_s: int             # requested duration
    filter_desc:str             # human-readable filter description
    local_path: str             # absolute path to local .pcap file
    file_bytes: int             # file size in bytes
    trigger:    str             # 'manual' | 'calibration_auto'
    error:      Optional[str]   # None on success


# ── Filter presets ─────────────────────────────────────────────────────────────

FILTER_PRESETS = {
    'all':            [],
    'data_only':      ['l2 data'],
    'dhcp':           ['l3 protocol 17 src-port 68 dst-port 67',
                       'l3 protocol 17 src-port 67 dst-port 68'],
    'decrypt_errors': ['l2 error decrypt', 'l2 error mic'],
}


# ── Agent ─────────────────────────────────────────────────────────────────────

class CaptureAgent:
    """
    Maintains its own SSH connection (independent of CollectorAgent).
    CollectorAgent runs its poll shell continuously; CaptureAgent opens a
    fresh connection per burst so capture commands don't block poll timing.
    """

    PROMPT      = '#'
    RECV_CHUNK  = 65535
    SETUP_TIMEOUT   = 10.0   # seconds — connect + filter setup commands
    TEARDOWN_TIMEOUT = 15.0  # seconds — save + SFTP pull

    def __init__(self, ap_ip: str, username: str, password: str, log_dir: str):
        self.ap_ip    = ap_ip
        self.username = username
        self.password = password
        self.log_dir  = log_dir

        self._lock      = threading.Lock()
        self._state     = 'idle'       # 'idle' | 'capturing' | 'transferring'
        self._capture_start: Optional[float] = None
        self._capture_duration: int = 0
        self._last_result: Optional[CaptureResult] = None

    # ── Public interface ───────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def elapsed(self) -> float:
        with self._lock:
            if self._capture_start is None:
                return 0.0
            return time.monotonic() - self._capture_start

    @property
    def last_result(self) -> Optional[CaptureResult]:
        with self._lock:
            return self._last_result

    def is_busy(self) -> bool:
        return self.state != 'idle'

    def start(
        self,
        interface:   str = 'wifi0',
        duration_s:  int = 60,
        filter_preset: str = 'data_only',
        mac_filter:  Optional[str] = None,
        trigger:     str = 'manual',
        on_complete: Optional[Callable[[CaptureResult], None]] = None,
        open_wireshark: bool = True,
    ) -> bool:
        """
        Non-blocking. Returns False if already busy.
        on_complete(result) is called from the capture thread when done.
        """
        with self._lock:
            if self._state != 'idle':
                return False
            self._state = 'capturing'
            self._capture_start    = time.monotonic()
            self._capture_duration = duration_s

        t = threading.Thread(
            target=self._run,
            args=(interface, duration_s, filter_preset, mac_filter,
                  trigger, on_complete, open_wireshark),
            daemon=True,
            name='capture',
        )
        t.start()
        return True

    # ── Internal capture flow ─────────────────────────────────────────────────

    def _run(self, interface, duration_s, filter_preset, mac_filter,
             trigger, on_complete, open_wireshark):
        ts_utc = datetime.now(timezone.utc).isoformat()
        slug   = datetime.now().strftime('%Y%m%dT%H%M%S')
        remote_path = f'/tmp/cap_{interface}_{slug}.pcap'
        local_path  = os.path.join(
            self.log_dir, f'pcap_{interface}_{slug}.pcap')
        Path(self.log_dir).mkdir(parents=True, exist_ok=True)

        filters    = list(FILTER_PRESETS.get(filter_preset, []))
        if mac_filter:
            filters.insert(0, f'l2 src-mac {mac_filter}')
        filter_desc = (f'{filter_preset}' +
                       (f' mac={mac_filter}' if mac_filter else ''))

        result = CaptureResult(
            ts=ts_utc, interface=interface, duration_s=duration_s,
            filter_desc=filter_desc, local_path=local_path,
            file_bytes=0, trigger=trigger, error=None,
        )

        client  = None
        shell   = None
        try:
            # ── 1. Connect ────────────────────────────────────────────────────
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                hostname=self.ap_ip, username=self.username,
                password=self.password,
                look_for_keys=False, allow_agent=False, timeout=15,
            )
            shell = client.invoke_shell(width=220, height=50)
            time.sleep(1.5)
            self._drain(shell)
            self._cmd(shell, 'console page 0', self.SETUP_TIMEOUT)

            # ── 2. Clear previous capture buffer ─────────────────────────────
            self._cmd(shell, 'clear capture local', self.SETUP_TIMEOUT)

            # ── 3. Set up filters ─────────────────────────────────────────────
            filter_args = ''
            if filters:
                for i, f in enumerate(filters, start=1):
                    self._cmd(shell, f'filter {i} {f}', self.SETUP_TIMEOUT)
                logic = 'and' if len(filters) == 1 else 'or'
                nums  = ' '.join(str(i) for i in range(1, len(filters)+1))
                filter_args = f' filter {logic} {nums}'

            # ── 4. Start capture (runs for duration_s seconds on AP) ──────────
            count = min(5000, duration_s * 80)  # ~80 frames/s worst case
            prom  = ' promiscuous' if interface.startswith('wifi') else ''
            capture_cmd = (
                f'capture interface {interface}'
                f' count {count} duration {duration_s}'
                f'{filter_args}{prom}'
            )
            # Send the command; don't wait for prompt — AP is silently capturing
            shell.send(capture_cmd + '\n')

            # ── 5. Wait for capture to finish ─────────────────────────────────
            with self._lock:
                self._state = 'capturing'
            time.sleep(duration_s + 3)

            # ── 6. Wait for prompt (capture finished) ─────────────────────────
            self._wait_prompt(shell, timeout=10.0)

            # ── 7. Save pcap to AP filesystem ────────────────────────────────
            with self._lock:
                self._state = 'transferring'
            self._cmd(shell, f'capture save interface {interface} {remote_path}',
                      self.TEARDOWN_TIMEOUT)

            # ── 8. SFTP pull ──────────────────────────────────────────────────
            sftp = client.open_sftp()
            try:
                sftp.get(remote_path, local_path)
                stat = sftp.stat(remote_path)
                result.file_bytes = stat.st_size
            finally:
                sftp.close()

            # ── 9. Clean up remote file ───────────────────────────────────────
            self._cmd(shell, 'clear capture local', self.TEARDOWN_TIMEOUT)

        except Exception as exc:
            result.error = str(exc)
        finally:
            try:
                if shell:  shell.close()
                if client: client.close()
            except Exception:
                pass
            with self._lock:
                self._state      = 'idle'
                self._capture_start = None
                self._last_result   = result

        # ── 10. Post-capture actions ─────────────────────────────────────────
        if result.error is None and os.path.exists(local_path):
            if open_wireshark:
                _open_wireshark(local_path)

        if on_complete:
            on_complete(result)

    # ── SSH helpers ───────────────────────────────────────────────────────────

    def _cmd(self, shell, cmd: str, timeout: float) -> str:
        shell.send(cmd + '\n')
        return self._wait_prompt(shell, timeout)

    def _wait_prompt(self, shell, timeout: float) -> str:
        out = ''
        deadline = time.time() + timeout
        while time.time() < deadline:
            if shell.recv_ready():
                chunk = shell.recv(self.RECV_CHUNK).decode('utf-8', errors='replace')
                out  += chunk
                last  = out.rstrip('\n').split('\n')[-1]
                if last.strip().endswith(self.PROMPT):
                    break
            else:
                time.sleep(0.1)
        return out

    def _drain(self, shell) -> None:
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if shell.recv_ready():
                shell.recv(self.RECV_CHUNK)
            else:
                time.sleep(0.2)


# ── Wireshark launcher ────────────────────────────────────────────────────────

def _open_wireshark(pcap_path: str) -> None:
    """
    Opens the pcap in Wireshark. Tries the CLI binary first, falls back
    to macOS open (uses default .pcap handler).
    """
    for cmd in (['wireshark', pcap_path], ['open', '-a', 'Wireshark', pcap_path]):
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except FileNotFoundError:
            continue
    # Last resort: open with default handler
    subprocess.Popen(['open', pcap_path])
