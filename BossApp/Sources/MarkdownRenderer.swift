import SwiftUI

// MARK: - AST Model

enum MarkdownBlock: Hashable {
    case heading(level: Int, text: String)
    case paragraph(text: String)
    case blockquote(blocks: [MarkdownNode])
    case list(ordered: Bool, items: [[MarkdownNode]])
    case code(language: String?, code: String)
    case divider
}

struct MarkdownNode: Identifiable, Hashable {
    let id: String
    let block: MarkdownBlock
}

// MARK: - Inline Spans

enum InlineSpan {
    case text(String)
    case bold(String)
    case italic(String)
    case code(String)
    case link(text: String, url: String)
    case boldItalic(String)
}

// MARK: - Recursive Parser

enum MarkdownParser {
    static func parse(_ text: String) -> [MarkdownNode] {
        let normalized = text
            .replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")
        return assignIDs(to: parseLines(normalized.components(separatedBy: "\n")))
    }

    private struct ListMatch {
        let markerLength: Int
        let ordered: Bool
    }

    private struct ParsedList {
        let block: MarkdownBlock
        let nextIndex: Int
    }

    private static func assignIDs(to blocks: [MarkdownBlock], prefix: String = "root") -> [MarkdownNode] {
        blocks.enumerated().map { index, block in
            let id = "\(prefix)-\(index)"

            switch block {
            case .blockquote(let blocks):
                return MarkdownNode(
                    id: id,
                    block: .blockquote(blocks: assignIDs(to: blocks.map(\.block), prefix: "\(id)-quote"))
                )

            case .list(let ordered, let items):
                let nestedItems = items.enumerated().map { itemIndex, nodes in
                    assignIDs(to: nodes.map(\.block), prefix: "\(id)-item\(itemIndex)")
                }
                return MarkdownNode(id: id, block: .list(ordered: ordered, items: nestedItems))

            default:
                return MarkdownNode(id: id, block: block)
            }
        }
    }

    private static func parseLines(_ lines: [String]) -> [MarkdownBlock] {
        var blocks: [MarkdownBlock] = []
        var index = 0

        while index < lines.count {
            let line = lines[index]
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            if trimmed.isEmpty {
                index += 1
                continue
            }

            if isDivider(trimmed) {
                blocks.append(.divider)
                index += 1
                continue
            }

            if let codeFence = parseCodeFenceStart(trimmed) {
                var codeLines: [String] = []
                index += 1

                while index < lines.count {
                    if lines[index].trimmingCharacters(in: .whitespaces).hasPrefix(codeFence.fence) {
                        index += 1
                        break
                    }
                    codeLines.append(lines[index])
                    index += 1
                }

                blocks.append(.code(language: codeFence.language, code: codeLines.joined(separator: "\n")))
                continue
            }

            if trimmed.hasPrefix(">") {
                var quoteLines: [String] = []

                while index < lines.count {
                    let quoteLine = lines[index]
                    let quoteTrimmed = quoteLine.trimmingCharacters(in: .whitespaces)

                    if quoteTrimmed.isEmpty {
                        quoteLines.append("")
                        index += 1
                        continue
                    }

                    guard quoteTrimmed.hasPrefix(">") else { break }
                    quoteLines.append(String(quoteTrimmed.dropFirst()).trimmingCharacters(in: .whitespaces))
                    index += 1
                }

                blocks.append(.blockquote(blocks: assignIDs(to: parseLines(quoteLines), prefix: "quote-\(blocks.count)")))
                continue
            }

            if let heading = parseHeading(trimmed) {
                blocks.append(.heading(level: heading.level, text: heading.text))
                index += 1
                continue
            }

            if let parsedList = parseList(lines, startIndex: index) {
                blocks.append(parsedList.block)
                index = parsedList.nextIndex
                continue
            }

            var paragraphLines: [String] = []
            while index < lines.count {
                let paragraphLine = lines[index]
                let paragraphTrimmed = paragraphLine.trimmingCharacters(in: .whitespaces)

                if paragraphTrimmed.isEmpty
                    || isDivider(paragraphTrimmed)
                    || parseCodeFenceStart(paragraphTrimmed) != nil
                    || paragraphTrimmed.hasPrefix(">")
                    || parseHeading(paragraphTrimmed) != nil
                    || parseListStart(paragraphTrimmed) != nil {
                    break
                }

                paragraphLines.append(paragraphLine.trimmingCharacters(in: .whitespaces))
                index += 1
            }

            if !paragraphLines.isEmpty {
                blocks.append(.paragraph(text: paragraphLines.joined(separator: "\n")))
            }
        }

        return blocks
    }

    private static func parseList(_ lines: [String], startIndex: Int) -> ParsedList? {
        let firstLine = lines[startIndex]
        let trimmed = firstLine.trimmingCharacters(in: .whitespaces)
        guard let firstMatch = parseListStart(trimmed) else { return nil }

        let ordered = firstMatch.ordered
        let baseIndent = leadingWhitespaceCount(in: firstLine)
        var items: [[MarkdownNode]] = []
        var currentItemLines = [String(trimmed.dropFirst(firstMatch.markerLength)).trimmingCharacters(in: .whitespaces)]
        var index = startIndex + 1

        while index < lines.count {
            let line = lines[index]
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            let indent = leadingWhitespaceCount(in: line)

            if trimmed.isEmpty {
                if let nextIndex = nextNonEmptyLine(after: index, in: lines) {
                    let nextLine = lines[nextIndex]
                    let nextTrimmed = nextLine.trimmingCharacters(in: .whitespaces)
                    let nextIndent = leadingWhitespaceCount(in: nextLine)

                    if nextIndent > baseIndent || (nextIndent == baseIndent && parseListStart(nextTrimmed)?.ordered == ordered) {
                        currentItemLines.append("")
                        index += 1
                        continue
                    }
                }
                break
            }

            if let nextMatch = parseListStart(trimmed), indent == baseIndent, nextMatch.ordered == ordered {
                items.append(assignIDs(to: parseLines(currentItemLines), prefix: "list-\(startIndex)-item\(items.count)"))
                currentItemLines = [String(trimmed.dropFirst(nextMatch.markerLength)).trimmingCharacters(in: .whitespaces)]
                index += 1
                continue
            }

            guard indent > baseIndent else { break }

            let dropCount = min(line.count, max(baseIndent + 2, 0))
            currentItemLines.append(String(line.dropFirst(dropCount)))
            index += 1
        }

        if !currentItemLines.isEmpty {
            items.append(assignIDs(to: parseLines(currentItemLines), prefix: "list-\(startIndex)-item\(items.count)"))
        }

        return ParsedList(block: .list(ordered: ordered, items: items), nextIndex: index)
    }

    private static func isDivider(_ text: String) -> Bool {
        let significant = text.filter { !$0.isWhitespace }
        guard significant.count >= 3, let marker = significant.first else { return false }
        guard marker == "-" || marker == "*" || marker == "_" else { return false }
        return significant.allSatisfy { $0 == marker }
    }

    private static func parseCodeFenceStart(_ text: String) -> (fence: String, language: String?)? {
        let fence = text.prefix(while: { $0 == "`" })
        guard fence.count >= 3 else { return nil }
        let language = String(text.dropFirst(fence.count)).trimmingCharacters(in: .whitespaces)
        return (String(fence), language.isEmpty ? nil : language)
    }

    private static func parseHeading(_ text: String) -> (level: Int, text: String)? {
        let hashes = text.prefix(while: { $0 == "#" }).count
        guard hashes > 0, hashes <= 6, text.dropFirst(hashes).hasPrefix(" ") else { return nil }
        return (hashes, String(text.dropFirst(hashes + 1)))
    }

    private static func parseListStart(_ text: String) -> ListMatch? {
        if text.hasPrefix("- ") || text.hasPrefix("* ") || text.hasPrefix("+ ") || text.hasPrefix("• ") {
            return ListMatch(markerLength: 2, ordered: false)
        }

        guard let dotIndex = text.firstIndex(of: ".") else { return nil }
        let numberPart = text[text.startIndex..<dotIndex]
        guard !numberPart.isEmpty, numberPart.allSatisfy(\.isNumber), text[dotIndex...].hasPrefix(". ") else {
            return nil
        }
        return ListMatch(markerLength: text.distance(from: text.startIndex, to: dotIndex) + 2, ordered: true)
    }

    private static func leadingWhitespaceCount(in line: String) -> Int {
        line.prefix(while: { $0 == " " || $0 == "\t" }).count
    }

    private static func nextNonEmptyLine(after index: Int, in lines: [String]) -> Int? {
        var current = index + 1
        while current < lines.count {
            if !lines[current].trimmingCharacters(in: .whitespaces).isEmpty {
                return current
            }
            current += 1
        }
        return nil
    }

    // MARK: Inline Parsing

    static func parseInline(_ text: String) -> [InlineSpan] {
        guard !text.isEmpty else { return [] }

        let pattern = [
            "`([^`]+)`",
            "\\[([^\\]]+)\\]\\(([^)]+)\\)",
            "\\*\\*\\*(.+?)\\*\\*\\*",
            "(?<!\\w)___(.+?)___(?!\\w)",
            "\\*\\*(.+?)\\*\\*",
            "(?<!\\w)__(.+?)__(?!\\w)",
            "\\*(.+?)\\*",
            "(?<!\\w)_(.+?)_(?!\\w)",
        ].joined(separator: "|")

        guard let regex = try? NSRegularExpression(pattern: pattern) else {
            return [.text(text)]
        }

        let ns = text as NSString
        let matches = regex.matches(in: text, range: NSRange(location: 0, length: ns.length))
        var spans: [InlineSpan] = []
        var cursor = 0

        for match in matches {
            let start = match.range.location
            if start > cursor {
                spans.append(.text(ns.substring(with: NSRange(location: cursor, length: start - cursor))))
            }

            if match.range(at: 1).location != NSNotFound {
                spans.append(.code(ns.substring(with: match.range(at: 1))))
            } else if match.range(at: 2).location != NSNotFound {
                spans.append(.link(
                    text: ns.substring(with: match.range(at: 2)),
                    url: ns.substring(with: match.range(at: 3))
                ))
            } else if match.range(at: 4).location != NSNotFound {
                spans.append(.boldItalic(ns.substring(with: match.range(at: 4))))
            } else if match.range(at: 5).location != NSNotFound {
                spans.append(.boldItalic(ns.substring(with: match.range(at: 5))))
            } else if match.range(at: 6).location != NSNotFound {
                spans.append(.bold(ns.substring(with: match.range(at: 6))))
            } else if match.range(at: 7).location != NSNotFound {
                spans.append(.bold(ns.substring(with: match.range(at: 7))))
            } else if match.range(at: 8).location != NSNotFound {
                spans.append(.italic(ns.substring(with: match.range(at: 8))))
            } else if match.range(at: 9).location != NSNotFound {
                spans.append(.italic(ns.substring(with: match.range(at: 9))))
            }

            cursor = match.range.location + match.range.length
        }

        if cursor < ns.length {
            spans.append(.text(ns.substring(with: NSRange(location: cursor, length: ns.length - cursor))))
        }

        return spans.isEmpty ? [.text(text)] : spans
    }
}

// MARK: - Typography

enum MDTypo {
    static let primaryText = Color.white.opacity(0.92)
    static let secondaryText = Color.white.opacity(0.55)
    static let tertiaryText = Color.white.opacity(0.35)
    static let bodySize: CGFloat = 15
    static let lineGap: CGFloat = 7
    static let tracking: CGFloat = -0.15
}

// MARK: - Syntax Highlighting

enum SyntaxHighlighter {
    enum TokenKind { case keyword, string, comment, number, plain }

    private static let keywordSets: [String: Set<String>] = [
        "swift": ["func", "let", "var", "class", "struct", "enum", "protocol", "import", "return",
                  "if", "else", "for", "while", "guard", "switch", "case", "break", "continue",
                  "self", "true", "false", "nil", "private", "public", "static", "override",
                  "init", "throws", "throw", "try", "catch", "await", "async", "some", "any",
                  "where", "in", "extension", "typealias", "defer", "do", "repeat"],
        "python": ["def", "class", "import", "from", "return", "if", "elif", "else", "for",
                   "while", "with", "as", "try", "except", "finally", "raise", "pass", "break",
                   "continue", "and", "or", "not", "in", "is", "None", "True", "False",
                   "lambda", "yield", "global", "nonlocal", "async", "await", "self"],
        "javascript": ["function", "const", "let", "var", "class", "import", "export", "default",
                       "return", "if", "else", "for", "while", "do", "switch", "case", "break",
                       "continue", "new", "this", "typeof", "instanceof", "throw", "try", "catch",
                       "finally", "async", "await", "true", "false", "null", "undefined"],
        "typescript": ["function", "const", "let", "var", "class", "interface", "type", "import",
                       "export", "default", "return", "if", "else", "for", "while", "do", "switch",
                       "case", "break", "continue", "new", "this", "typeof", "instanceof", "throw",
                       "try", "catch", "finally", "async", "await", "true", "false", "null",
                       "undefined", "enum", "implements", "abstract", "as", "keyof", "readonly"],
        "rust": ["fn", "let", "mut", "const", "static", "struct", "enum", "impl", "trait", "type",
                "use", "mod", "pub", "crate", "self", "super", "return", "if", "else", "for",
                "while", "loop", "match", "break", "continue", "where", "as", "in", "ref",
                "move", "async", "await", "unsafe", "true", "false"],
        "go": ["func", "var", "const", "type", "struct", "interface", "import", "package", "return",
              "if", "else", "for", "range", "switch", "case", "break", "continue", "select",
              "chan", "go", "defer", "map", "make", "new", "true", "false", "nil", "default"],
        "bash": ["if", "then", "else", "elif", "fi", "for", "while", "do", "done", "case", "esac",
                "function", "return", "in", "echo", "exit", "export", "source", "local", "set",
                "unset", "true", "false"],
    ]

    private static let hashCommentLangs: Set<String> = ["python", "bash", "ruby", "r"]
    private static let slashCommentLangs: Set<String> = ["swift", "javascript", "typescript", "rust", "go", "java", "c", "cpp"]

    static func highlightedText(_ code: String, language: String?) -> Text {
        let tokens = tokenize(code, language: language)
        return tokens.reduce(Text("")) { result, token in
            result + styledText(token)
        }
    }

    private static func styledText(_ token: (kind: TokenKind, text: String)) -> Text {
        let color: Color
        switch token.kind {
        case .keyword:  color = Color(red: 0.78, green: 0.46, blue: 0.93)
        case .string:   color = Color(red: 0.58, green: 0.84, blue: 0.44)
        case .comment:  color = Color.white.opacity(0.35)
        case .number:   color = Color(red: 0.85, green: 0.7, blue: 0.35)
        case .plain:    color = Color.white.opacity(0.82)
        }
        return Text(token.text).foregroundColor(color)
    }

    private static func tokenize(_ code: String, language: String?) -> [(kind: TokenKind, text: String)] {
        let lang = language?.lowercased() ?? ""
        guard let keywords = keywordSets[lang] else {
            return [(.plain, code)]
        }

        var patterns: [String] = []
        if slashCommentLangs.contains(lang) { patterns.append("//[^\n]*") }
        if hashCommentLangs.contains(lang) { patterns.append("#[^\n]*") }
        patterns.append("\"(?:[^\"\\\\]|\\\\.)*\"|'(?:[^'\\\\]|\\\\.)*'")
        patterns.append("\\b\\d+(?:\\.\\d+)?\\b")
        patterns.append("\\b[A-Za-z_][A-Za-z0-9_]*\\b")

        guard let regex = try? NSRegularExpression(pattern: patterns.joined(separator: "|")) else {
            return [(.plain, code)]
        }

        let ns = code as NSString
        let matches = regex.matches(in: code, range: NSRange(location: 0, length: ns.length))
        var tokens: [(kind: TokenKind, text: String)] = []
        var cursor = 0

        for match in matches {
            let r = match.range
            if r.location > cursor {
                tokens.append((.plain, ns.substring(with: NSRange(location: cursor, length: r.location - cursor))))
            }
            let t = ns.substring(with: r)
            let kind: TokenKind
            if t.hasPrefix("//") || t.hasPrefix("#") {
                kind = .comment
            } else if t.hasPrefix("\"") || t.hasPrefix("\'") {
                kind = .string
            } else if t.first?.isNumber == true {
                kind = .number
            } else if keywords.contains(t) {
                kind = .keyword
            } else {
                kind = .plain
            }
            tokens.append((kind: kind, text: t))
            cursor = r.location + r.length
        }

        if cursor < ns.length {
            tokens.append((.plain, ns.substring(with: NSRange(location: cursor, length: ns.length - cursor))))
        }

        return tokens
    }
}

// MARK: - Inline Text Rendering

struct InlineTextView: View {
    let spans: [InlineSpan]

    init(_ text: String) {
        self.spans = MarkdownParser.parseInline(text)
    }

    init(spans: [InlineSpan]) {
        self.spans = spans
    }

    var body: some View {
        textContent
            .font(.system(size: MDTypo.bodySize))
            .tracking(MDTypo.tracking)
            .lineSpacing(MDTypo.lineGap)
            .foregroundColor(MDTypo.primaryText)
            .textSelection(.enabled)
    }

    var textContent: Text {
        spans.reduce(Text("")) { result, span in
            result + renderSpan(span)
        }
    }

    private func renderSpan(_ span: InlineSpan) -> Text {
        switch span {
        case .text(let str):
            return Text(str)
        case .bold(let str):
            return Text(str).bold()
        case .italic(let str):
            return Text(str).italic()
        case .boldItalic(let str):
            return Text(str).bold().italic()
        case .code(let str):
            return Text(str)
                .font(.system(size: MDTypo.bodySize - 1, design: .monospaced))
                .foregroundColor(Color(red: 0.92, green: 0.58, blue: 0.46))
        case .link(let text, let url):
            var attrStr = AttributedString(text)
            attrStr.foregroundColor = .init(red: 0.4, green: 0.7, blue: 1.0)
            attrStr.underlineStyle = .single
            if let linkURL = URL(string: url) {
                attrStr.link = linkURL
            }
            return Text(attrStr)
        }
    }
}

// MARK: - Recursive Renderer

struct MarkdownBlocksView: View {
    let nodes: [MarkdownNode]

    init(blocks: [MarkdownNode]) {
        self.nodes = blocks
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(nodes) { node in
                MarkdownNodeView(node: node)
            }
        }
    }
}

private struct MarkdownNodeView: View {
    let node: MarkdownNode

    var body: some View {
        switch node.block {
        case .heading(let level, let text):
            headingView(level: level, text: text)

        case .paragraph(let text):
            paragraphView(text)

        case .blockquote(let blocks):
            blockquoteView(blocks)

        case .list(let ordered, let items):
            listView(ordered: ordered, items: items)

        case .code(let language, let code):
            codeBlockView(language: language, code: code)

        case .divider:
            Rectangle()
                .fill(Color.white.opacity(0.06))
                .frame(height: 1)
                .padding(.vertical, 8)
        }
    }

    private func headingView(level: Int, text: String) -> some View {
        let style: (CGFloat, Font.Weight) = {
            switch level {
            case 1: return (22, .semibold)
            case 2: return (19, .semibold)
            case 3: return (17, .medium)
            default: return (15, .medium)
            }
        }()

        return InlineTextView(text).textContent
            .font(.system(size: style.0, weight: style.1))
            .tracking(-0.2)
            .foregroundColor(MDTypo.primaryText)
            .padding(.top, level <= 2 ? 6 : 2)
            .textSelection(.enabled)
    }

    private func paragraphView(_ text: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(Array(text.components(separatedBy: "\n").enumerated()), id: \.offset) { _, line in
                InlineTextView(line).textContent
                    .font(.system(size: MDTypo.bodySize))
                    .tracking(MDTypo.tracking)
                    .lineSpacing(MDTypo.lineGap)
                    .foregroundColor(MDTypo.primaryText)
                    .textSelection(.enabled)
            }
        }
    }

    private func blockquoteView(_ blocks: [MarkdownNode]) -> some View {
        HStack(alignment: .top, spacing: 12) {
            RoundedRectangle(cornerRadius: 2)
                .fill(Color.white.opacity(0.12))
                .frame(width: 4)

            VStack(alignment: .leading, spacing: 10) {
                ForEach(blocks) { nested in
                    MarkdownNodeView(node: nested)
                }
            }
        }
        .padding(.leading, 2)
    }

    private func codeBlockView(language: String?, code: String) -> some View {
        VStack(alignment: .leading, spacing: 0) {
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
                SyntaxHighlighter.highlightedText(code, language: language)
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
    }

    private func listView(ordered: Bool, items: [[MarkdownNode]]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(items.enumerated()), id: \.offset) { index, itemNodes in
                HStack(alignment: .top, spacing: 8) {
                    if let checkboxState = checkboxState(in: itemNodes) {
                        Image(systemName: checkboxState ? "checkmark.square.fill" : "square")
                            .foregroundColor(checkboxState ? Color.white.opacity(0.88) : MDTypo.secondaryText)
                            .padding(.top, 2)
                            .frame(width: 18, alignment: .center)
                    } else if ordered {
                        Text("\(index + 1).")
                            .font(.system(size: MDTypo.bodySize - 1, weight: .medium, design: .monospaced))
                            .foregroundColor(MDTypo.tertiaryText)
                            .frame(width: 24, alignment: .trailing)
                    } else {
                        Text("•")
                            .font(.system(size: MDTypo.bodySize + 1))
                            .foregroundColor(MDTypo.tertiaryText)
                            .frame(width: 16, alignment: .center)
                    }

                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(cleanCheckbox(from: itemNodes)) { nested in
                            MarkdownNodeView(node: nested)
                        }
                    }
                }
            }
        }
        .padding(.leading, 4)
    }

    private func checkboxState(in nodes: [MarkdownNode]) -> Bool? {
        guard let first = nodes.first,
              case .paragraph(let text) = first.block else {
            return nil
        }

        let trimmed = text.trimmingCharacters(in: .whitespaces)
        if trimmed.hasPrefix("[ ]") { return false }
        if trimmed.hasPrefix("[x]") || trimmed.hasPrefix("[X]") { return true }
        return nil
    }

    private func cleanCheckbox(from nodes: [MarkdownNode]) -> [MarkdownNode] {
        guard let first = nodes.first,
              case .paragraph(let text) = first.block,
              checkboxState(in: nodes) != nil else {
            return nodes
        }

        let cleanedText = String(text.dropFirst(3)).trimmingCharacters(in: .whitespaces)
        var cleaned = nodes
        cleaned[0] = MarkdownNode(id: first.id, block: .paragraph(text: cleanedText))
        return cleaned
    }
}

// MARK: - Streaming Fallback

struct StreamingTextView: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.system(size: MDTypo.bodySize))
            .tracking(MDTypo.tracking)
            .lineSpacing(MDTypo.lineGap)
            .foregroundColor(MDTypo.primaryText)
            .textSelection(.enabled)
    }
}