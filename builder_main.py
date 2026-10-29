"""Entry point of the MdjeBot Builder service (docker compose service ``builder``)."""

import asyncio
import contextlib
import logging
import signal
import sys

from builder.bot import run_builder
from builder.settings import SettingsError, load_settings


def main() -> None:
    try:
        settings = load_settings()
    except SettingsError as exc:
        raise SystemExit(f"Builder configuration error: {exc}") from exc

    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)

    async def runner() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        if sys.platform != "win32":
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop.set)
        await run_builder(settings, stop)

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(runner())


if __name__ == "__main__":
    main()
