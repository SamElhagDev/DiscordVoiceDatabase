"""Gateway reconnect resilience tweaks.

The patch is intentionally defensive: if discord.py's internals ever change
shape, it logs a warning and leaves the stock backoff in place rather than
breaking startup. Worst case is therefore identical to today's behaviour.
"""

import logging

import discord.backoff
import discord.client

logger = logging.getLogger("discord_bot")


def cap_reconnect_backoff(max_exp: int = 5) -> None:
    """Cap discord.py's gateway reconnect backoff to ~``2**max_exp`` seconds.

    Default ``max_exp=5`` bounds the retry delay to ~32s (vs. discord.py's stock
    ceiling of ~1024s).
    """
    try:
        base_cls = discord.backoff.ExponentialBackoff

        class _BoundedExponentialBackoff(base_cls):
            def __init__(self, base: int = 1, *, integral: bool = False):
                super().__init__(base, integral=integral)
                self._max = max_exp

        # Verify the cap actually took effect before swapping it in.
        if getattr(_BoundedExponentialBackoff(), "_max", None) != max_exp:
            raise RuntimeError("backoff cap did not take effect")

        discord.client.ExponentialBackoff = _BoundedExponentialBackoff
        logger.info("Reconnect backoff capped: max retry delay ~%ds", 2 ** max_exp)
    except Exception:
        logger.warning(
            "Could not cap reconnect backoff — discord.py internals may have "
            "changed; using default backoff.",
            exc_info=True,
        )
