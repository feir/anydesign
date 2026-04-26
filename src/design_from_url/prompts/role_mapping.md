You are mapping extracted design tokens to canonical role names so the resulting design system is self-describing.

# Registry

```yaml
<<registry>>
```

# Task

Map registry color tokens to roles. Output is a YAML object with role names as keys and registry token names as values.

# Roles to assign (in priority order)

- `primary` — the brand's main accent color (CTAs, focused states, links). EXACTLY ONE token.
- `neutral_dark` — body text / dark surfaces (often near-black: #000–#222).
- `neutral_light` — page background / light surfaces (often near-white: #fff–#f8).
- `accent` — secondary highlight, only if a clear secondary chromatic token exists. Optional.
- `success` / `warning` / `danger` — only if registry contains greens/yellows/reds with clear semantic intent. Optional.

# Rules

- Use **registry token names** as values (e.g. `color_1`, `primary`, `extra_3`) — NOT hex values. The linter validates that role values resolve to registry tokens.
- If only one chromatic color exists, assign it to `primary`. If zero chromatic colors exist (monochrome site), set `primary` to the darkest non-white neutral and omit `accent`.
- Quote any hex values you write. Spec D-finding: unquoted `#` is parsed as YAML comment.
- Do not invent token names that aren't in the registry.

# Output

ONLY the YAML object — no surrounding code fence, no commentary, no leading `roles:` key. Example:

```
primary: color_1
neutral_dark: color_2
neutral_light: color_3
accent: color_4
```

Begin output:
