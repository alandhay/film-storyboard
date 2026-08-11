"""The Runway API surface we wrap.

These are intentionally *strings*, not an enum of models. The endpoint set is
stable (Runway adds models, not endpoints); the ``model`` value inside params is
opaque and validated by the API, never here. See docs/api-surface.md.
"""

from __future__ import annotations

from typing import Final

#: Asynchronous generation endpoints. Each is ``client.<name>.create(**params)``
#: returning ``{id}``; the result is polled via ``tasks.retrieve(id)``.
GENERATION_ENDPOINTS: Final[frozenset[str]] = frozenset(
    {
        "text_to_image",
        "image_to_video",
        "video_to_video",
        "text_to_speech",
        "sound_effect",
        "video_upscale",
    }
)

#: Hosted, higher-level ``client.recipes.<name>(**params)`` methods. Black boxes
#: exposed for pass-through; the gateway never depends on any specific pipeline.
RECIPE_ENDPOINTS: Final[frozenset[str]] = frozenset(
    {
        "ad_localization",
        "marketing_stock_image",
        "multi_shot_video",
        "product_ad",
        "product_campaign_image",
        "product_swap",
        "product_ugc",
    }
)


def is_generation_endpoint(endpoint: str) -> bool:
    return endpoint in GENERATION_ENDPOINTS


def is_recipe(name: str) -> bool:
    return name in RECIPE_ENDPOINTS
