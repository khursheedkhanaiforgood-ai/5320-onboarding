"""
SerialProxy — abstraction layer between session_manager and the bridge WebSocket.
Provides the same get_lines() / get_raw_buffer() interface as LogfileMonitor
so session_manager can be reused with minimal changes.
"""
import asyncio
import collections
import time
from typing import Callable


class SerialProxy:
    def __init__(self, buffer_size: int = 4000):
        self._lines: asyncio.Queue = asyncio.Queue()
        self._raw_buf: collections.deque = collections.deque()
        self._raw_len: int = 0
        self._buffer_size = buffer_size
        self._send_cb: Callable[[str], None] | None = None  # set by ws_bridge

    def set_send_callback(self, cb: Callable[[str], None]):
        self._send_cb = cb

    def ingest_line(self, line: str):
        """Called by ws_bridge when a serial_line arrives from the bridge."""
        self._lines.put_nowait(line)
        self._raw_buf.append(line + "\n")
        self._raw_len += len(line) + 1
        while self._raw_len > self._buffer_size * 2 and self._raw_buf:
            removed = self._raw_buf.popleft()
            self._raw_len -= len(removed)

    def get_lines(self) -> list[str]:
        """Drain all queued lines (non-blocking)."""
        lines = []
        while not self._lines.empty():
            try:
                lines.append(self._lines.get_nowait())
            except asyncio.QueueEmpty:
                break
        return lines

    def get_raw_buffer(self, max_chars: int) -> str:
        raw = "".join(self._raw_buf)
        return raw[-max_chars:] if len(raw) > max_chars else raw

    async def send_command(self, command: str):
        """Send a command to the switch via the bridge."""
        if self._send_cb:
            await self._send_cb(command + "\r\n")
