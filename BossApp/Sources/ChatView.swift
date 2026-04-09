import AppKit
import SwiftUI

// MARK: - Typography Tokens

private enum Typo {
    static let primaryText   = Color.white.opacity(0.92)
    static let secondaryText = Color.white.opacity(0.55)
    static let tertiaryText  = Color.white.opacity(0.35)

    static let bodySize: CGFloat   = 15
    static let lineGap: CGFloat    = 7
    static let tracking: CGFloat   = -0.15
    static let paragraphGap: CGFloat = 12
}

// MARK: - Chat View

struct ChatView: View {
    @EnvironmentObject var vm: ChatViewModel
    @AppStorage("bossAutoScroll") private var autoScrollEnabled: Bool = true
    @State private var isNearBottom: Bool = true
    @State private var scrollViewHeight: CGFloat = 0

    private var hasRealMessages: Bool {
        vm.messages.contains { $0.role == .user || $0.role == .assistant }
    }

    var body: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView(.vertical, showsIndicators: false) {
                    VStack(spacing: 0) {
                        // Persistent header anchor
                        if hasRealMessages {
                            HStack {
                                Text("Boss")
                                    .font(.system(size: 12, weight: .medium))
                                    .foregroundColor(Typo.tertiaryText)
                                    .tracking(0.3)

                                Spacer()

                                Menu {
                                    Button("Export as Markdown") { vm.exportConversation(asMarkdown: true) }
                                    Button("Export as Text") { vm.exportConversation(asMarkdown: false) }
                                } label: {
                                    Image(systemName: "square.and.arrow.up")
                                        .font(.system(size: 11, weight: .medium))
                                        .foregroundColor(Typo.tertiaryText)
                                }
                                .menuStyle(.borderlessButton)
                                .buttonStyle(.plain)
                                .frame(width: 20)
                                .accessibilityLabel("Export conversation")
                                .help("Export conversation")
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.leading, 4)
                            .padding(.top, 8)
                            .padding(.bottom, 4)
                        } else {
                            VStack(spacing: 8) {
                                Text("Boss")
                                    .font(.system(size: 22, weight: .semibold))
                                    .foregroundColor(Typo.primaryText)
                                    .tracking(-0.3)
                                Text("Ready. Ask anything.")
                                    .font(.system(size: 14))
                                    .foregroundColor(Typo.tertiaryText)
                            }
                            .frame(maxWidth: .infinity)
                            .padding(.top, 120)
                            .padding(.bottom, 60)
                        }

                        // Message flow
                        VStack(alignment: .leading, spacing: 0) {
                            let realMessages = vm.messages.filter { $0.role != .system }
                            let lastUserMsgId = realMessages.last(where: { $0.role == .user })?.id
                            ForEach(Array(realMessages.enumerated()), id: \.element.id) { index, message in
                                MessageView(
                                    message: message,
                                    previousRole: index > 0 ? realMessages[index - 1].role : nil,
                                    isLastUserMessage: message.id == lastUserMsgId
                                )
                                .id(message.id)
                            }
                        }
                    }
                    .frame(maxWidth: 680, alignment: .leading)
                    .frame(maxWidth: .infinity, alignment: .center)
                    .padding(.top, hasRealMessages ? 80 : 0)
                    .padding(.bottom, 32)

                    // Bottom anchor for scroll position tracking
                    Color.clear.frame(height: 1)
                        .background(GeometryReader { geo in
                            Color.clear.preference(
                                key: ScrollBottomOffsetKey.self,
                                value: geo.frame(in: .named("chatScroll")).minY
                            )
                        })
                }
                .coordinateSpace(name: "chatScroll")
                .overlay(GeometryReader { geo in
                    Color.clear
                        .onAppear { scrollViewHeight = geo.size.height }
                        .onChange(of: geo.size.height) { _, h in scrollViewHeight = h }
                })
                .onPreferenceChange(ScrollBottomOffsetKey.self) { bottomY in
                    isNearBottom = bottomY <= scrollViewHeight + 150
                }
                .onChange(of: vm.messages.count) { _, _ in
                    if let last = vm.messages.last, isNearBottom, autoScrollEnabled {
                        withAnimation(.easeOut(duration: 0.14)) {
                            proxy.scrollTo(last.id, anchor: .bottom)
                        }
                    }
                }
                .onChange(of: vm.messages.last?.content) { _, _ in
                    if let last = vm.messages.last, last.isStreaming, isNearBottom, autoScrollEnabled {
                        proxy.scrollTo(last.id, anchor: .bottom)
                    }
                }
            }

            // System state strip
            SystemStateBarView()
                .frame(maxWidth: 680)
                .frame(maxWidth: .infinity, alignment: .center)

            // Input
            InputBarView()
                .frame(maxWidth: 680)
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.bottom, 32)
        }
        .background(
            ZStack {
                BossColor.black
                RadialGradient(
                    colors: [Color.white.opacity(0.015), .clear],
                    center: .center,
                    startRadius: 0,
                    endRadius: 600
                )
            }
            .ignoresSafeArea()
        )
    }

}

// MARK: - System State Bar (extracted for SwiftUI diffing performance)

private struct SystemStateBarView: View {
    @EnvironmentObject var vm: ChatViewModel

    var body: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(
                    vm.pendingPermissionCount > 0
                        ? Color.white.opacity(0.5)
                        : vm.isLoading ? BossColor.accent.opacity(0.8) : Color.white.opacity(0.15)
                )
                .frame(width: 5, height: 5)
                .accessibilityHidden(true)

            if vm.pendingPermissionCount > 0 {
                Text("Awaiting approval")
                    .font(.system(size: 11))
                    .foregroundColor(Typo.secondaryText)
            } else if vm.isLoading {
                if let tool = vm.activeToolName {
                    Text(tool)
                        .font(.system(size: 11))
                        .foregroundColor(Typo.secondaryText)
                } else {
                    Text(vm.currentAgent == AgentInfo.entryAgentName ? "Boss is thinking…" : "\(AgentInfo.forName(vm.currentAgent).display) is thinking…")
                        .font(.system(size: 11))
                        .foregroundColor(Typo.secondaryText)
                }
            } else {
                Text("Idle")
                    .font(.system(size: 11))
                    .foregroundColor(Typo.tertiaryText)
            }

            Spacer()
        }
        .frame(height: 22)
        .padding(.horizontal, 24)
        .padding(.bottom, 6)
        .animation(.easeOut(duration: 0.14), value: vm.isLoading)
    }
}

// MARK: - Input Bar (extracted for SwiftUI diffing performance)

private struct InputBarView: View {
    @EnvironmentObject var vm: ChatViewModel
    @FocusState private var inputFocused: Bool

    private var hasText: Bool {
        !vm.inputText.trimmingCharacters(in: .whitespaces).isEmpty
    }

    private var hasDraftContent: Bool {
        hasText || !vm.draftAttachments.isEmpty
    }

    var body: some View {
        VStack(spacing: 0) {
            // Text area
            TextField("Ask Boss...", text: $vm.inputText, axis: .vertical)
                .textFieldStyle(.plain)
                .font(.system(size: 16))
                .tracking(Typo.tracking)
                .foregroundColor(Typo.primaryText)
                .lineLimit(1...8)
                .focused($inputFocused)
                .onKeyPress(.return) {
                    if NSEvent.modifierFlags.contains(.shift) {
                        return .ignored
                    }
                    vm.send()
                    return .handled
                }
                .padding(.horizontal, 20)
                .padding(.top, 18)
                .padding(.bottom, 12)

            if !vm.draftAttachments.isEmpty {
                attachmentTray
                    .padding(.horizontal, 16)
                    .padding(.bottom, 12)
            }

            // Bottom toolbar row
            HStack(spacing: 12) {
                Button(action: pickAttachments) {
                    Image(systemName: "plus")
                        .font(.system(size: 15, weight: .medium))
                        .symbolRenderingMode(.monochrome)
                        .foregroundColor(Typo.secondaryText)
                        .frame(width: 28, height: 28)
                        .background(
                            Circle()
                                .fill(Color.white.opacity(0.04))
                        )
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Attach file")
                .help("Attach file")

                Spacer(minLength: 12)

                Menu {
                    ForEach(WorkMode.allCases, id: \.rawValue) { mode in
                        Button {
                            vm.selectMode(mode)
                        } label: {
                            HStack {
                                Text(mode.label)
                                Spacer()
                                if vm.selectedMode == mode {
                                    Image(systemName: "checkmark")
                                }
                            }
                        }
                    }
                } label: {
                    HStack(spacing: 6) {
                        Text(vm.selectedMode.label)
                            .font(.system(size: 12, weight: .medium))
                        Text(vm.selectedMode.detail)
                            .font(.system(size: 11))
                            .foregroundColor(Typo.tertiaryText)
                    }
                    .foregroundColor(Typo.secondaryText)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(
                        Capsule()
                            .fill(Color.white.opacity(0.05))
                    )
                }
                .menuStyle(.borderlessButton)
                .buttonStyle(.plain)

                Menu {
                    ForEach(ExecutionStyle.allCases, id: \.rawValue) { style in
                        Button {
                            vm.selectedExecutionStyle = style
                        } label: {
                            HStack {
                                Text(style.label)
                                Spacer()
                                if vm.selectedExecutionStyle == style {
                                    Image(systemName: "checkmark")
                                }
                            }
                        }
                    }
                } label: {
                    HStack(spacing: 4) {
                        Image(systemName: vm.selectedExecutionStyle == .iterative ? "repeat" : "play")
                            .font(.system(size: 10))
                        Text(vm.selectedExecutionStyle.label)
                            .font(.system(size: 11, weight: .medium))
                    }
                    .foregroundColor(Typo.secondaryText)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 5)
                    .background(
                        Capsule()
                            .fill(Color.white.opacity(0.05))
                    )
                }
                .menuStyle(.borderlessButton)
                .buttonStyle(.plain)

                Spacer(minLength: 12)

                Button(action: { vm.launchBackgroundJob() }) {
                    Image(systemName: vm.jobsState.isLaunchingBackgroundJob ? "hourglass" : "clock.badge.plus")
                        .font(.system(size: 12, weight: .bold))
                        .symbolRenderingMode(.monochrome)
                        .foregroundColor(hasDraftContent && !vm.jobsState.isLaunchingBackgroundJob ? .white : Typo.tertiaryText)
                        .frame(width: 30, height: 30)
                        .background(
                            Circle()
                                .fill(hasDraftContent && !vm.jobsState.isLaunchingBackgroundJob
                                    ? Color.white.opacity(0.12)
                                    : Color.white.opacity(0.06))
                        )
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Launch background job")
                .help("Launch this prompt as a local background job")
                .disabled(!hasDraftContent || vm.jobsState.isLaunchingBackgroundJob)

                Button(action: { vm.send() }) {
                    Image(systemName: vm.isLoading ? "stop.fill" : "arrow.up")
                        .font(.system(size: 14, weight: .semibold))
                        .symbolRenderingMode(.monochrome)
                        .foregroundColor(hasDraftContent || vm.isLoading ? .black : Typo.tertiaryText)
                        .frame(width: 32, height: 32)
                        .background(
                            Circle()
                                .fill(hasDraftContent || vm.isLoading
                                    ? BossColor.accent
                                    : Color.white.opacity(0.06))
                                .shadow(
                                    color: (hasDraftContent || vm.isLoading) ? BossColor.accent.opacity(0.6) : .clear,
                                    radius: 6, x: 0, y: 2
                                )
                        )
                }
                .buttonStyle(.plain)
                .accessibilityLabel(vm.isLoading ? "Stop generation" : "Send message")
                .help(vm.isLoading ? "Stop generation" : "Send message")
                .keyboardShortcut(.return, modifiers: .command)
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 14)
        }
        .background(
            RoundedRectangle(cornerRadius: 26, style: .continuous)
                .fill(.ultraThinMaterial)
                .overlay(
                    RoundedRectangle(cornerRadius: 26, style: .continuous)
                        .stroke(Color.white.opacity(0.1), lineWidth: 1)
                )
        )
        .padding(.horizontal, 20)
    }

    private var attachmentTray: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(vm.draftAttachments) { attachment in
                    AttachmentChipView(attachment: attachment, removable: true) {
                        vm.removeDraftAttachment(attachment.id)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func pickAttachments() {
        let panel = NSOpenPanel()
        panel.title = "Add Images or Files"
        panel.message = "Choose local files to attach to your message."
        panel.prompt = "Add"
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = true
        panel.resolvesAliases = true

        if panel.runModal() == .OK {
            vm.addAttachments(panel.urls)
        }
    }
}

// MARK: - Message View (Editorial)

struct MessageView: View {
    @EnvironmentObject var vm: ChatViewModel
    @AppStorage("bossFontSize") private var fontSize: Double = 15
    @AppStorage("bossShowThinking") private var showThinkingDefault: Bool = false
    let message: ChatMessage
    let previousRole: ChatMessage.Role?
    var isLastUserMessage: Bool = false
    @State private var showThinking = false
    @State private var appeared = false

    // Spacing: tight pairing for user→assistant, standard otherwise
    private var topSpacing: CGFloat {
        guard let prev = previousRole else { return 0 }
        if prev == .user && message.role == .assistant { return 20 }
        if prev == .assistant && message.role == .assistant { return 12 }
        return 28
    }

    var body: some View {
        VStack(spacing: 0) {
            switch message.role {
            case .user:
                userMessage
            case .assistant:
                assistantMessage
            case .error:
                errorMessage
            case .system:
                EmptyView()
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.top, topSpacing)
        .opacity(appeared ? 1 : 0)
        .onAppear {
            withAnimation(.easeOut(duration: 0.12)) { appeared = true }
            showThinking = showThinkingDefault
        }
    }

    // MARK: - User Message (right-aligned, high-contrast)

    private var userMessage: some View {
        HStack {
            Spacer(minLength: 60)

            VStack(alignment: .leading, spacing: 10) {
                if !message.attachments.isEmpty {
                    attachmentWrap(message.attachments, removable: false)
                }

                if !message.content.isEmpty {
                    Text(message.content)
                        .font(.system(size: CGFloat(fontSize), weight: .medium))
                        .tracking(Typo.tracking)
                        .lineSpacing(Typo.lineGap)
                        .foregroundColor(Color.black.opacity(0.9))
                        .textSelection(.enabled)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .background(
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .fill(Color.white.opacity(0.95))
                    .shadow(color: Color.white.opacity(0.15), radius: 12, x: 0, y: 4)
            )
        }
        .frame(maxWidth: 640, alignment: .trailing)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("You: \(message.content)")
        .contextMenu { messageContextMenu }
        .onTapGesture(count: 2) {
            if isLastUserMessage && !vm.isLoading {
                vm.editLastUserMessage()
            }
        }
    }

    private func attachmentWrap(_ attachments: [AttachmentItem], removable: Bool) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(attachments) { attachment in
                AttachmentChipView(attachment: attachment, removable: removable)
            }
        }
    }

    // MARK: - Assistant Message (editorial text block)

    private var assistantMessage: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Agent label — quiet, uppercase
            if let agent = message.agent, agent != AgentInfo.entryAgentName {
                Text(agent.uppercased())
                    .font(.system(size: 10, weight: .medium))
                    .foregroundColor(Typo.tertiaryText)
                    .tracking(1.2)
                    .padding(.bottom, 6)
            }

            if !message.executionSteps.isEmpty {
                VStack(alignment: .leading, spacing: 3) {
                    ForEach(message.executionSteps) { step in
                        executionStepView(step)
                    }
                }
                .padding(.bottom, message.content.isEmpty ? 0 : 14)
            }

            // Loop progress widget
            if let loopStatus = message.loopStatus {
                LoopProgressView(status: loopStatus)
                    .padding(.bottom, 8)
            }

            // Content: always render through markdown parser (streaming and finalized)
            if !message.content.isEmpty {
                StreamingMarkdownView(content: message.content, isStreaming: message.isStreaming)
            }

            // Streaming dots
            if message.isStreaming && message.content.isEmpty {
                HStack(spacing: 5) {
                    Circle().fill(Typo.tertiaryText).frame(width: 3, height: 3)
                    Circle().fill(Typo.tertiaryText.opacity(0.6)).frame(width: 3, height: 3)
                    Circle().fill(Typo.tertiaryText.opacity(0.3)).frame(width: 3, height: 3)
                }
                .padding(.top, 4)
            }

            // Thinking
            if let thinking = message.thinkingContent, !thinking.isEmpty {
                Button(action: { showThinking.toggle() }) {
                    HStack(spacing: 4) {
                        Image(systemName: "chevron.right")
                            .font(.system(size: 8, weight: .semibold))
                            .rotationEffect(.degrees(showThinking ? 90 : 0))
                        Text("Reasoning")
                            .font(.system(size: 11))
                    }
                    .foregroundColor(Typo.tertiaryText)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(showThinking ? "Hide reasoning" : "Show reasoning")
                .padding(.top, 10)
                .animation(.easeOut(duration: 0.14), value: showThinking)

                if showThinking {
                    Text(thinking)
                        .font(.system(size: 12, design: .monospaced))
                        .foregroundColor(Typo.tertiaryText)
                        .lineSpacing(4)
                        .tracking(0)
                        .padding(.leading, 12)
                        .padding(.top, 4)
                }
            }
        }
        .frame(maxWidth: 640, alignment: .leading)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(message.agent ?? "Boss"): \(message.content)")
        .contextMenu { messageContextMenu }
    }

    // MARK: - Message Context Menu

    @ViewBuilder
    private var messageContextMenu: some View {
        Button("Copy Message") {
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(message.content, forType: .string)
        }
        Button("Copy as Markdown") {
            let prefix = message.role == .user ? "## User" : "## Assistant (\(message.agent ?? "Boss"))"
            let md = "\(prefix)\n\n\(message.content)"
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(md, forType: .string)
        }
        Divider()
        Button("Delete Message", role: .destructive) {
            vm.deleteMessage(message.id)
        }
    }

    // MARK: - Execution Narrative

    private func executionStepView(_ step: ExecutionStep) -> some View {
        HStack(spacing: 0) {
            RoundedRectangle(cornerRadius: 1)
                .fill(step.state == .failure ? Color.red.opacity(0.5) : Color.white.opacity(0.12))
                .frame(width: 3)

            VStack(alignment: .leading, spacing: 3) {
                Text(primaryLine(for: step))
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundColor(step.state == .success ? Typo.tertiaryText : Typo.secondaryText)
                    .lineLimit(2)
                    .contentTransition(.opacity)

                if let statusLine = secondaryLine(for: step) {
                    HStack(spacing: 8) {
                        Text(statusLine)
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundColor(Typo.tertiaryText)
                            .lineLimit(1)
                            .contentTransition(.opacity)

                        if step.state == .failure {
                            Button(action: { vm.retryLastMessage() }) {
                                HStack(spacing: 4) {
                                    Image(systemName: "arrow.clockwise")
                                        .font(.system(size: 10, weight: .medium))
                                    Text("Retry")
                                        .font(.system(size: 11, weight: .medium))
                                }
                                .foregroundColor(Color.white.opacity(0.6))
                                .padding(.horizontal, 8)
                                .padding(.vertical, 4)
                                .background(
                                    Capsule()
                                        .fill(Color.white.opacity(0.06))
                                )
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("Retry last message")
                        }
                    }
                }

                if let request = step.permissionRequest, step.state == .waitingPermission {
                    PulsingPermissionView {
                        PermissionPromptView(request: request) { decision in
                            vm.respondToPermission(messageId: message.id, request: request, decision: decision)
                        }
                    }
                    .padding(.top, 4)
                }
            }
            .padding(.leading, 10)
            .padding(.vertical, 6)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 2)
        .padding(.horizontal, 10)
        .background(
            RoundedRectangle(cornerRadius: 6)
                .fill(Color.white.opacity(0.03))
        )
        .animation(.easeOut(duration: 0.12), value: step.state)
    }

    private func primaryLine(for step: ExecutionStep) -> String {
        switch step.kind {
        case .handoff:
            return step.description
        case .tool:
            let base = step.description.isEmpty ? step.title : step.description
            switch step.state {
            case .pending, .waitingPermission, .running:
                return base + "…"
            case .success, .failure:
                return base
            }
        }
    }

    private func secondaryLine(for step: ExecutionStep) -> String? {
        switch step.kind {
        case .handoff:
            return nil
        case .tool:
            switch step.state {
            case .pending:
                return "→ Pending"
            case .waitingPermission:
                return "→ Waiting for approval"
            case .running:
                return "→ Running"
            case .success:
                if let output = step.output, !output.isEmpty {
                    return "→ " + shortened(output)
                }
                return "→ Complete"
            case .failure:
                if step.decision == .deny {
                    return "→ Not approved"
                }
                if let output = step.output, !output.isEmpty {
                    return "→ " + shortened(output)
                }
                return "→ Failed"
            }
        }
    }

    private func shortened(_ value: String) -> String {
        let compact = value.replacingOccurrences(of: "\n", with: " ")
        let prefix = compact.prefix(72)
        return String(prefix) + (compact.count > 72 ? "…" : "")
    }

    // MARK: - Error

    private var errorMessage: some View {
        Text(message.content)
            .font(.system(size: 13))
            .tracking(Typo.tracking)
            .foregroundColor(BossColor.accent.opacity(0.8))
            .frame(maxWidth: 640, alignment: .leading)
    }
}

private struct AttachmentChipView: View {
    let attachment: AttachmentItem
    let removable: Bool
    var onRemove: (() -> Void)? = nil

    private var fileTypeIcon: String {
        let ext = attachment.url.pathExtension.lowercased()
        if attachment.isImage { return "photo" }
        if ["md", "markdown"].contains(ext) { return "doc.richtext" }
        if attachment.isPreviewableText { return "curlybraces" }
        return "paperclip"
    }

    var body: some View {
        Button(action: {
            NSWorkspace.shared.activateFileViewerSelecting([attachment.url])
        }) {
            VStack(alignment: .leading, spacing: 0) {
                if attachment.isImage {
                    AsyncImage(url: attachment.url) { phase in
                        switch phase {
                        case .success(let image):
                            image
                                .resizable()
                                .aspectRatio(contentMode: .fit)
                                .frame(maxWidth: 200, maxHeight: 140)
                                .clipShape(RoundedRectangle(cornerRadius: 6))
                                .padding(.bottom, 6)
                        default:
                            EmptyView()
                        }
                    }
                }

                HStack(spacing: 8) {
                    Image(systemName: fileTypeIcon)
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(Color.white.opacity(0.72))

                    VStack(alignment: .leading, spacing: 2) {
                        Text(attachment.displayName)
                            .font(.system(size: 11, weight: .medium))
                            .foregroundColor(Color.white.opacity(0.82))
                            .lineLimit(1)

                        Text(attachment.path)
                            .font(.system(size: 10))
                            .foregroundColor(Color.white.opacity(0.38))
                            .lineLimit(1)
                    }

                    if removable, let onRemove {
                        Button(action: onRemove) {
                            Image(systemName: "xmark")
                                .font(.system(size: 9, weight: .bold))
                                .foregroundColor(Color.white.opacity(0.45))
                                .frame(width: 18, height: 18)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Remove attachment")
                    }
                }
            }
        }
        .buttonStyle(.plain)
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(Color.white.opacity(0.05))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(Color.white.opacity(0.05), lineWidth: 1)
        )
    }
}

// MARK: - Streaming Markdown (incremental rendering with caching)

private struct StreamingMarkdownView: View {
    let content: String
    let isStreaming: Bool

    @State private var parsedBlocks: [MarkdownNode] = []
    @State private var lastParsedLength: Int = 0
    @State private var pulsing = false
    @State private var parseTask: Task<Void, Never>?

    private var hasUnclosedCodeFence: Bool {
        var fenceCount = 0
        for line in content.components(separatedBy: "\n") {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            let backticks = trimmed.prefix(while: { $0 == "`" })
            if backticks.count >= 3 { fenceCount += 1 }
        }
        return fenceCount % 2 == 1
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            MarkdownBlocksView(blocks: parsedBlocks)

            if isStreaming && hasUnclosedCodeFence {
                RoundedRectangle(cornerRadius: 1)
                    .fill(Color.white.opacity(pulsing ? 0.3 : 0.08))
                    .frame(width: 24, height: 2)
                    .padding(.leading, 14)
                    .padding(.top, -6)
                    .animation(
                        .easeInOut(duration: 0.7).repeatForever(autoreverses: true),
                        value: pulsing
                    )
                    .onAppear { pulsing = true }
            }
        }
        .onAppear { reparse() }
        .onChange(of: content) { _, _ in
            if !isStreaming {
                reparse()
            } else {
                let delta = abs(content.count - lastParsedLength)
                if delta >= 20 || parsedBlocks.isEmpty {
                    reparse()
                }
            }
        }
        .onChange(of: isStreaming) { _, streaming in
            if !streaming { reparse() }
        }
    }

    private func reparse() {
        parseTask?.cancel()
        let text = content
        parseTask = Task.detached(priority: .userInitiated) {
            let blocks = MarkdownParser.parse(text)
            await MainActor.run {
                parsedBlocks = blocks
                lastParsedLength = text.count
            }
        }
    }
}

// MARK: - Pulsing ring wrapper for pending permission prompts

private struct PulsingPermissionView<Content: View>: View {
    let content: () -> Content
    @State private var pulsing = false

    var body: some View {
        content()
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(Color.orange.opacity(pulsing ? 0.6 : 0.15), lineWidth: 1.5)
                    .scaleEffect(pulsing ? 1.01 : 0.99)
                    .animation(
                        .easeInOut(duration: 1.1).repeatForever(autoreverses: true),
                        value: pulsing
                    )
            )
            .onAppear { pulsing = true }
    }

    init(@ViewBuilder content: @escaping () -> Content) {
        self.content = content
    }
}

// MARK: - Preference Key for Scroll Position Tracking

private struct ScrollBottomOffsetKey: PreferenceKey {
    nonisolated(unsafe) static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}
