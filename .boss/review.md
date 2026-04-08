# Boss Review Behavior

This file provides detailed review-mode guidance loaded as a prompt layer when Boss is in review mode. Core review rules are in `.boss/rules/30-review-mode.md`.

## Evidence Prioritization
- Prefer diff evidence first: what changed, what was added, what was removed.
- Then use indexed project context: call graphs, symbol definitions, import chains.
- Then use local docs and control files when verifying contract compliance.
- Do not rely on memory or assumptions about code that was not read during this review.

## Finding Format
Each finding should follow this structure:
- **Severity**: high / medium / low
- **Location**: file path with line number(s)
- **Evidence**: the specific code or diff that demonstrates the issue
- **Risk**: what goes wrong if this is not fixed
- **Fix**: a concrete recommended change (but do not apply it)

## Verification Expectations
- If the review covers backend changes, note whether `python3 -m compileall boss` and the regression harness were run.
- If the review covers client changes, note whether `swift build` passes.
- If verification was not performed, state that as a gap.

## Closing
- When no findings are found, state that explicitly.
- Note any residual risk: untested paths, missing edge cases, areas where behavior could not be verified.
- Do not pad the review with praise or filler. Findings and gaps are the deliverable.
