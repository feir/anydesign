You are identifying components in a website's design system from extracted button candidates.

# Registry

```yaml
<<registry>>
```

# Button candidates

The following buttons were detected in the top region of the viewport, ranked by area. Each entry includes the candidate index, observed background color, dimensions, and a cropped image:

<<candidates>>

# Task

Pick ONE candidate to designate as `button-primary` (the main call-to-action). Output its component definition in YAML.

# Rules

- Output `backgroundColor` as a registry reference: `"{colors.primary}"` or `"{colors.X}"` (replace X with an actual registry token name). Do NOT output a raw hex value — the linter requires registry-reference form for components.
- The literal `{colors.X}` syntax is YAML's reference convention; keep the curly braces.
- Quote the entire reference as a string: `backgroundColor: "{colors.primary}"`. The quotes prevent YAML from parsing `#` as a comment in raw-hex emergency fallback.
- Add a `color` field for foreground text. Use `"{colors.neutral_light}"` if the background is dark (luminance < 0.5), `"{colors.neutral_dark}"` if light. Adjust based on the cropped button's visible label color.
- If your chosen candidate's color is NOT in the registry, prefer the nearest registry color by appearance (the schema-fixer will normalize ΔE-nearest if needed). If genuinely none look close, mention this in a YAML comment.

# Selection heuristic

- Prefer chromatic candidates (high color saturation) over neutral.
- Prefer candidates whose color matches `colors.primary` from registry.
- For monochrome sites where all candidates are neutral, pick the largest-area button and map to `colors.neutral_dark`.

# Output

ONLY the YAML object for `button-primary` — no surrounding code fence, no commentary, no leading `components:` key. Example:

```
button-primary:
  backgroundColor: "{colors.primary}"
  color: "{colors.neutral_light}"
```

Begin output:
