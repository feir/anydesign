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
    p_build.add_argument(
        "--with-llm",
        action="store_true",
        help=(
            "Phase 2: enable LLM-driven prose generation + self-lint loop. "
            "Requires local oMLX (gemma4:26b) — vision call cannot fall back "
            "to cloud (cloud has no vision input). On unavailability, exits 2 "
            "with degraded_reason=omx_failover."
        ),
    )
    p_build.add_argument(
        "--llm-model",
        default="local/gemma4:26b",
        help="LLM model identifier (must be local/...). Default: local/gemma4:26b.",
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
    # Screenshot needed for: brand auto-detection (Phase 1.5b) OR --with-llm vision call
    needs_screenshot = (primary is None and not args.no_auto_primary) or args.with_llm
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

        # Phase 2.5: self-lint loop (only when --with-llm and --out set)
        if args.with_llm:
            if not args.out:
                print(
                    "FATAL: --with-llm requires --out (loop needs a writable target file)",
                    file=sys.stderr,
                )
                return 4
            if not screenshot_path or not os.path.exists(screenshot_path):
                print(
                    "FATAL: --with-llm requires a viewport screenshot; "
                    "set --primary or remove --no-auto-primary",
                    file=sys.stderr,
                )
                return 4
            return _run_self_lint_loop(
                out_path=args.out,
                screenshot_path=screenshot_path,
                registry=registry,
                payload=payload,
                args=args,
                source_url=payload.get("url") or args.url,
            )
        return 0
    finally:
        if cleanup_screenshot and screenshot_path and os.path.exists(screenshot_path):
            os.unlink(screenshot_path)


def _patch_design_md(
    out_path: str, overview_text: str, dos_text: str, component_yaml: str,
) -> None:
    """Replace LLM placeholders + inject component YAML into DESIGN.md.

    Operations (all idempotent):
    - Overview placeholder → overview_text (or skip if placeholder absent)
    - Do's & Don'ts placeholder → dos_text
    - Remaining placeholders (colors_prose / typography_prose / layout_prose /
      components_prose) → empty (deleted) — Phase 2 doesn't yet generate these
    - components_yaml → injected under YAML frontmatter `components:` key
      (creates the key if missing)
    """
    with open(out_path, encoding="utf-8") as f:
        text = f.read()

    # Placeholder substitutions
    text = text.replace(
        "<!-- LLM_PLACEHOLDER:overview -->", overview_text.strip(),
    )
    text = text.replace(
        "<!-- LLM_PLACEHOLDER:dos_donts -->", dos_text.strip(),
    )
    # Drop unfilled placeholders (sections kept as headers but body empty)
    for stub in (
        "<!-- LLM_PLACEHOLDER:colors_prose -->",
        "<!-- LLM_PLACEHOLDER:typography_prose -->",
        "<!-- LLM_PLACEHOLDER:layout_prose -->",
        "<!-- LLM_PLACEHOLDER:components_prose -->",
    ):
        text = text.replace(stub, "_(prose generation deferred to Phase 2.x)_")

    # Inject component YAML into frontmatter (if any)
    if component_yaml.strip():
        # Find frontmatter close `\n---\n` and inject before it.
        # YAML expects nested under `components:` key; we add the key if absent.
        from design_from_url.schema_fixer import split_frontmatter, join_frontmatter
        try:
            yaml_text, body = split_frontmatter(text)
        except ValueError:
            yaml_text, body = "", text
        if "components:" not in yaml_text:
            # Indent the LLM output and append under a new components: key
            indented = "\n".join("  " + line for line in component_yaml.strip().splitlines())
            yaml_text = yaml_text.rstrip("\n") + "\ncomponents:\n" + indented
        else:
            # Append under existing components: key (LLM output already has button-primary as its key)
            indented = "\n".join("  " + line for line in component_yaml.strip().splitlines())
            yaml_text = yaml_text.rstrip("\n") + "\n" + indented
        text = join_frontmatter(yaml_text, body)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)


def _run_self_lint_loop(
    *, out_path: str, screenshot_path: str, registry, payload: dict,
    args, source_url: str,
) -> int:
    """Phase 2.5 — orchestrate prose generation + self-lint convergence.

    Returns the exit code per design.md D6.1 enum mapping.
    """
    import datetime
    import os as _os
    from design_from_url import llm, component
    from design_from_url.prompt_loader import load_prompt
    from design_from_url.preflight import classify, lint_design_md_structured
    from design_from_url.run_report import RunReport
    from design_from_url.schema_fixer import (
        Pass2Unresolvable, apply_to_file,
    )
    from design_from_url.prose_retry import (
        build_retry_prompt, replace_overview_section,
    )

    # Build registry YAML block for prompts (cap at 12 colors per Phase 1.7.1)
    registry_lines = ["colors:"]
    for tok in registry.colors[:12]:
        registry_lines.append(f'  {tok.name}: "{tok.value}"')
    registry_yaml = "\n".join(registry_lines)

    report = RunReport(
        url=source_url,
        extracted_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        registry_size={
            "colors": len(registry.colors),
            "typography": len(registry.typography),
            "spacing": len(registry.spacing),
            "rounded": len(registry.rounded),
        },
        llm_model=args.llm_model,
    )

    # Stage 1 — Generate Overview prose
    try:
        overview_text = llm.generate(
            load_prompt("overview", registry=registry_yaml),
            image_path=screenshot_path,
            model=args.llm_model,
        )
    except llm.LLMUnavailable as exc:
        print(f"DEGRADED MODE: cloud has no image — abort. {exc}", file=sys.stderr)
        report.update_status("omx_failover")
        report.write(out_path + ".run_report.json")
        # Preserve partial DESIGN.md alongside (already written above)
        return report.exit_code

    # Stage 2 — Generate Do's & Don'ts prose
    try:
        dos_text = llm.generate(
            load_prompt("dos_donts", registry=registry_yaml,
                        role_mapping="(role mapping inferred from registry token names)"),
            image_path=screenshot_path,
            model=args.llm_model,
        )
    except llm.LLMUnavailable as exc:
        print(f"DEGRADED MODE during dos_donts: {exc}", file=sys.stderr)
        report.update_status("omx_failover")
        report.write(out_path + ".run_report.json")
        return report.exit_code

    # Stage 3 — Component identification (button-primary)
    candidates = component.select_top_candidates(payload)
    component_yaml = ""
    if candidates:
        crops_dir = _os.path.dirname(out_path) or "."
        crop_paths = component.crop_buttons_from_viewport(
            screenshot_path, candidates, crops_dir=crops_dir, pad=4,
        )
        if crop_paths:
            try:
                component_yaml = component.pick_button_primary(
                    candidates, crop_paths, registry_yaml,
                    llm_generate=llm.generate, model=args.llm_model,
                )
            except llm.LLMUnavailable as exc:
                print(f"DEGRADED MODE during component pick: {exc}", file=sys.stderr)
                report.update_status("omx_failover")
                report.write(out_path + ".run_report.json")
                return report.exit_code

    # Patch all sections into DESIGN.md
    _patch_design_md(out_path, overview_text, dos_text, component_yaml)

    # Stage 2 — Self-lint loop (initial + 2 retries)
    for round_idx in range(3):
        lint_result = lint_design_md_structured(out_path)
        report.findings_total = len(lint_result.findings)
        if lint_result.errors == 0:
            break  # PASS
        schema_findings, prose_findings = classify(lint_result.findings)
        report.schema_findings = len(schema_findings)
        report.prose_findings = len(prose_findings)

        # Pass 1+2 schema fix
        try:
            p1, p2 = apply_to_file(out_path, schema_findings, registry)
        except Pass2Unresolvable as exc:
            print(f"FATAL: required field unresolvable: {exc}", file=sys.stderr)
            report.update_status("required_field_unresolvable")
            report.write(out_path + ".run_report.json")
            return report.exit_code
        # Record actions
        from design_from_url.run_report import FixerAction
        for a in p1:
            report.fixer_actions.append(FixerAction(rule=a.rule, action=a.action, target=a.target))
        for a in p2:
            report.fixer_actions.append(FixerAction(rule=a.rule, action=a.action, target=a.target))

        # Prose retry (skip on last round to avoid pointless work)
        if prose_findings and round_idx < 2:
            try:
                with open(out_path, encoding="utf-8") as f:
                    prev = f.read()
                retry_prompt = build_retry_prompt(
                    load_prompt("overview", registry=registry_yaml),
                    prev, prose_findings,
                )
                new_overview = llm.generate(
                    retry_prompt, image_path=screenshot_path, model=args.llm_model,
                )
                with open(out_path, encoding="utf-8") as f:
                    text = f.read()
                text = replace_overview_section(text, new_overview)
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(text)
            except llm.LLMUnavailable as exc:
                print(f"DEGRADED MODE during prose retry: {exc}", file=sys.stderr)
                report.update_status("omx_failover")
                report.write(out_path + ".run_report.json")
                return report.exit_code

        report.retry_rounds = round_idx + 1

    # Final lint check
    final = lint_design_md_structured(out_path)
    if final.errors == 0:
        report.update_status(None)
    else:
        # Loop exhausted with errors remaining → DEGRADED
        report.update_status("prose_retry_exhausted")
    report.write(out_path + ".run_report.json")
    print(f"final_status={report.final_status} errors={final.errors} retries={report.retry_rounds}",
          file=sys.stderr)
    return report.exit_code
