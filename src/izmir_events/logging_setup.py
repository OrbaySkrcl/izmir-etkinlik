"""structlog yapılandırması."""

from __future__ import annotations

import logging
import sys

import structlog


def setup_logging(level: str = "INFO", *, json_logs: bool = False) -> None:
    """Uygulama loglarını yapılandırır.

    Railway gibi ortamlarda ``json_logs=True`` makine-okur çıktı verir.
    """
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )
    # Kütüphane logları gürültü yapmasın.
    for noisy in ("httpx", "httpcore", "telegram.ext.Updater", "apscheduler", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    renderer = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )
