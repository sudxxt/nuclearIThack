from __future__ import annotations

"""Lightweight static server for Telegram WebApp files.

This module exposes a coroutine `start_webapp_server` that launches an
`aiohttp.web.TCPSite` serving the contents of the `donor_bot/webapp` folder
on `http://localhost:8080` (configurable via env/Settings).

It is intended to be started in the background alongside the aiogram bot so
that the *User agreement* mini-app is available even without a public domain.
"""

import asyncio
import logging
from pathlib import Path

from aiohttp import web

from donor_bot.config import settings

logger = logging.getLogger(__name__)


async def _create_static_app() -> web.Application:
    """Create aiohttp application that serves the `webapp` directory."""
    root_dir = Path(__file__).resolve().parent  # donor_bot/webapp
    app = web.Application()
    # Serve everything (HTML, JS, CSS, etc.) from the root of the directory
    app.router.add_static("/", str(root_dir), show_index=False)
    return app


async def start_webapp_server(port: int | None = None) -> None:
    """Launch the static file server.

    This coroutine **never returns** – it blocks until the event loop is
    cancelled. Run it with ``asyncio.create_task`` to keep it in background.
    """

    _port = port or getattr(settings, "LOCAL_WEBAPP_PORT", 8080)
    app = await _create_static_app()

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="127.0.0.1", port=_port)
    await site.start()

    logger.info("WebApp static files are being served at http://localhost:%d/", _port)

    # Keep the coroutine alive for the lifetime of the application.
    # The polling task will keep the loop running; we just wait forever.
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    # Позволяет запускать сервер командой
    #   python -m donor_bot.webapp.server
    # или
    #   python donor_bot/webapp/server.py
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(start_webapp_server())
    except (KeyboardInterrupt, SystemExit):
        pass 