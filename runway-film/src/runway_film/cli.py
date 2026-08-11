"""``runway-film`` CLI: cost (dry-run), keyframes (stop at gate), film (full run).

``print`` is used here (the presentation layer); everywhere else is structured
logging. ``cost`` never touches the network or needs a key. ``keyframes`` and
``film`` run live and spend credits - both preview cost and require ``--yes``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from runway_gateway.api import RunwayAPI
from runway_gateway.api.pricing import default_pricing_book
from runway_gateway.core import Budget, Gateway, LocalArtifactStore, SqliteCache

from .pipeline import plan_cost, run_film, run_keyframes
from .storyboard import Storyboard


def _live_gateway(cache_path: str, artifacts_dir: str, budget: float | None) -> Gateway:
    api = RunwayAPI.from_env()
    return Gateway(
        api,
        SqliteCache(cache_path),
        budget=Budget(budget) if budget is not None else None,
        store=LocalArtifactStore(artifacts_dir),
    )


def _print_cost(sb: Storyboard) -> float:
    lines = plan_cost(default_pricing_book(), sb)
    priced = 0.0
    print(f"{'stage':<18} {'endpoint':<16} {'model':<14} {'credits':>8}  detail")
    print("-" * 78)
    for line in lines:
        credits = "unpriced" if line.credits is None else f"{line.credits:>8.2f}"
        if line.credits is not None:
            priced += line.credits
        print(f"{line.stage:<18} {line.endpoint:<16} {line.model:<14} {credits:>8}  {line.detail}")
    print("-" * 78)
    print(f"priced subtotal: {priced:.2f} credits  (~${priced * 0.01:.2f})")
    unpriced = [line.stage for line in lines if line.credits is None]
    if unpriced:
        print(f"UNPRICED (excluded from subtotal): {', '.join(unpriced)}")
    return priced


def cmd_cost(args: argparse.Namespace) -> int:
    sb = Storyboard.load(args.storyboard)
    _print_cost(sb)
    return 0


def cmd_keyframes(args: argparse.Namespace) -> int:
    sb = Storyboard.load(args.storyboard)
    priced = _print_cost(sb)
    if not args.yes:
        print(f"\nThis will spend up to ~{priced:.2f} credits on stills. Re-run with --yes.")
        return 1
    gw = _live_gateway(args.cache, args.out, args.budget)
    _bible, keyframes = run_keyframes(gw, sb)
    manifest: dict[str, bool] = {}
    print("\nkeyframes:")
    for kf in keyframes:
        print(f"  {kf.shot_id}: {kf.ref.url}")
        manifest[kf.shot_id] = False
    approvals_path = Path(args.out) / "approvals.json"
    approvals_path.parent.mkdir(parents=True, exist_ok=True)
    approvals_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nApproval manifest written to {approvals_path}")
    print("Set shots to true, then: runway-film film <storyboard> --approvals "
          f"{approvals_path} --yes")
    return 0


def _load_approvals(args: argparse.Namespace, sb: Storyboard) -> set[str]:
    if args.approve_all:
        return {shot.id for shot in sb.shots}
    if args.approvals:
        data = json.loads(Path(args.approvals).read_text(encoding="utf-8"))
        return {shot_id for shot_id, ok in data.items() if ok}
    return set()


def cmd_film(args: argparse.Namespace) -> int:
    sb = Storyboard.load(args.storyboard)
    approved = _load_approvals(args, sb)
    if not approved:
        print("No approved shots. Pass --approvals <file> (with true values) or --approve-all.")
        return 1
    priced = _print_cost(sb)
    if not args.yes:
        print(
            f"\nThis will spend up to ~{priced:.2f} credits (video dominates). Re-run with --yes."
        )
        return 1
    gw = _live_gateway(args.cache, args.out, args.budget)
    result = run_film(gw, sb, approved)
    print(f"\nclips ({len(result.clips)} ok, {len(result.errors)} failed):")
    for clip in result.clips:
        print(f"  {clip.shot_id}: {clip.ref.url}")
    for err in result.errors:
        print(f"  FAILED {err.endpoint}: {err.error}")
    return 0 if not result.errors else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="runway-film", description="Storyboard-to-film pipeline")
    parser.add_argument("--cache", default="runway-cache.sqlite", help="SQLite cache path")
    parser.add_argument("--out", default="artifacts", help="artifact output dir")
    parser.add_argument("--budget", type=float, default=None, help="max spend (credits)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_cost = sub.add_parser("cost", help="dry-run cost estimate (no spend, no key)")
    p_cost.add_argument("storyboard")
    p_cost.set_defaults(func=cmd_cost)

    p_kf = sub.add_parser("keyframes", help="generate stills, then STOP at the approval gate")
    p_kf.add_argument("storyboard")
    p_kf.add_argument("--yes", action="store_true", help="confirm spend")
    p_kf.set_defaults(func=cmd_keyframes)

    p_film = sub.add_parser("film", help="full pipeline (needs approvals)")
    p_film.add_argument("storyboard")
    p_film.add_argument("--approvals", help="approvals JSON from `keyframes`")
    p_film.add_argument("--approve-all", action="store_true", help="approve every shot (demo)")
    p_film.add_argument("--yes", action="store_true", help="confirm spend")
    p_film.set_defaults(func=cmd_film)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    func: Any = args.func
    return int(func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
