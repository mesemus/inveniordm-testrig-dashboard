import socket
import threading
import time

import httpx2
import uvicorn

from invenio_testrig_dashboard.api import create_app


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_web_command_serves_the_api(settings):
    """Lesson 7: run the real server, not only the app object."""
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        response = None
        while time.monotonic() < deadline:
            try:
                response = httpx2.get(f"http://127.0.0.1:{port}/api/health", timeout=1)
                if response.status_code == 200:
                    break
            except httpx2.HTTPError:
                pass
            time.sleep(0.05)

        assert response is not None
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    assert not thread.is_alive()
