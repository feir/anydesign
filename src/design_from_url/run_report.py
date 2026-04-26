"""run_report.json — observability contract for Phase 2.5 self-lint loop.

Single source of truth for run state. Schema documented in design.md D6.
Status mapping (degraded_reason → final_status → exit_code) per D6.1.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Literal


# D6.1 enum mapping: (degraded_reason → (final_status, exit_code))
# Phase 3a (BREAKING): HARD_FAIL was exit 2 in Phase 2; now exit 1.
# DEGRADED stays at exit 2. PASS stays at 0.
STATUS_MAP: dict[str | None, tuple[str, int]] = {
    None: ("PASS", 0),
    # HARD_FAIL set — no usable output, exit 1
    "omx_failover": ("HARD_FAIL", 1),
    "required_field_unresolvable": ("HARD_FAIL", 1),
    "url_parse_failed": ("HARD_FAIL", 1),
    "render_timeout": ("HARD_FAIL", 1),
    "lint_cli_missing": ("HARD_FAIL", 1),
    "registry_empty": ("HARD_FAIL", 1),
    # DEGRADED set — output exists but AC not fully met, exit 2
    "prose_retry_exhausted": ("DEGRADED", 2),
    "prose_partial": ("DEGRADED", 2),
}

DegradedReason = Literal[
    "omx_failover",
    "required_field_unresolvable",
    "url_parse_failed",
    "render_timeout",
    "lint_cli_missing",
    "registry_empty",
    "prose_retry_exhausted",
    "prose_partial",
] | None
FinalStatus = Literal["PASS", "HARD_FAIL", "DEGRADED"]


@dataclass(frozen=True)
class FixerAction:
    rule: str         # e.g. "broken-ref" — derived from the lint finding
    action: str       # "nearest" | "default" | "drop" | "promote-extra" | "re-emit"
    target: str       # field path the action mutated (e.g. "components.button-primary.backgroundColor")


@dataclass
class RunReport:
    """Per-run observability record. Written to out/<domain>/run_report.json
    next to DESIGN.md and viewport.png.

    `final_status` and `exit_code` are derived from `degraded_reason` via
    `STATUS_MAP` — the dataclass keeps them in sync via `update_status()`.
    """
    url: str
    extracted_at: str                    # ISO 8601
    registry_size: dict[str, int]        # {"colors": 12, "typography": 6, ...}
    llm_model: str                       # e.g. "local/gemma4:26b"
    findings_total: int = 0
    schema_findings: int = 0
    prose_findings: int = 0
    fixer_actions: list[FixerAction] = field(default_factory=list)
    retry_rounds: int = 0
    final_status: FinalStatus = "PASS"
    degraded_reason: DegradedReason = None
    exit_code: int = 0

    def update_status(self, degraded_reason: DegradedReason) -> None:
        """Set degraded_reason and derive final_status + exit_code from STATUS_MAP."""
        self.degraded_reason = degraded_reason
        self.final_status, self.exit_code = STATUS_MAP[degraded_reason]

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(asdict(self), indent=indent, ensure_ascii=False)

    def write(self, path: str) -> None:
        """Write to disk. Parent directory must exist (caller responsibility)."""
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json())
            f.write("\n")
