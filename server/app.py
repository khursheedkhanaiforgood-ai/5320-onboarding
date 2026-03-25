"""
FastAPI application — entry point for Railway deployment.
Serves the web UI and handles WebSocket connections from bridge and browser.
"""
import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

from .config import BRIDGE_TOKEN
from .serial_proxy import SerialProxy
from .session_manager import SessionManager

app = FastAPI(title="5320 Onboarding Agent")

# Mount static files (web UI)
_static = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_static)), name="static")

# ── Shared state ──────────────────────────────────────────────────────────────
_proxy = SerialProxy()
_ui_clients: set[WebSocket] = set()
_session: SessionManager | None = None
_session_task: asyncio.Task | None = None


async def _broadcast(msg: dict):
    """Send a message to all connected web UI clients."""
    dead = set()
    for ws in _ui_clients:
        try:
            await ws.send_json(msg)
        except Exception:
            dead.add(ws)
    _ui_clients.difference_update(dead)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return (_static / "index.html").read_text()


@app.get("/health")
async def health():
    return {"status": "ok", "bridge_connected": _proxy._send_cb is not None}


# ── Bridge WebSocket ──────────────────────────────────────────────────────────

@app.websocket("/ws/bridge")
async def ws_bridge(ws: WebSocket):
    global _session, _session_task

    token = ws.query_params.get("token", "")
    if token != BRIDGE_TOKEN:
        await ws.close(code=4001)
        return

    await ws.accept()
    await _broadcast({"type": "bridge_status", "status": "connected"})

    # Wire up the send callback
    async def send_to_bridge(command: str):
        try:
            await ws.send_json({"type": "serial_send", "command": command})
        except Exception:
            pass

    _proxy.set_send_callback(send_to_bridge)

    # Start session
    if _session_task is None or _session_task.done():
        _session = SessionManager(_proxy, _broadcast)
        _session_task = asyncio.create_task(_session.run())

    try:
        async for raw in ws.iter_text():
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if msg.get("type") == "serial_line":
                line = msg.get("line", "")
                _proxy.ingest_line(line)

            elif msg.get("type") == "bridge_hello":
                await _broadcast({
                    "type": "bridge_hello",
                    "port": msg.get("port"),
                    "bridge_id": msg.get("bridge_id"),
                })

    except WebSocketDisconnect:
        pass
    finally:
        _proxy.set_send_callback(None)
        await _broadcast({"type": "bridge_status", "status": "disconnected"})


# ── UI WebSocket ──────────────────────────────────────────────────────────────

@app.websocket("/ws/ui")
async def ws_ui(ws: WebSocket):
    await ws.accept()
    _ui_clients.add(ws)
    try:
        async for raw in ws.iter_text():
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if msg.get("type") == "prompt_response" and _session:
                _session.receive_prompt_response(
                    msg.get("prompt_id", ""),
                    msg.get("value", ""),
                )

            elif msg.get("type") == "manual_command" and _proxy._send_cb:
                cmd = msg.get("command", "")
                if cmd:
                    await _proxy.send_command(cmd)

    except WebSocketDisconnect:
        pass
    finally:
        _ui_clients.discard(ws)
