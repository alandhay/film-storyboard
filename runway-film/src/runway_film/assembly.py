"""Assembly stage - plain concat via ffmpeg (anything richer is a STUB).

Phase 2 does a plain concatenation of downloaded clips with the ffmpeg concat
demuxer. Transitions, per-shot trims, and re-timing are out of scope. If ffmpeg is
not on PATH this raises a clear error rather than producing a broken cut.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from .models import LocalVideo


class AssemblyError(RuntimeError):
    pass


def assemble(clip_paths: Sequence[str], *, out_path: str | Path) -> LocalVideo:
    """Concatenate local clip files into one cut. Inputs must already be local
    (the gateway's ArtifactStore provides paths). Beyond plain concat is a stub."""
    if not clip_paths:
        raise AssemblyError("no clips to assemble")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise AssemblyError(
            "ffmpeg not found on PATH; install ffmpeg to assemble the cut "
            "(plain concat is the only assembly implemented in Phase 2)"
        )
    out = Path(out_path)
    with tempfile.NamedTemporaryFile(
        "w", suffix=".txt", delete=False, encoding="utf-8"
    ) as manifest:
        for path in clip_paths:
            # Absolute path: the concat demuxer resolves relative entries against
            # the manifest's own directory (a temp dir here), not the cwd.
            manifest.write(f"file '{Path(path).resolve().as_posix()}'\n")
        manifest_path = manifest.name
    try:
        subprocess.run(
            [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", manifest_path,
             "-c", "copy", str(out)],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:  # pragma: no cover - needs ffmpeg
        raise AssemblyError(f"ffmpeg concat failed: {exc.stderr.decode(errors='ignore')}") from exc
    finally:
        Path(manifest_path).unlink(missing_ok=True)
    return LocalVideo(path=str(out))
