"""runway-gateway: domain-agnostic gateway over the Runway generative media API.

Public entry point today is the transport wrapper, :mod:`runway_gateway.api`. The
policy layer (cache, retry, budget, fan-out, durability) lands in
:mod:`runway_gateway.core` in Phase 2.
"""

from __future__ import annotations

__version__ = "0.1.0"
