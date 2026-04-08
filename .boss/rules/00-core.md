+++
title = "Core Boss Rules"
targets = ["all"]
modes = ["ask", "plan", "agent", "review"]
tags = ["core", "product"]
always = true
+++

# Core Boss Rules

- Keep Boss local-first and product-native.
- Preserve existing API, SSE, and persistence compatibility unless the task explicitly includes the migration work.
- Prefer the smallest coherent change that fixes the root cause.
- Make runtime behavior debuggable with local diagnostics instead of hidden state.
