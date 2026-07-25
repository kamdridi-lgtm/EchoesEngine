#!/usr/bin/env python3
"""Stable entrypoint for the self-healing pinned ModelScope proof provider.

The exact model loading and render implementation remains in
``modelscope_low_vram_provider_v2.py``. The resilient provider starts health
immediately, retries failed model loads with bounded backoff, preserves the
existing launcher path used by Windows and external contracts, and uses the
ready-aware HTTP layer when the complete runtime package is present.
"""

from __future__ import annotations

import modelscope_resilient_provider as resilient
from modelscope_resilient_provider import *  # noqa: F401,F403

try:
    import modelscope_ready_aware_provider as ready_aware
except ModuleNotFoundError as error:
    if error.name != "modelscope_ready_aware_provider":
        raise
    ready_aware = None


def main() -> int:
    """Prefer the ready-aware runtime without breaking verified sparse contracts."""
    if ready_aware is not None:
        return ready_aware.main()
    return resilient.main()


if __name__ == "__main__":
    raise SystemExit(main())
