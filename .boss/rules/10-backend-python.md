+++
title = "Backend Python Rules"
targets = ["code", "backend-python"]
modes = ["ask", "plan", "agent", "review"]
tags = ["backend", "python", "fastapi"]
+++

# Backend Python Rules

- Keep FastAPI contracts backward compatible when possible.
- Put new filesystem paths behind configuration rather than scattering literals.
- Prefer stdlib-first backend changes unless a new dependency is clearly justified.
- Keep memory, execution, and runtime state readable across restarts.
