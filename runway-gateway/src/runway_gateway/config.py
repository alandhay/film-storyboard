"""Secret/environment resolution for the live client (stdlib only).

The SDK reads ``RUNWAYML_API_SECRET``; this project's ``.env`` may instead use
``RUNWAY_API_KEY``. We accept either, and load a ``.env`` file ourselves rather
than depend on python-dotenv.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Env var names accepted for the Runway API secret, in priority order.
API_SECRET_ENV_VARS: tuple[str, ...] = ("RUNWAYML_API_SECRET", "RUNWAY_API_KEY")


def load_dotenv(path: str | Path = ".env", *, override: bool = False) -> None:
    """Load ``KEY=VALUE`` pairs from a ``.env`` file into ``os.environ``.

    A no-op if the file is absent. Existing vars are preserved unless ``override``.
    Supports ``export KEY=...`` and quoted values. Values are never logged.
    """
    file = Path(path)
    if not file.exists():
        return
    for raw in file.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if override or key not in os.environ:
            os.environ[key] = value


def resolve_api_secret(
    *, load_env_file: bool = True, dotenv_path: str | Path = ".env"
) -> str:
    """Return the Runway API secret from the environment (optionally loading .env).

    Raises ``RuntimeError`` with a clear message if none is set. The secret value
    itself is never included in the error.
    """
    if load_env_file:
        load_dotenv(dotenv_path)
    for var in API_SECRET_ENV_VARS:
        value = os.environ.get(var)
        if value:
            return value
    raise RuntimeError(
        "No Runway API secret found. Set one of "
        f"{', '.join(API_SECRET_ENV_VARS)} (e.g. in a .env file)."
    )
