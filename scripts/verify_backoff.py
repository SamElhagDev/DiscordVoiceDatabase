"""Verify the gateway reconnect-backoff cap is effective.

Applies ``cap_reconnect_backoff`` and confirms the patched backoff never returns
a delay above the cap across many samples. No network or bot token required.

Run from anywhere:  python scripts/verify_backoff.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord.client  # noqa: E402

from utils.reconnect import cap_reconnect_backoff  # noqa: E402

MAX_EXP = 5
CAP = 2 ** MAX_EXP
SAMPLES = 2000


def main() -> int:
    cap_reconnect_backoff(MAX_EXP)

    backoff = discord.client.ExponentialBackoff()
    worst = 0.0
    for _ in range(SAMPLES):
        delay = backoff.delay()
        worst = max(worst, delay)
        if delay > CAP:
            print(f"FAIL: delay {delay:.2f}s exceeded cap {CAP}s")
            return 1

    print(f"OK: {SAMPLES} delays all <= {CAP}s (observed max {worst:.2f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
