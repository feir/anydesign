"""Command-line entry point for design-from-url."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from design_from_url import __version__
from design_from_url.constants import DESIGN_MD_NPM_PACKAGE


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="design-from-url",
        description=(
            "Extract design tokens from a URL and emit a DESIGN.md file "
            "conformant to the @google/design.md spec."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # preflight
    p_preflight = sub.add_parser(
        "preflight",
        help=f"Verify {DESIGN_MD_NPM_PACKAGE} is reachable via npx.",
    )
    p_preflight.set_defaults(func=_cmd_preflight)

    # extract
    p_extract = sub.add_parser(
        "extract",
        help="Render a URL and dump raw token extraction (JSON to stdout).",
    )
    p_extract.add_argument("url", help="URL to render and extract from.")
    p_extract.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Page-load timeout in seconds (default: 30).",
    )
    p_extract.add_argument(
        "--no-consent-dismiss",
        action="store_true",
        help="Skip consent overlay dismissal (debug aid).",
    )
    p_extract.set_defaults(func=_cmd_extract)

    # aggregate
    p_agg = sub.add_parser(
        "aggregate",
        help="Render + extract + cluster colors/spacing/rounded; emit intermediate JSON.",
    )
    p_agg.add_argument("url", help="URL to render and extract from.")
    p_agg.add_argument("--timeout", type=int, default=30)
    p_agg.add_argument("--no-consent-dismiss", action="store_true")
    p_agg.add_argument(
        "--k-max",
        type=int,
        default=5,
        help="Cap on cluster count per length category (default: 5).",
    )
    p_agg.add_argument(
        "--delta-e",
        type=float,
        default=6.0,
        help="ΔE76 threshold for color dedupe merging (default: 6.0).",
    )
    p_agg.set_defaults(func=_cmd_aggregate)

    # build
    p_build = sub.add_parser(
        "build",
        help="End-to-end: extract → aggregate → registry → DESIGN.md draft.",
    )
    p_build.add_argument("url", help="URL to render and extract from.")
    p_build.add_argument(
        "--primary",
        metavar="HEX",
        help="Inject brand color override (e.g. #635bff). Bypasses the empty guard at 1 color.",
    )
    p_build.add_argument(
        "--out",
        metavar="PATH",
        help="Write DESIGN.md to this path (default: stdout).",
    )
    p_build.add_argument("--timeout", type=int, default=30)
    p_build.add_argument("--no-consent-dismiss", action="store_true")
    p_build.add_argument("--k-max", type=int, default=5)
    p_build.add_argument("--delta-e", type=float, default=6.0)
    p_build.add_argument(
        "--cap-colors",
        type=int,
        default=12,
        help="Max colors to emit in DESIGN.md front matter (default: 12). 0 disables.",
    )
    p_build.add_argument(
        "--no-auto-primary",
        action="store_true",
        help="Skip Phase 1.5b auto brand-color detection (saves a screenshot capture).",
    )
    p_build.set_defaults(func=_cmd_build)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    return int(args.func(args) or 0)


# ---- subcommand implementations ----

def _cmd_preflight(args: argparse.Namespace) -> int:
    from design_from_url.preflight import check_npx_design_md

    result = check_npx_design_md()
    if result.ok:
        print(f"OK: {DESIGN_MD_NPM_PACKAGE} reachable via npx", file=sys.stderr)
        return 0
    print(f"PREFLIGHT FAIL: {result.reason}", file=sys.stderr)
    print(
        f"Hint: run `npx --yes {DESIGN_MD_NPM_PACKAGE} --version` once "
        f"(needs network) or `npm install {DESIGN_MD_NPM_PACKAGE}` to "
        "warm the cache.",
        file=sys.stderr,
    )
    return 3


def _cmd_extract(args: argparse.Namespace) -> int:
    from design_from_url.extractor import extract_from_url

    payload = extract_from_url(
        url=args.url,
        timeout_s=args.timeout,
        dismiss_consent=not args.no_consent_dismiss,
    )
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def _cmd_aggregate(args: argparse.Namespace) -> int:
    from design_from_url.extractor import extract_from_url
    from design_from_url.aggregator import aggregate_spacing_and_rounded
    from design_from_url.colors import collect_color_strings, dedupe_colors

    payload = extract_from_url(
        url=args.url,
        timeout_s=args.timeout,
        dismiss_consent=not args.no_consent_dismiss,
    )
    lengths = aggregate_spacing_and_rounded(payload, k_max=args.k_max)
    color_clusters = dedupe_colors(
        collect_color_strings(payload), delta_e_threshold=args.delta_e,
    )
    out = {
        "url": payload.get("url"),
        "page_title": payload.get("page_title"),
        "html_size": payload.get("html_size"),
        "spacing": lengths["spacing"],
        "rounded": lengths["rounded"],
        "colors": [
            {
                "representative": c.representative,
                "frequency": c.frequency,
                "members_count": len(c.members),
                "members": list(c.members),
            }
            for c in color_clusters
        ],
        "_meta": {
            **payload.get("_meta", {}),
            "spacing_clusters": len(lengths["spacing"]),
            "rounded_clusters": len(lengths["rounded"]),
            "color_clusters": len(color_clusters),
        },
    }
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    import os
    import tempfile

    from design_from_url.extractor import extract_from_url
    from design_from_url.aggregator import aggregate_spacing_and_rounded
    from design_from_url.colors import collect_color_strings, dedupe_colors
    from design_from_url.registry import build_registry, RegistryGuardError
    from design_from_url.template import build_design_md
    from design_from_url.brand_color import detect_brand_color

    primary = args.primary
    needs_screenshot = primary is None and not args.no_auto_primary
    screenshot_path: str | None = None
    cleanup_screenshot = False
    if needs_screenshot:
        # If --out is set, place screenshot next to it for inspection;
        # otherwise use a temp file we delete after.
        if args.out:
            screenshot_path = os.path.splitext(args.out)[0] + ".png"
        else:
            fd, screenshot_path = tempfile.mkstemp(suffix=".png", prefix="dfu-")
            os.close(fd)
            cleanup_screenshot = True

    try:
        payload = extract_from_url(
            url=args.url,
            timeout_s=args.timeout,
            dismiss_consent=not args.no_consent_dismiss,
            screenshot_path=screenshot_path,
        )

        if primary is None and not args.no_auto_primary and screenshot_path:
            brand = detect_brand_color(
                image_path=screenshot_path,
                payload=payload,
                url=payload.get("url") or args.url,
            )
            if brand is not None:
                primary = brand.hex
                print(
                    f"auto-primary: {brand.hex} (source={brand.source}, "
                    f"confidence={brand.confidence:.2f})",
                    file=sys.stderr,
                )

        lengths = aggregate_spacing_and_rounded(payload, k_max=args.k_max)
        color_clusters = dedupe_colors(
            collect_color_strings(payload), delta_e_threshold=args.delta_e,
        )
        aggregated = {
            "spacing": lengths["spacing"],
            "rounded": lengths["rounded"],
            "colors": [
                {
                    "representative": c.representative,
                    "frequency": c.frequency,
                    "members": list(c.members),
                }
                for c in color_clusters
            ],
        }
        try:
            registry = build_registry(
                aggregated, payload, primary_override=primary,
            )
        except RegistryGuardError as e:
            print(f"FATAL: {e}", file=sys.stderr)
            return 4

        cap = args.cap_colors if args.cap_colors > 0 else None
        md = build_design_md(
            registry, source_url=payload.get("url") or args.url, cap_colors=cap,
        )
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(md)
            print(f"wrote {args.out}", file=sys.stderr)
        else:
            sys.stdout.write(md)
        return 0
    finally:
        if cleanup_screenshot and screenshot_path and os.path.exists(screenshot_path):
            os.unlink(screenshot_path)
