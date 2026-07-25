#!/usr/bin/env python3
"""Stable entrypoint for the self-healing pinned ModelScope proof provider.

The exact model loading and render implementation remains in
``modelscope_low_vram_provider_v2.py``. The resilient wrapper starts health
immediately, retries failed model loads with bounded backoff, preserves the
existing launcher path used by Windows and external contracts, and returns
HTTP 503 while the real model is still unavailable.
"""

from modelscope_ready_aware_provider import *  # noqa: F401,F403


if __name__ == "__main__":
    raise SystemExit(main())
