# Copilot Prompt: Replace SyntaxHighlighter with HighlightSwift

```
You are editing /Users/tj/boss/BossApp.

The code block syntax highlighter in MarkdownRenderer.swift is a hand-rolled regex tokenizer
that supports only 8 languages with basic keyword/string/comment/number coloring. Replace it
with the HighlightSwift package for proper 50+ language support with auto-detection.

## Step 1: Add the HighlightSwift dependency

In BossApp/Package.swift, add the HighlightSwift package and link it to the BossApp target.

Current file:

    // swift-tools-version:6.0
    import PackageDescription

    let package = Package(
        name: "BossApp",
        platforms: [.macOS(.v14)],
        targets: [
            .executableTarget(
                name: "BossApp",
                path: "Sources"
            )
        ]
    )

Update to:

    // swift-tools-version:6.0
    import PackageDescription

    let package = Package(
        name: "BossApp",
        platforms: [.macOS(.v14)],
        dependencies: [
            .package(url: "https://github.com/appstefan/HighlightSwift.git", from: "1.0.0"),
        ],
        targets: [
            .executableTarget(
                name: "BossApp",
                dependencies: [
                    .product(name: "HighlightSwift", package: "HighlightSwift"),
                ],
                path: "Sources"
            )
        ]
    )

## Step 2: Replace the SyntaxHighlighter enum

In BossApp/Sources/MarkdownRenderer.swift, find the entire SyntaxHighlighter enum
(starts at line ~345 with `enum SyntaxHighlighter {` and ends at line ~451 with the
closing brace after the `tokenize` function).

Replace it with a new implementation that uses HighlightSwift:

    import HighlightSwift

    // MARK: - Syntax Highlighting (HighlightSwift)

    enum SyntaxHighlighter {
        /// Shared highlighter instance — reused across calls to avoid re-initializing
        /// the JavaScript engine on every code block.
        private static let highlighter = Highlight()

        /// Synchronously highlight code and return a styled SwiftUI Text.
        /// Falls back to plain monospace on failure.
        static func highlightedText(_ code: String, language: String?) -> Text {
            // Try to get an AttributedString from HighlightSwift
            if let attributed = highlightSync(code, language: language) {
                return Text(attributed)
            }
            // Fallback: plain monospace text
            return Text(code)
                .foregroundColor(Color.white.opacity(0.82))
        }

        /// Run HighlightSwift synchronously by blocking on an async call.
        /// HighlightSwift's highlight() is async because the JS engine may need
        /// initialization. We use a semaphore to bridge to sync since this is called
        /// from a SwiftUI view body builder.
        private static func highlightSync(_ code: String, language: String?) -> AttributedString? {
            let semaphore = DispatchSemaphore(value: 0)
            var result: AttributedString?

            Task.detached {
                do {
                    let highlighted = try await highlighter.request(code, language: language)
                    // Convert the result to an AttributedString with our dark theme colors
                    result = applyDarkTheme(highlighted.attributedString)
                } catch {
                    result = nil
                }
                semaphore.signal()
            }

            semaphore.wait()
            return result
        }

        /// Apply the Boss dark theme to HighlightSwift's output.
        /// HighlightSwift returns an AttributedString with foreground colors from
        /// its theme. We keep those colors but ensure the base style matches our app.
        private static func applyDarkTheme(_ attributed: AttributedString) -> AttributedString {
            var result = attributed
            // Set base font to match our code block style
            result.font = .system(size: 13, design: .monospaced)
            return result
        }
    }

IMPORTANT: HighlightSwift's `Highlight` class and its `request()` method may have a
slightly different API depending on the version. After adding the package, check the
actual public API by looking at the resolved package source. The key points:

- Create a `Highlight()` instance (reuse it — it loads a JS engine)
- Call its highlight/request method with the code string and optional language
- It returns a result containing an `AttributedString`
- The result uses highlight.js themes — pick a dark theme like "atom-one-dark" or
  "github-dark" if the API supports theme selection

If `Highlight.request()` does not exist, look for `Highlight.highlight()` or similar.
Adapt the call to match the actual API. The pattern is:
  1. Pass code + language to the highlighter
  2. Get back an AttributedString
  3. Render it in a Text view

## Step 3: Handle the async-to-sync bridge properly

The semaphore approach above can deadlock if called from the main actor. A safer
alternative is to make code block highlighting async and cache the result:

Replace the `codeBlockView` function (lines ~601-639) with one that highlights
asynchronously and caches:

    private func codeBlockView(language: String?, code: String) -> some View {
        CodeBlockContainer(language: language, code: code)
    }

Then add a new private struct outside MarkdownNodeView:

    private struct CodeBlockContainer: View {
        let language: String?
        let code: String

        @State private var highlightedText: Text?

        var body: some View {
            VStack(alignment: .leading, spacing: 0) {
                // Title bar with traffic light dots
                HStack(spacing: 6) {
                    Circle().fill(Color.red.opacity(0.8)).frame(width: 8, height: 8)
                    Circle().fill(Color.yellow.opacity(0.8)).frame(width: 8, height: 8)
                    Circle().fill(Color.green.opacity(0.8)).frame(width: 8, height: 8)
                    Spacer()

                    if let language, !language.isEmpty {
                        Text(language.uppercased())
                            .font(.system(size: 10, weight: .bold, design: .monospaced))
                            .foregroundColor(MDTypo.tertiaryText)
                            .tracking(0.4)
                    }
                }
                .padding(.horizontal, 12)
                .padding(.top, 10)
                .padding(.bottom, 8)
                .background(Color.white.opacity(0.025))

                ScrollView(.horizontal, showsIndicators: false) {
                    (highlightedText ?? fallbackText)
                        .font(.system(size: 13, design: .monospaced))
                        .lineSpacing(4)
                        .textSelection(.enabled)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 12)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 12)
                    .fill(Color.white.opacity(0.035))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(Color.white.opacity(0.05), lineWidth: 1)
            )
            .task(id: code) {
                await highlight()
            }
        }

        private var fallbackText: Text {
            Text(code)
                .foregroundColor(Color.white.opacity(0.82))
        }

        private func highlight() async {
            do {
                let result = try await SyntaxHighlighter.highlighter.request(code, language: language)
                highlightedText = Text(result.attributedString)
            } catch {
                highlightedText = nil  // stays on fallback
            }
        }
    }

And update SyntaxHighlighter to just expose the shared instance:

    enum SyntaxHighlighter {
        static let highlighter = Highlight()
    }

This approach:
- Highlights asynchronously (no deadlock risk)
- Shows plain text immediately, then swaps in highlighted text when ready
- Uses .task(id: code) so it re-highlights only when the code content changes
- During streaming, code blocks show plain text until the fence closes, then highlight once

## Step 4: Clean up

- Delete the old SyntaxHighlighter enum entirely (the keywordSets, tokenize, etc.)
- Delete TokenKind enum
- Keep everything else in MarkdownRenderer.swift unchanged (InlineSpan, InlineTextView,
  MarkdownParser, MarkdownBlocksView, MarkdownNodeView, StreamingTextView)
- The only things that change are:
  1. Package.swift (add dependency)
  2. SyntaxHighlighter enum (replace with HighlightSwift wrapper)
  3. codeBlockView function (extract to CodeBlockContainer with async highlighting)

## Step 5: Verify

    cd /Users/tj/boss/BossApp && swift build

If HighlightSwift's API doesn't match exactly, read the package source after resolution
and adapt. The key contract is: pass code string + language, get AttributedString back.

## Constraints

- Do NOT change the block parser, inline parser, InlineTextView, or StreamingMarkdownView
- Do NOT change the visual design of code blocks (traffic light dots, dark background, rounded corners)
- Do NOT add MarkdownUI or any other markdown rendering library
- Keep the import at the top of MarkdownRenderer.swift
- The fallback for unknown languages should be plain monospace white text (same as current)
- Keep text selection working (.textSelection(.enabled))
```
