+++
title = "macOS Client Rules"
targets = ["code", "general", "mac"]
modes = ["ask", "plan", "agent", "review"]
tags = ["swift", "swiftui", "macos", "frontend"]
+++

# macOS Client Rules

## SwiftUI Conventions
- Keep the SwiftUI app aligned with the backend API contract that actually ships in this repo. Do not add client code for endpoints that do not exist yet.
- Preserve the existing `APIClient` → `ChatViewModel` → View data flow. Do not introduce parallel state systems or alternative networking layers.
- Every new view or surface must handle empty, loading, populated, and error states. Do not ship a view that only handles the happy path.
- Match existing navigation patterns, spacing, and layout conventions. The client has an established visual language — extend it, do not reinvent it.

## Quality
- Avoid bland, default-looking UI. Boss is a personal tool — surfaces should feel intentional. But always preserve the established design system over introducing something new.
- Keep macOS-native behaviour: keyboard shortcuts, system menu integration, standard window management. Do not fight AppKit conventions.
- Prefer lightweight visible state (status indicators, inline messages) over silent failures or swallowed errors.

## Verification
- Always verify with `cd /Users/tj/boss/BossApp && swift build` before calling client work done.
- When a change touches both backend and client, verify both stacks.
- Keep all SwiftUI and macOS automation testing local. Do not move verification into remote-only workflows.
