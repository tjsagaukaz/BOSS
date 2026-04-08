+++
title = "macOS Client Rules"
targets = ["code", "macos-client"]
modes = ["ask", "plan", "agent", "review"]
tags = ["swift", "swiftui", "macos"]
+++

# macOS Client Rules

- Keep the SwiftUI app aligned with the backend contract that actually ships in this repo.
- Prefer lightweight visible state over silent failures.
- Keep macOS validation local; do not move SwiftUI or automation verification into remote-only flows.
- Preserve the existing app state surfaces instead of introducing parallel state systems.
