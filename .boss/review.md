# Boss Review Behavior

When Boss is in review mode:

- Start with findings ordered by severity.
- Include concrete file references when calling out issues.
- Prefer diff evidence first, then indexed project context and local docs verification when available.
- Each finding must include severity, file path, evidence, risk, and a recommended fix.
- Focus on bugs, regressions, unsafe behavior, and missing tests before style commentary.
- Do not emit style-only nits unless they hide a real bug.
- Do not auto-fix code in review mode.
- State explicitly when no findings were found, along with any residual risk or untested area.
