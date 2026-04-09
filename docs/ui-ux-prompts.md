# BossApp UI/UX Implementation Prompts

Ordered prompts for Copilot. Each is self-contained. Run them in order — later prompts may depend on earlier ones.

---

## Prompt 1: Token Buffering — Fix O(n^2) Streaming Performance

```
You are editing /Users/tj/boss/BossApp.

The chat streaming has an O(n^2) performance bug. In ChatViewModel.swift, every SSE "text" event appends directly to the message content string:

    messages[messageIndex].content += content  (line ~191)

This fires a @Published update per token, causing a full SwiftUI view update for every single token. A 1000-token response triggers 1000 re-renders with 1000 string reallocations.

Fix this by adding a token buffer that flushes to the published message at most every 50ms:

1. Add a private property `private var tokenBuffer: String = ""` and a `private var flushTask: Task<Void, Never>?` to ChatViewModel.

2. In handleEvent for case "text", instead of directly appending to messages[messageIndex].content, append to tokenBuffer. If no flushTask is running, start one that waits 50ms then flushes:
   - Append tokenBuffer to messages[messageIndex].content
   - Clear tokenBuffer
   - Set flushTask = nil
   Store the messageIndex being streamed as `private var activeStreamMessageIndex: Int?` so the flush knows where to write.

3. In consumeStream, after the for-await loop ends, flush any remaining buffered tokens immediately (don't wait for timer).

4. In ChatView.swift, fix the previousRole(at:) function (line ~114). It currently calls `vm.messages.filter { $0.role != .system }` on every render for every message — O(messages^2). Instead, compute realMessages once at the top of the message list ForEach scope and pass the previous role via a local variable.

Constraints:
- Do not change the SSE parser or APIClient
- Do not change the message model
- Keep @MainActor on all mutations
- Test: rapid token streaming should feel smooth, not janky
```

---

## Prompt 2: Fix SSE Parser — Silent Data Loss

```
You are editing /Users/tj/boss/BossApp/Sources/APIClient.swift.

The SSE stream parser (line 653-719) has multiple data loss bugs:

1. **Silent JSON drops**: Line 688 uses `try?` which silently discards malformed JSON. Replace with proper error handling — log a warning (via os.Logger or print for now) and yield an error event so the UI knows something was lost.

2. **Type coercion**: Line 691 converts all JSON values to strings with `"\(value)"`. This loses type information — numeric fields like "attempt" become the string "5" instead of being parseable. Change the SSEEvent.data type from `[String: String]` to `[String: Any]`, and update all consumers in ChatViewModel.swift to extract values properly:
   - String values: `event.data["content"] as? String`
   - Int values: `event.data["attempt"] as? Int` (or fall back from String)
   - Keep backward compat: if a consumer expects String and gets Int, convert gracefully.

3. **Multiline JSON**: Line 680 splits the buffer by `\n`, which breaks if the JSON data field itself contains newlines (e.g. code content). Instead of splitting the full buffer by newlines and looking for "data: " prefixes line-by-line, extract the data field using proper SSE parsing:
   - SSE spec: an event is lines separated by \n, terminated by \n\n
   - Each line starts with "field: value" or is empty
   - The "data" field can span multiple lines (each prefixed with "data: ") — concatenate them with \n between
   - Process "event:" lines for the event type

4. **Buffer bounds**: Add a max buffer size (e.g., 1MB). If rawBuffer exceeds this without a double-newline, discard it and yield an error event. This prevents unbounded memory growth on malformed streams.

5. **Dropped events**: Line 701 silently drops events with empty type. Instead of dropping, log a warning with the event data for debugging.

Update the SSEEvent struct if needed. Update all call sites in ChatViewModel.swift to match the new data types.

Constraints:
- Do not change the backend SSE format
- Keep the AsyncStream<SSEEvent> interface
- Maintain backward compatibility with existing event handling in ChatViewModel
```

---

## Prompt 3: Chat Session Persistence

```
You are editing /Users/tj/boss/BossApp.

Chat messages are lost on app restart. Add local persistence for chat sessions.

Implementation:

1. Create a new file `BossApp/Sources/SessionStore.swift`:
   - Use a simple JSON file at `~/.boss/app-sessions/` (one file per session ID)
   - Struct `PersistedSession`: sessionId, messages (array of PersistedMessage), createdAt, updatedAt
   - Struct `PersistedMessage`: role, content, agent, thinkingContent, attachments, executionSteps (simplified — persist kind, name, state, output only, skip closures/callbacks)
   - `func save(sessionId: String, messages: [ChatMessage])` — writes JSON
   - `func load(sessionId: String) -> [ChatMessage]?` — reads JSON
   - `func listSessions() -> [(id: String, title: String, updatedAt: Date)]` — returns recent sessions sorted by updatedAt, title = first user message (truncated to 60 chars)
   - `func delete(sessionId: String)` — removes file

2. In ChatViewModel.swift:
   - After each message completes streaming (in consumeStream after isStreaming = false), call `SessionStore.shared.save(sessionId: sessionId, messages: messages)`
   - In init(), try to load the most recent session. If found, restore messages and sessionId. If not, start fresh.
   - Add `func loadSession(_ id: String)` that switches to a saved session
   - Add `@Published var savedSessions: [(id: String, title: String, updatedAt: Date)] = []` and refresh it on save/delete

3. In ContentView.swift sidebar:
   - Below "New Chat" button, add a "Recent" section listing savedSessions (show title + relative date)
   - Tapping a session calls vm.loadSession(id)
   - Add a swipe-to-delete or right-click context menu with "Delete Session"

Constraints:
- Use Codable for serialization
- Keep it simple — no Core Data, no SQLite, just JSON files
- Don't persist PermissionRequest closures — skip the onDecision callback when serializing
- Limit stored sessions to 50 most recent (prune oldest on save)
- Handle corrupted JSON gracefully (skip, don't crash)
```

---

## Prompt 4: Permission Prompt as Sticky Overlay

```
You are editing /Users/tj/boss/BossApp.

Permission prompts currently only appear inline within execution steps in the chat scroll. If the user scrolls up or is on a different surface, they miss the approval request entirely.

Add a sticky permission banner at the top of the app when any permission is pending:

1. In ContentView.swift, add an overlay above the main HStack:
   - When `vm.pendingPermissionCount > 0`, show a banner pinned to the top of the window
   - Banner content: amber/yellow background (Color.orange.opacity(0.15)), icon (exclamationmark.triangle), text "Approval needed — {count} pending", and a "View" button
   - "View" button scrolls to the pending permission in chat (set vm.selectedSurface = .chat, then scroll to the message with the pending request)
   - Banner should have a subtle slide-down animation on appear, slide-up on dismiss

2. Also add a macOS notification via NSUserNotification (or UNUserNotificationCenter) when a permission prompt arrives and the app is not frontmost:
   - Title: "Boss needs approval"
   - Body: The permission request description
   - Only fire once per approval (track sent notification IDs)

3. In ChatView.swift, when a permission prompt exists in an execution step, add a subtle pulsing ring animation around the PermissionPromptView to draw the eye.

Constraints:
- Don't remove the existing inline permission prompt — the banner is supplementary
- Don't block the UI — the banner is dismissible but reappears if still pending
- Keep the visual style consistent with the existing dark theme
```

---

## Prompt 5: Markdown — Inline Formatting and Syntax Highlighting

```
You are editing /Users/tj/boss/BossApp/Sources/MarkdownRenderer.swift.

The current markdown renderer only handles block-level elements (headings, paragraphs, lists, code fences, blockquotes, dividers). It has no support for inline formatting within paragraph text.

Add inline formatting support:

1. Add a new enum `InlineSpan` to represent inline segments:
   - .text(String)
   - .bold(String)
   - .italic(String)
   - .code(String)
   - .link(text: String, url: String)
   - .boldItalic(String)

2. Add a parser function `parseInline(_ text: String) -> [InlineSpan]` that handles:
   - **bold** and __bold__ → .bold
   - *italic* and _italic_ → .italic
   - ***bolditalic*** → .boldItalic
   - `code` → .code
   - [text](url) → .link
   - Anything else → .text
   Use a state machine or regex-based approach. Handle nested/adjacent spans correctly. Don't break on unmatched markers (treat as literal text).

3. Add a SwiftUI view `InlineTextView` that renders [InlineSpan] as a concatenated Text using + operator:
   - .bold → .bold() modifier
   - .italic → .italic() modifier
   - .code → monospace font, subtle background
   - .link → blue underlined text (on tap, open URL via NSWorkspace.shared.open)
   - .boldItalic → .bold().italic()

4. Update the existing paragraph and heading renderers to use InlineTextView instead of plain Text. Update list item text rendering too.

5. Add basic syntax highlighting for code blocks:
   - Parse the language tag from the code fence (```python, ```swift, etc.)
   - Apply keyword-based coloring for common languages (swift, python, javascript, typescript, rust, go, bash):
     - Keywords (func, let, var, class, def, import, return, if, else, for, while, etc.) → accent color or distinct color
     - Strings (quoted) → green-ish
     - Comments (// or # to end of line) → gray/dimmed
     - Numbers → distinct color
   - Keep it simple — regex-based token coloring, not a full parser
   - Render using AttributedString or concatenated styled Text views
   - Fall back to plain monospace for unknown languages

Constraints:
- Don't break existing block-level parsing
- Don't add external dependencies (no swift-markdown package)
- Keep the existing visual style (font sizes, spacing, colors from BossColor)
- Test with real markdown that includes mixed inline formatting: "This is **bold** and *italic* and `code` and [a link](https://example.com)"
```

---

## Prompt 6: Streaming Markdown (Incremental Rendering)

```
You are editing /Users/tj/boss/BossApp/Sources/ChatView.swift.

Currently during streaming, assistant messages show plain text. When streaming completes, the view crossfades to the parsed MarkdownBlocksView. This causes a jarring visual jump as content reformats.

Fix this by rendering markdown incrementally during streaming:

1. In the assistant message rendering section of ChatView (around line ~439 where streaming vs finalized is decided), instead of showing plain Text during streaming:
   - Parse the current content through MarkdownParser.parse() on every update
   - Render using MarkdownBlocksView
   - The parser already handles partial content gracefully (unclosed fences become paragraphs)

2. Performance concern: re-parsing on every token flush (from Prompt 1's buffer) could be expensive. Mitigate by:
   - Only re-parse when the buffered content is flushed (not per-character)
   - Cache the parsed result in the message view using @State
   - On content change, re-parse only if content length changed by more than a threshold (e.g., 20 chars) or on flush

3. Remove the crossfade/transition between streaming and finalized states — they should now look identical since both render through MarkdownBlocksView.

4. For code blocks during streaming: if a code fence is opened (```) but not yet closed, render the accumulated code in a code block style with a subtle "streaming" indicator (pulsing border or dimmed background).

Constraints:
- Keep MarkdownParser.parse() as the single source of truth for parsing
- Don't duplicate parsing logic
- The transition from streaming to finalized should be invisible to the user
```

---

## Prompt 7: Search Across Surfaces

```
You are editing /Users/tj/boss/BossApp.

Multiple surfaces have truncated lists with no search: Memory (8 items), Jobs (12 items), Permissions (all shown but no filter). Add search/filter to the three most important surfaces.

1. **MemoryView.swift** — Add a search bar at the top of the view:
   - Filter across all memory sections (user profile, preferences, recent memories, pending candidates)
   - Match against label, text, and category fields (case-insensitive contains)
   - When search is active, show a flat list of all matching items across sections instead of the sectioned layout
   - When search is empty, show the normal sectioned view
   - Remove the hardcoded `.prefix(8)` / `.prefix(10)` limits — show all items when no search, use a LazyVStack for performance

2. **JobsView.swift** — Add a search bar + status filter:
   - Search matches against job title, prompt, and status
   - Add a horizontal pill filter row: All | Running | Waiting | Completed | Failed
   - Remove the `.prefix(12)` limit
   - Use LazyVStack for the job list

3. **PermissionsView.swift** — Add a search bar:
   - Filter across tool name, title, scope label, and decision
   - Keep the grouped layout but hide empty groups when filtering

Implementation pattern for all three:
- Add `@State private var searchText: String = ""` to each view
- Use a TextField styled as a search bar (magnifyingglass icon, clear button, subtle background matching existing card style)
- Filter the data source using .filter { } on the search text
- Debounce is not needed for local filtering

Constraints:
- Keep the existing visual style (card backgrounds, spacing, typography)
- Don't change the ViewModel data fetching — filtering is view-local
- Use LazyVStack instead of VStack for filtered lists (performance)
```

---

## Prompt 8: Copy Buttons, Retry, and Attachment Preview

```
You are editing /Users/tj/boss/BossApp.

Three small UX improvements:

### A. Copy-to-clipboard buttons
In DiagnosticsView.swift, JobsView.swift, and anywhere a file path, ID, or URL is displayed as read-only text:
- Add a small clipboard icon button (doc.on.doc) next to the value
- On tap, copy the value to NSPasteboard.general
- Show a brief "Copied" tooltip or change the icon to a checkmark for 1.5 seconds
- Keep it visually subtle (gray icon, same size as secondary text)

### B. Retry button for failed execution steps
In ChatView.swift, in the execution step rendering, when a step has state `.failure`:
- Add a "Retry" button next to the error output
- On tap, re-send the last user message (call vm.send() with the same text)
- Style: small capsule button matching the existing button styles, with arrow.clockwise icon

### C. Attachment preview
In ChatView.swift, in the user message attachment rendering:
- For image attachments (file extension is jpg, jpeg, png, gif, webp, heic): show an inline thumbnail (max 200px wide, aspect fit) using AsyncImage or Image from file URL
- For other file types: show a file type icon (doc.fill for text, curlybraces for code, doc.richtext for markdown, paperclip for other)
- On click of any attachment, open the file in Finder: NSWorkspace.shared.activateFileViewerSelecting([url])

Constraints:
- Keep visually subtle — these are utility features, not primary UI
- Don't add external dependencies
- Use SF Symbols for all icons
```

---

## Prompt 9: Settings Panel

```
You are editing /Users/tj/boss/BossApp.

There is no settings screen. Add one.

1. Add `AppSurface.settings` case to the enum in Models.swift.

2. Create `BossApp/Sources/SettingsView.swift`:

   Layout: Scrollable VStack with grouped sections matching the existing card style (white opacity background, 1px border, rounded corners).

   **Appearance section:**
   - Font size slider (12-18pt, stored in @AppStorage("bossFontSize"), default 15)
   - This should be read by all text rendering as a base size

   **Chat section:**
   - Default work mode picker (Ask/Plan/Agent/Review, stored in @AppStorage)
   - Default execution style picker (Single Pass/Iterative, stored in @AppStorage)
   - Toggle: "Auto-scroll during streaming" (default on)
   - Toggle: "Show thinking content by default" (default off)

   **Memory section:**
   - Toggle: "Auto-inject memory into prompts" (informational — shows current backend setting, links to .boss/config.toml)
   - Display: auto-approve threshold (read from system status)

   **Backend section:**
   - Display: API base URL (read-only, from APIClient)
   - Display: API port
   - Button: "Restart Backend" — calls LocalBackendBootstrapper to restart
   - Button: "Open Config" — opens .boss/config.toml in default editor

   **About section:**
   - App version (from Bundle.main)
   - Backend version (from system status)
   - Link: "Open Data Directory" — opens ~/.boss in Finder

3. Add a gear icon button in the sidebar (bottom) that sets vm.selectedSurface = .settings.

4. Wire up the @AppStorage values:
   - In ChatView, read bossFontSize and apply to message text
   - In ChatViewModel.init(), read default mode and execution style from @AppStorage

Constraints:
- Use @AppStorage for all user preferences (persists via UserDefaults automatically)
- Keep the existing dark theme style
- Settings that control backend behavior should be display-only with guidance to edit config.toml
- Don't add a light mode toggle yet (save for later)
```

---

## Prompt 10: Accessibility Pass

```
You are editing /Users/tj/boss/BossApp.

The app has zero accessibility support. Add VoiceOver labels, keyboard navigation, and basic accessibility across all views.

1. **Icon buttons** — Every button that uses only an SF Symbol (no visible text label) needs an .accessibilityLabel:
   - ChatView: plus button ("Attach file"), send button ("Send message"), stop button ("Stop generation"), clock button ("Launch background job"), chevron toggle ("Toggle thinking")
   - ContentView sidebar: "New Chat", "Scan System", each surface button (name of surface)
   - JobsView: refresh ("Refresh jobs"), cancel, resume, take over buttons
   - MemoryView: delete buttons ("Delete memory"), approve/reject buttons
   - PermissionsView: revoke button ("Revoke permission")
   - Every other icon-only button across all views

2. **Permission prompt** — Add .accessibilityElement(children: .contain) to PermissionPromptView. Add .accessibilityLabel to each decision button: "Allow once", "Always allow", "Deny".

3. **Message bubbles** — Each message should have .accessibilityElement(children: .combine) with a label combining role + content. Execution steps should be grouped as children.

4. **Status indicators** — Color-only status dots (green/red/yellow in DiagnosticsView, JobsView) need .accessibilityLabel describing the status ("Healthy", "Unavailable", etc.).

5. **Keyboard navigation**:
   - Add .focusable() to interactive cards (job list items, memory items, session list items)
   - Add keyboard shortcuts:
     - Cmd+N: New chat
     - Cmd+1 through Cmd+9: Switch surfaces
     - Cmd+K: Focus search (when search is added)
     - Escape: Return to chat from any surface

6. **Dynamic type**: Replace all hardcoded `.system(size: N)` font calls with a pattern that scales relative to a base size. Create a Typo extension:
   ```swift
   extension Font {
       static func boss(_ style: BossTextStyle) -> Font {
           // reads @AppStorage("bossFontSize") or uses system dynamic type
       }
   }
   ```
   This can be a follow-up if complex — at minimum, ensure text is selectable where appropriate.

Constraints:
- Don't change visual appearance — only add accessibility modifiers
- Use .accessibilityLabel, .accessibilityHint, .accessibilityValue as appropriate
- Test by turning on VoiceOver (Cmd+F5) and tabbing through the UI
```

---

## Prompt 11: Conversation Export and Message Actions

```
You are editing /Users/tj/boss/BossApp.

Add message-level actions and conversation export.

1. **Message context menu** — In ChatView.swift, add a .contextMenu to each message bubble:
   - "Copy Message" — copies content to clipboard
   - "Copy as Markdown" — copies with markdown formatting preserved
   - Separator
   - "Delete Message" — removes from vm.messages array (and persisted session if Prompt 3 is implemented)

2. **Conversation export** — Add an export button in the chat header area (or toolbar):
   - Menu with options: "Export as Markdown", "Export as Text"
   - On tap, build the export string:
     - Markdown: `## User\n\n{content}\n\n## Assistant ({agent})\n\n{content}\n\n---\n\n` per message
     - Text: `User: {content}\n\nAssistant: {content}\n\n` per message
   - Present NSSavePanel to let user choose save location
   - Default filename: "boss-chat-{date}.md" or ".txt"

3. **Edit last message** — When chat is idle (not streaming):
   - Double-clicking the last user message populates the input bar with that message's text
   - Remove the last user message and last assistant message from the array
   - User can edit and re-send
   - Only works for the most recent user message

Constraints:
- Context menu should work on right-click (standard macOS behavior)
- Export should handle messages with no content gracefully (skip them)
- Don't add edit capability for assistant messages
```

---

## Prompt 12: ViewModel Split — Reduce Monolith

```
You are editing /Users/tj/boss/BossApp/Sources/ChatViewModel.swift.

This file is 1124 lines with 57 @Published properties covering chat, sidebar, jobs, reviews, workers, deploys, preview, diagnostics, memory, and permissions. Split it into focused modules while keeping the single source of truth pattern.

1. Keep ChatViewModel as the main @ObservableObject that the app passes via @EnvironmentObject.

2. Extract surface-specific state and logic into sub-objects owned by ChatViewModel:

   - `JobsState` (ObservableObject): jobs, selectedJob, selectedJobLog, jobsRefreshError, and all job-related methods (refreshJobs, cancelJob, resumeJob, etc.)
   - `ReviewState` (ObservableObject): reviewCapabilities, reviewHistory, selectedReviewRun, review target fields, isRunningReview, reviewRefreshError, and review methods
   - `WorkersState` (ObservableObject): workPlans, selectedWorkPlan, workersRefreshError, and worker methods
   - `DeployState` (ObservableObject): deployStatus, deployments, selectedDeployment, deployRefreshError, and deploy methods
   - `MemoryState` (ObservableObject): memoryOverview, memoryStats, memoryRefreshError, and memory methods

3. ChatViewModel owns these as @Published properties:
   ```swift
   @Published var jobsState = JobsState()
   @Published var reviewState = ReviewState()
   @Published var workersState = WorkersState()
   @Published var deployState = DeployState()
   @Published var memoryState = MemoryState()
   ```

4. Update each surface view to access the sub-state:
   - `vm.jobsState.jobs` instead of `vm.jobs`
   - `vm.reviewState.reviewHistory` instead of `vm.reviewHistory`
   - etc.

5. Create separate files for each state object:
   - `BossApp/Sources/State/JobsState.swift`
   - `BossApp/Sources/State/ReviewState.swift`
   - `BossApp/Sources/State/WorkersState.swift`
   - `BossApp/Sources/State/DeployState.swift`
   - `BossApp/Sources/State/MemoryState.swift`

6. Keep chat-specific state (messages, inputText, isLoading, sessionId, etc.) in ChatViewModel directly — that's its core responsibility.

Constraints:
- Do not break any existing functionality
- All state objects must be @MainActor
- Keep the @EnvironmentObject pattern — views still access `vm` and drill into sub-states
- Each sub-state file should have the methods that were previously in ChatViewModel
- The APIClient.shared reference can be used directly in sub-state objects
```

---

## Prompt 13: Polish Pass — Visual Consistency

```
You are editing /Users/tj/boss/BossApp.

Apply a visual consistency pass across all surfaces:

1. **Standardize button styles** — Create reusable button components in a new file `BossApp/Sources/Components.swift`:
   - `BossPrimaryButton(title:action:)` — red accent background, white text, capsule shape (for destructive/important actions like Cancel, Deny)
   - `BossSecondaryButton(title:icon:action:)` — white 0.08 opacity background, white text, rounded rect (for standard actions like Refresh, Allow Once)
   - `BossTertiaryButton(title:action:)` — no background, white 0.55 text (for subtle actions like Revoke, Remove)
   Replace all ad-hoc button styles across views with these three components.

2. **Standardize card component** — Create `BossCard` view:
   ```swift
   struct BossCard<Content: View>: View {
       let content: () -> Content
       var body: some View {
           content()
               .padding(14)
               .background(RoundedRectangle(cornerRadius: 12).fill(Color.white.opacity(0.035)))
               .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.white.opacity(0.06), lineWidth: 1))
       }
   }
   ```
   Replace all manually-constructed card backgrounds across DiagnosticsView, MemoryView, JobsView, ReviewView, PreviewView, DeployView, WorkersView.

3. **Tooltips** — Add .help() to every icon-only button across all views. Examples:
   - Refresh buttons: "Refresh"
   - Copy buttons: "Copy to clipboard"
   - Delete/revoke: "Remove" / "Revoke"
   - Navigation buttons: surface name

4. **Status pill consistency** — Create `StatusPill(text:color:)` view:
   - Capsule with text, colored background at 0.15 opacity, text in full color
   - Use for job status, worker state, deployment status, review severity
   - Replace all ad-hoc capsule/pill implementations

5. **Spacing audit** — Ensure all VStacks use consistent spacing:
   - Section gaps: 24
   - Card internal spacing: 12
   - Label-to-value: 6
   - Between cards: 16

Constraints:
- Don't change functionality, only visual presentation
- Keep the existing BossColor palette
- All new components go in Components.swift
- Run `swift build` after to verify
```

---

## Execution Order

**Phase 1 — Fix what's broken (Prompts 1-2):**
Performance and data integrity. Do these first because everything else builds on a working foundation.

**Phase 2 — Core UX (Prompts 3-6):**
Session persistence, permission visibility, markdown quality, streaming markdown. These make the app usable day-to-day.

**Phase 3 — Discoverability (Prompts 7-9):**
Search, utility buttons, settings. These make features findable.

**Phase 4 — Quality (Prompts 10-13):**
Accessibility, export, architecture cleanup, visual polish. These make it feel finished.

Each prompt is independent enough to be a single Copilot session. Verify with `cd /Users/tj/boss/BossApp && swift build` after each one.
