#!/usr/bin/env python3
"""Stable entrypoint for the self-healing pinned ModelScope proof provider.

The exact model loading and render implementation remains in
``modelscope_low_vram_provider_v2.py``. The resilient wrapper starts health
immediately, retries failed model loads with bounded backoff, and preserves the
existing launcher path used by Windows and external contracts.
"""

from modelscope_resilient_provider import *  # noqa: F401,F403


if __name__ == "__main__":
    raise SystemExit(main())
