You are documenting Do's and Don'ts for a website's design system.

# Registry

```yaml
<<registry>>
```

# Role mapping

```yaml
<<role_mapping>>
```

# Task

Write 3 Do's and 3 Don'ts as markdown bullet points. Each item must reference a specific token from the registry.

# Rules

- Every bullet must contain at least one literal token reference: `{colors.primary}`, `{typography.h1}`, etc. Keep curly braces literal — the linter validates them.
- Tie each Do/Don't to a concrete designer-actionable behavior, not a vague principle. Examples:
  - Good: "Use `{colors.primary}` only for primary CTAs and focused states."
  - Bad: "Be consistent with primary color." (no token reference, no actionable rule)
- Don'ts should describe specific anti-patterns, not negations of the Do's.
- 3 Do's + 3 Don'ts. No more, no less.

# Output

Two `###`-level sections:

```
### Do's

- Use `{colors.primary}` only for [specific use].
- [...]
- [...]

### Don'ts

- Don't use `{colors.primary}` for [specific anti-pattern].
- [...]
- [...]
```

NO surrounding code fence. NO leading `## Do's and Don'ts` header (that's already in the template). Begin output:
