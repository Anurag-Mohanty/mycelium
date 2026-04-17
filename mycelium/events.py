"""Event emitter for real-time visualization and recording.

Bidirectional:
- Backend → Browser: emit() sends events via WebSocket + writes events.jsonl
- Browser → Backend: wait_for_client_message() receives user choices (budget tier, etc.)
"""

import asyncio
import http.server
import json
import threading
import time
import websockets
import webbrowser
from pathlib import Path

# Global state
_clients: set = set()
_server = None
_event_queue: asyncio.Queue = None
_events_file = None
_start_time: float = 0.0
_client_messages: asyncio.Queue = None  # messages FROM the browser
_http_server = None


async def _handler(websocket):
    """Handle a WebSocket client connection — receives messages from browser."""
    _clients.add(websocket)
    try:
        async for message in websocket:
            # Client sent us something (e.g., budget tier selection)
            if _client_messages:
                try:
                    data = json.loads(message)
                    await _client_messages.put(data)
                except json.JSONDecodeError:
                    pass
    finally:
        _clients.discard(websocket)


async def start_server(port: int = 8765, run_dir: str = None):
    """Start the WebSocket server, open visualizer, and begin recording."""
    global _server, _event_queue, _events_file, _start_time, _client_messages
    _event_queue = asyncio.Queue()
    _client_messages = asyncio.Queue()
    _start_time = time.time()

    if run_dir:
        events_path = Path(run_dir) / "events.jsonl"
        _events_file = open(events_path, "a")

    _server = await websockets.serve(_handler, "localhost", port)

    # Serve visualizer HTML over HTTP on port 8766
    project_root = str(Path(__file__).parent.parent)
    _start_http_server(project_root, 8766)

    webbrowser.open("http://localhost:8766/visualizer.html")

    asyncio.create_task(_broadcast_loop())
    return _server


def start_recording(run_dir: str):
    """Start recording events to file only (no WebSocket server)."""
    global _events_file, _start_time
    _start_time = time.time()
    if run_dir:
        events_path = Path(run_dir) / "events.jsonl"
        _events_file = open(events_path, "a")


def _start_http_server(directory: str, port: int = 8766):
    """Start a simple HTTP server to serve the visualizer HTML."""
    global _http_server

    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)
        def log_message(self, *args):
            pass  # suppress request logs

    try:
        _http_server = http.server.HTTPServer(("localhost", port), QuietHandler)
        thread = threading.Thread(target=_http_server.serve_forever, daemon=True)
        thread.start()
    except OSError:
        pass  # port in use, skip


async def _broadcast_loop():
    """Continuously broadcast queued events to all connected clients."""
    while True:
        event = await _event_queue.get()
        if event is None:
            break
        msg = json.dumps(event, default=str)
        if _clients:
            await asyncio.gather(
                *[client.send(msg) for client in _clients],
                return_exceptions=True,
            )


async def stop_server():
    """Shutdown the WebSocket server, HTTP server, and close the events file."""
    global _server, _events_file, _http_server
    if _event_queue:
        await _event_queue.put(None)
    if _server:
        _server.close()
        await _server.wait_closed()
    if _http_server:
        _http_server.shutdown()
        _http_server = None
    if _events_file:
        _events_file.close()
        _events_file = None


def emit(event_type: str, data: dict):
    """Emit an event — both to WebSocket (live) and events.jsonl (recording)."""
    event = {
        "type": event_type,
        "timestamp": time.time(),
        "elapsed_ms": int((time.time() - _start_time) * 1000) if _start_time else 0,
        **data,
    }

    if _events_file:
        try:
            _events_file.write(json.dumps(event, default=str) + "\n")
            _events_file.flush()
        except (ValueError, OSError):
            pass

    if _event_queue is not None:
        try:
            _event_queue.put_nowait(event)
        except asyncio.QueueFull:
            pass


async def wait_for_client_message(timeout: float = 600) -> dict | None:
    """Wait for a message from the browser (e.g., budget tier selection).

    Returns the message dict, or None if timeout or no WebSocket server.
    """
    if _client_messages is None:
        return None
    try:
        return await asyncio.wait_for(_client_messages.get(), timeout=timeout)
    except asyncio.TimeoutError:
        return None
