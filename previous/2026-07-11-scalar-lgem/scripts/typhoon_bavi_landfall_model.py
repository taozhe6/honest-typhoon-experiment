#!/usr/bin/env python3
"""Compatibility entry point for the dynamic typhoon landfall model.

The filename is retained for existing commands. Storm identity, source IDs,
landfall geometry, and environmental forcing now live in
``typhoon_landfall_core`` and are resolved at runtime.
"""

from typhoon_landfall_core import main


if __name__ == "__main__":
    raise SystemExit(main())
