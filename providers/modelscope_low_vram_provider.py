#!/usr/bin/env python3
"""Compatibility entrypoint for the pinned low-VRAM ModelScope provider.

The implementation moved to ``modelscope_low_vram_provider_v2.py`` so the
existing Windows launcher and external contracts keep the same stable path.
"""

from modelscope_low_vram_provider_v2 import *  # noqa: F401,F403


if __name__ == "__main__":
    raise SystemExit(main())
