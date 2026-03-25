#!/usr/bin/env python3
"""
5320 Onboarding Agent — Local Bridge
Runs on the operator's Mac. Reads the USB-serial console port and
streams lines to the Railway server via WebSocket. Receives commands
back and writes them to the switch.

Usage:
    pip install -r requirements.txt
    python3 bridge.py --server wss://your-app.railway.app --token YOUR_TOKEN

    # Auto-detect port:
    python3 bridge.py --server wss://your-app.railway.app --token YOUR_TOKEN

    # Specify port:
    python3 bridge.py --server wss://your-app.railway.app --token YOUR_TOKEN --port /dev/cu.usbserial-A9VKJO11
"""
import argparse
import asyncio
import glob
import json
import os
import socket
import sys
import time
import threading

import serial
import websockets

BAUD = 115200
RECONNECT_DELAYS = [1, 2, 4, 8, 16, 30]  # seconds


def detect_port():
    patterns = ["/dev/cu.usbserial-*", "/dev/cu.usbmodem*", "/dev/ttyUSB*", "/dev/ttyACM*"]
    found = []
    for p in patterns:
        found.extend(glob.glob(p))
    if not found:
        print("ERROR: No USB-serial port detected. Is the cable plugged in?")
        sys.exit(1)
    if len(found) == 1:
        return found[0]
    print("Multiple serial ports found:")
    for i, p in enumerate(found):
        print(f"  {i+1}. {p}")
    choice = input("Choose [1]: ").strip() or "1"
    return found[int(choice) - 1]


class Bridge:
    def __init__(self, server_url: str, token: str, port: str):
        self.server_url = server_url
        self.token = token
        self.port = port
        self._serial: serial.Serial | None = None
        self._ws = None
        self._send_queue: asyncio.Queue = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = True

    def _open_serial(self):
        """Open serial port, killing any process holding it first."""
        try:
            self._serial = serial.Serial(self.port, BAUD, timeout=0.1)
            print(f"[bridge] Serial port open: {self.port} @ {BAUD}")
        except serial.SerialException as e:
            print(f"[bridge] Cannot open {self.port}: {e}")
            print("[bridge] Try: lsof -t " + self.port + " | xargs kill")
            sys.exit(1)

    def _serial_reader(self):
        """Background thread: reads serial bytes, queues decoded lines."""
        buf = b""
        while self._running:
            if self._serial and self._serial.is_open:
                try:
                    chunk = self._serial.read(256)
                    if chunk:
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            decoded = line.decode("utf-8", errors="replace").rstrip("\r")
                            if self._loop:
                                asyncio.run_coroutine_threadsafe(
                                    self._send_queue.put(
                                        json.dumps({
                                            "type": "serial_line",
                                            "line": decoded,
                                            "ts": time.time(),
                                        })
                                    ),
                                    self._loop,
                                )
                except Exception:
                    pass
            else:
                time.sleep(0.1)

    async def _send_loop(self, ws):
        """Drains the send queue and writes to WebSocket."""
        while True:
            msg = await self._send_queue.get()
            try:
                await ws.send(msg)
            except Exception:
                # Put it back and bail — outer loop will reconnect
                await self._send_queue.put(msg)
                return

    async def _recv_loop(self, ws):
        """Receives messages from server and acts on them."""
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "serial_send":
                cmd = msg.get("command", "")
                if self._serial and self._serial.is_open:
                    self._serial.write(cmd.encode("utf-8"))

    async def run(self):
        self._loop = asyncio.get_running_loop()
        self._open_serial()

        # Start serial reader thread
        t = threading.Thread(target=self._serial_reader, daemon=True)
        t.start()

        ws_url = f"{self.server_url}/ws/bridge?token={self.token}"
        delay_idx = 0

        while self._running:
            try:
                print(f"[bridge] Connecting to {self.server_url} ...")
                async with websockets.connect(ws_url, ping_interval=20, ping_timeout=30) as ws:
                    self._ws = ws
                    delay_idx = 0
                    print("[bridge] Connected. Streaming console to server.")

                    # Send hello
                    await ws.send(json.dumps({
                        "type": "bridge_hello",
                        "bridge_id": socket.gethostname(),
                        "port": self.port,
                        "baud": BAUD,
                    }))

                    # Run send + recv concurrently
                    send_task = asyncio.create_task(self._send_loop(ws))
                    recv_task = asyncio.create_task(self._recv_loop(ws))
                    done, pending = await asyncio.wait(
                        [send_task, recv_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()

            except (websockets.exceptions.WebSocketException, OSError) as e:
                delay = RECONNECT_DELAYS[min(delay_idx, len(RECONNECT_DELAYS) - 1)]
                delay_idx += 1
                print(f"[bridge] Disconnected ({e}). Reconnecting in {delay}s ...")
                await asyncio.sleep(delay)
            except KeyboardInterrupt:
                print("\n[bridge] Stopped.")
                self._running = False


def main():
    parser = argparse.ArgumentParser(description="5320 Bridge — streams serial console to Railway")
    parser.add_argument("--server", required=True, help="WebSocket server URL, e.g. wss://app.railway.app")
    parser.add_argument("--token", required=True, help="Bridge auth token (set in Railway env as BRIDGE_TOKEN)")
    parser.add_argument("--port", help="Serial port (auto-detected if omitted)")
    args = parser.parse_args()

    port = args.port or detect_port()
    bridge = Bridge(server_url=args.server, token=args.token, port=port)

    try:
        asyncio.run(bridge.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
