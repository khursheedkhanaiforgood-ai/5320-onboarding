"""Console readers for Extreme 5320 switch onboarding agent.

Two modes:
  LogfileMonitor  — reads from a screen logfile (recommended, no port conflict)
  SerialMonitor   — reads directly from serial port (requires exclusive access)
"""
import threading
import queue
import time
import os
from collections import deque
from typing import Callable


class LogfileMonitor:
    """
    Reads console output from a screen logfile in a background thread.
    The human operator's screen session holds the serial port exclusively.
    This monitor just tails the logfile — no port conflict.

    Start screen with logging:
        TERM=vt100 screen -L -Logfile /tmp/5320_console.log /dev/cu.usbserial-XXXX 115200
    """

    DEFAULT_LOGFILE = "/tmp/5320_console.log"

    def __init__(self, logfile: str = DEFAULT_LOGFILE, buffer_size: int = 4000):
        self._logfile = logfile
        self._buffer_size = buffer_size
        self._line_queue: queue.Queue[str] = queue.Queue()
        self._raw_buffer: deque[str] = deque(maxlen=buffer_size)
        self._partial_line = ""
        self._running = False
        self._thread: threading.Thread | None = None
        self._file_position = 0
        self._last_byte_time = time.monotonic()
        self._prompt_timeout = 2.0

    def start(self):
        """Start tailing the logfile in a background thread."""
        # Seek to end of existing file so we only see new output
        if os.path.exists(self._logfile):
            self._file_position = os.path.getsize(self._logfile)
        self._running = True
        self._thread = threading.Thread(target=self._tail_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def get_lines(self) -> list[str]:
        lines = []
        while True:
            try:
                lines.append(self._line_queue.get_nowait())
            except queue.Empty:
                break
        if self._partial_line and (time.monotonic() - self._last_byte_time) > self._prompt_timeout:
            lines.append(self._partial_line)
            self._partial_line = ""
        return lines

    def get_raw_buffer(self, max_chars: int | None = None) -> str:
        raw = "".join(self._raw_buffer)
        if max_chars:
            return raw[-max_chars:]
        return raw

    def _tail_loop(self):
        while self._running:
            try:
                if os.path.exists(self._logfile):
                    size = os.path.getsize(self._logfile)
                    if size > self._file_position:
                        with open(self._logfile, 'rb') as f:
                            f.seek(self._file_position)
                            data = f.read(size - self._file_position)
                        self._file_position = size
                        self._last_byte_time = time.monotonic()
                        text = data.decode('utf-8', errors='replace')
                        self._process_text(text)
            except Exception:
                pass
            time.sleep(0.05)

    def _process_text(self, text: str):
        for char in text:
            self._raw_buffer.append(char)
            if char in ('\n', '\r'):
                if self._partial_line.strip():
                    self._line_queue.put(self._partial_line)
                self._partial_line = ""
            else:
                self._partial_line += char


class SerialMonitor:
    """
    Reads from a serial port in a background thread.
    Requires exclusive port access — use LogfileMonitor if screen is also open.
    """

    def __init__(self, port: str, baud: int = 115200, buffer_size: int = 4000):
        self._port = port
        self._baud = baud
        self._buffer_size = buffer_size
        self._line_queue: queue.Queue[str] = queue.Queue()
        self._raw_buffer: deque[str] = deque(maxlen=buffer_size)
        self._partial_line = ""
        self._running = False
        self._thread: threading.Thread | None = None
        self._ser = None
        self._on_line: Callable[[str], None] | None = None
        self._last_byte_time = time.monotonic()
        self._prompt_timeout = 2.0

    def start(self):
        """Open serial port and start background reader thread."""
        import serial
        self._ser = serial.Serial(
            port=self._port,
            baudrate=self._baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.1,
        )
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._ser:
            self._ser.close()

    def get_lines(self) -> list[str]:
        """Drain the line queue and return all available lines."""
        lines = []
        while True:
            try:
                lines.append(self._line_queue.get_nowait())
            except queue.Empty:
                break

        # Also flush partial line if it's been sitting too long (prompt detection)
        if self._partial_line and (time.monotonic() - self._last_byte_time) > self._prompt_timeout:
            lines.append(self._partial_line)
            self._partial_line = ""

        return lines

    def get_raw_buffer(self, max_chars: int | None = None) -> str:
        """Return recent console output as a string for Claude context."""
        raw = "".join(self._raw_buffer)
        if max_chars:
            return raw[-max_chars:]
        return raw

    def _read_loop(self):
        """Background thread: read bytes from serial, buffer into lines."""
        while self._running:
            try:
                if self._ser and self._ser.in_waiting:
                    data = self._ser.read(self._ser.in_waiting)
                    self._last_byte_time = time.monotonic()
                    text = data.decode('utf-8', errors='replace')
                    self._process_text(text)
                else:
                    time.sleep(0.05)
            except Exception:
                time.sleep(0.1)

    def _process_text(self, text: str):
        """Split text into lines, handling CR/LF and partial lines."""
        for char in text:
            self._raw_buffer.append(char)
            if char in ('\n', '\r'):
                if self._partial_line.strip():
                    self._line_queue.put(self._partial_line)
                self._partial_line = ""
            else:
                self._partial_line += char
