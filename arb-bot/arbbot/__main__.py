"""Entry point: python -m arbbot"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

from .app import App
from .config.settings import get_settings


async def amain() -> int:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    log = logging.getLogger("arbbot")
    if not settings.telegram_bot_token or not settings.admin_ids:
        log.error("TELEGRAM_BOT_TOKEN and TELEGRAM_ADMIN_IDS are required: Telegram is the control interface")
        return 2

    app = App(settings)
    # one trading instance per deployment: a second one would double-allocate capital
    if await app.cache.get("instance"):
        log.error("another instance appears to be running (heartbeat present)")
        return 3
    await app.cache.set("instance", "1", ttl=30)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    from .bot.telegram import bot as tg

    await app.init()
    log.info("reconciled; mode=%s autotrade=%s venues=%s", app.cfg.mode, app.cfg.autotrade,
             [e.value for e in app.venues])
    app.start_background()
    tg_task = asyncio.create_task(tg.run(app), name="telegram")
    stop_task = asyncio.create_task(stop.wait())
    done, _ = await asyncio.wait({tg_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    code = 0
    if tg_task in done and tg_task.exception():
        log.error("telegram stopped: %s", tg_task.exception())
        code = 1
    tg_task.cancel()
    await asyncio.gather(tg_task, return_exceptions=True)
    await app.shutdown()
    return code


def main() -> None:
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
