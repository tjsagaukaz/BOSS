import Combine
import SwiftUI
import UserNotifications

// MARK: - Chat ViewModel

@MainActor
final class ChatViewModel: ObservableObject {
    @Published var messages: [ChatMessage] = []
    @Published var inputText: String = ""
    @Published var isLoading: Bool = false
    @Published var currentAgent: String = AgentInfo.entryAgentName
    @Published var activeToolName: String?
    @Published var pendingPermissionCount: Int = 0
    @Published var sessionId: String = UUID().uuidString
    @Published var selectedMode: WorkMode = .default
    @Published var selectedExecutionStyle: ExecutionStyle = .singlePass
    @Published var draftAttachments: [AttachmentItem] = []
    @Published var selectedSurface: AppSurface = .chat
    @Published var selectedProjectPath: String?

    // Sidebar data
    @Published var projects: [ProjectInfo] = []
    @Published var facts: [FactInfo] = []
    @Published var systemStatus: SystemStatusInfo?
    @Published var permissions: [PermissionEntry] = []
    @Published var sidebarRefreshError: String?
    @Published var diagnosticsRefreshError: String?
    @Published var promptDiagnostics: PromptDiagnosticsInfo?
    @Published var permissionsRefreshError: String?
    @Published var previewStatus: PreviewStatusInfo?
    @Published var previewRefreshError: String?
    @Published var startupIssue: String?
    @Published var savedSessions: [(id: String, title: String, updatedAt: Date)] = []

    // Sub-state modules
    var jobsState = JobsState()
    var reviewState = ReviewState()
    var workersState = WorkersState()
    var deployState = DeployState()
    var memoryState = MemoryState()

    private let api = APIClient.shared
    private var cancellables = Set<AnyCancellable>()

    // Tracks approval IDs for which we've already sent a macOS notification.
    private var sentNotificationIds: Set<String> = []

    private var tokenBuffer: String = ""
    private var flushTask: Task<Void, Never>? = nil
    private var activeStreamMessageIndex: Int? = nil

    init() {
        // Read stored preferences
        if let storedMode = UserDefaults.standard.string(forKey: "bossDefaultMode"),
           let mode = WorkMode(rawValue: storedMode) {
            selectedMode = mode
        }
        if let storedStyle = UserDefaults.standard.string(forKey: "bossDefaultExecStyle"),
           let style = ExecutionStyle(rawValue: storedStyle) {
            selectedExecutionStyle = style
        }

        let store = SessionStore.shared
        let recent = store.listSessions()
        if let latest = recent.first, let restored = store.load(sessionId: latest.id) {
            sessionId = latest.id
            messages = restored
        } else {
            messages.append(ChatMessage(role: .system, content: "Boss Assistant ready. Ask me anything."))
        }
        savedSessions = recent

        // Forward sub-state objectWillChange to this view model
        jobsState.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }.store(in: &cancellables)
        reviewState.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }.store(in: &cancellables)
        workersState.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }.store(in: &cancellables)
        deployState.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }.store(in: &cancellables)
        memoryState.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }.store(in: &cancellables)

        // Wire memory mutation callback to refresh sidebar
        memoryState.onMemoryChanged = { [weak self] in
            await self?.refreshSidebar()
        }

        Task { await bootstrapRuntimeAndRefresh() }
    }

    // MARK: - Send message

    func retryLastMessage() {
        guard !isLoading else { return }
        guard let lastUserMsg = messages.last(where: { $0.role == .user }) else { return }
        inputText = lastUserMsg.content
        draftAttachments = lastUserMsg.attachments
        send()
    }

    func send() {
        let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        let attachments = draftAttachments
        let requestText = requestMessage(text: text, attachments: attachments)
        guard !requestText.isEmpty, !isLoading else { return }

        inputText = ""
        draftAttachments = []
        selectedSurface = .chat
        messages.append(ChatMessage(role: .user, content: text, attachments: attachments))

        let assistantMsg = ChatMessage(role: .assistant, content: "", agent: AgentInfo.entryAgentName, isStreaming: true)
        messages.append(assistantMsg)

        isLoading = true
        currentAgent = AgentInfo.entryAgentName

        Task {
            guard await ensureBackendReadyForUserAction(messageId: assistantMsg.id) else {
                return
            }
            await consumeStream(
                api.streamChat(
                    message: requestText,
                    sessionId: sessionId,
                    mode: selectedMode,
                    projectPath: selectedProjectPath,
                    executionStyle: selectedExecutionStyle
                ),
                for: assistantMsg.id
            )
        }
    }

    func addAttachments(_ urls: [URL]) {
        for url in urls {
            let candidate = AttachmentItem(url: url)
            if draftAttachments.contains(where: { $0.path == candidate.path }) {
                continue
            }
            draftAttachments.append(candidate)
        }
    }

    func removeDraftAttachment(_ attachmentId: UUID) {
        draftAttachments.removeAll { $0.id == attachmentId }
    }

    func respondToPermission(
        messageId: UUID,
        request: PermissionRequest,
        decision: PermissionDecision
    ) {
        guard let messageIndex = messageIndex(for: messageId),
              let stepIndex = executionStepIndex(in: messageIndex, stepId: request.approvalId) else {
            return
        }

        messages[messageIndex].executionSteps[stepIndex].decision = decision
        messages[messageIndex].executionSteps[stepIndex].permissionRequest = nil
        messages[messageIndex].executionSteps[stepIndex].state = decision == .deny ? .failure : .running
        messages[messageIndex].isStreaming = true

        isLoading = true
        activeToolName = messages[messageIndex].executionSteps[stepIndex].title
        refreshPermissionCount()

        Task {
            guard await ensureBackendReadyForResumedAction(messageId: messageId) else {
                return
            }
            await consumeStream(
                api.streamPermissionDecision(
                    runId: request.runId,
                    approvalId: request.approvalId,
                    decision: decision
                ),
                for: messageId
            )
        }
    }

    private func flushTokenBuffer() {
        guard !tokenBuffer.isEmpty, let idx = activeStreamMessageIndex, idx < messages.count else {
            tokenBuffer = ""
            flushTask = nil
            return
        }
        messages[idx].content += tokenBuffer
        tokenBuffer = ""
        flushTask = nil
    }

    private func consumeStream(_ stream: AsyncStream<SSEEvent>, for messageId: UUID) async {
        var sawDone = false

        for await event in stream {
            if handleEvent(event, for: messageId) {
                sawDone = true
            }
        }

        flushTask?.cancel()
        flushTask = nil
        flushTokenBuffer()
        activeStreamMessageIndex = nil

        if let messageIndex = messageIndex(for: messageId) {
            messages[messageIndex].isStreaming = false
        }

        refreshPermissionCount()
        isLoading = false
        activeToolName = nil

        // Persist after each completed stream.
        let sid = sessionId
        let msgs = messages
        Task.detached(priority: .background) {
            SessionStore.shared.save(sessionId: sid, messages: msgs)
        }
        savedSessions = SessionStore.shared.listSessions()

        if sawDone {
            await refreshSidebar()
            await refreshPermissions()
        }
    }

    @discardableResult
    private func handleEvent(_ event: SSEEvent, for messageId: UUID) -> Bool {
        guard let messageIndex = messageIndex(for: messageId) else { return false }

        switch event.type {
        case "session":
            if let sid = event.stringValue("session_id") {
                sessionId = sid
            }

        case "agent":
            if let name = event.stringValue("name") {
                currentAgent = name
                messages[messageIndex].agent = name
                activeToolName = nil
            }

        case "text":
            if let content = event.stringValue("content") {
                tokenBuffer += content
                activeStreamMessageIndex = messageIndex
                if flushTask == nil {
                    flushTask = Task { [weak self] in
                        guard let self else { return }
                        try? await Task.sleep(nanoseconds: 50_000_000)
                        self.flushTokenBuffer()
                    }
                }
            }

        case "thinking":
            if let content = event.stringValue("content") {
                messages[messageIndex].thinkingContent = content
            }

        case "handoff":
            markLatestTransferStepSuccessful(in: messageIndex)
            let from = event.stringValue("from") ?? "?"
            let to = event.stringValue("to") ?? "?"
            let info = AgentInfo.forName(to)
            messages[messageIndex].executionSteps.append(
                ExecutionStep(
                    id: UUID().uuidString,
                    kind: .handoff,
                    name: "handoff",
                    title: "Handoff",
                    description: "\(from) → \(info.display)",
                    state: .success
                )
            )

        case "tool_call":
            let callId = event.stringValue("call_id") ?? UUID().uuidString
            let name = event.stringValue("name") ?? "tool"
            let title = event.stringValue("title") ?? name.replacingOccurrences(of: "_", with: " ").capitalized
            let description = event.stringValue("description") ?? title
            let arguments = event.stringValue("arguments") ?? ""
            let executionType = event.stringValue("execution_type").flatMap(ExecutionType.init(rawValue:))
            let state: ToolState = executionType == .plan
                ? .running
                : executionType?.requiresPermission == true ? .pending : .running
            let step = ExecutionStep(
                id: callId,
                kind: .tool,
                name: name,
                title: title,
                description: description,
                arguments: arguments,
                state: state,
                executionType: executionType
            )

            if let stepIndex = executionStepIndex(in: messageIndex, stepId: callId) {
                messages[messageIndex].executionSteps[stepIndex] = step
            } else {
                messages[messageIndex].executionSteps.append(step)
            }
            activeToolName = title

        case "tool_result":
            let callId = event.stringValue("call_id") ?? ""
            let output = event.stringValue("output")
            if let stepIndex = executionStepIndex(in: messageIndex, stepId: callId)
                ?? lastToolStepIndex(in: messageIndex) {
                messages[messageIndex].executionSteps[stepIndex].output = output
                messages[messageIndex].executionSteps[stepIndex].state =
                    messages[messageIndex].executionSteps[stepIndex].decision == .deny ? .failure : .success
                messages[messageIndex].executionSteps[stepIndex].permissionRequest = nil
            }
            activeToolName = nil

        case "permission_request":
            let runId = event.stringValue("run_id") ?? ""
            let approvalId = event.stringValue("approval_id") ?? UUID().uuidString
            let name = event.stringValue("tool") ?? "tool"
            let title = event.stringValue("title") ?? name.replacingOccurrences(of: "_", with: " ").capitalized
            let description = event.stringValue("description") ?? title
            let executionType = event.stringValue("execution_type").flatMap(ExecutionType.init(rawValue:)) ?? .run
            let scopeLabel = event.stringValue("scope_label") ?? "Any"
            let request = PermissionRequest(
                runId: runId,
                approvalId: approvalId,
                name: name,
                title: title,
                description: description,
                executionType: executionType,
                scopeLabel: scopeLabel
            )

            if let stepIndex = executionStepIndex(in: messageIndex, stepId: approvalId) {
                messages[messageIndex].executionSteps[stepIndex].title = title
                messages[messageIndex].executionSteps[stepIndex].description = description
                messages[messageIndex].executionSteps[stepIndex].executionType = executionType
                messages[messageIndex].executionSteps[stepIndex].permissionRequest = request
                messages[messageIndex].executionSteps[stepIndex].state = .waitingPermission
            } else {
                messages[messageIndex].executionSteps.append(
                    ExecutionStep(
                        id: approvalId,
                        kind: .tool,
                        name: name,
                        title: title,
                        description: description,
                        state: .waitingPermission,
                        executionType: executionType,
                        permissionRequest: request
                    )
                )
            }

            messages[messageIndex].isStreaming = false
            refreshPermissionCount()
            sendPermissionNotificationIfNeeded(request: request)

        case "permission_result":
            guard let approvalId = event.stringValue("approval_id"),
                  let decisionRaw = event.stringValue("decision"),
                  let decision = PermissionDecision(rawValue: decisionRaw),
                  let stepIndex = executionStepIndex(in: messageIndex, stepId: approvalId) else {
                break
            }

            messages[messageIndex].executionSteps[stepIndex].decision = decision
            messages[messageIndex].executionSteps[stepIndex].permissionRequest = nil
            messages[messageIndex].executionSteps[stepIndex].state = decision == .deny ? .failure : .running
            refreshPermissionCount()

        case "error":
            let message = event.stringValue("message") ?? "Unknown error"
            if messages[messageIndex].content.isEmpty {
                messages[messageIndex].content = message
            }
            if let stepIndex = lastToolStepIndex(in: messageIndex) {
                messages[messageIndex].executionSteps[stepIndex].state = .failure
                messages[messageIndex].executionSteps[stepIndex].output = message
            }
            messages[messageIndex].isStreaming = false

        case "done":
            messages[messageIndex].isStreaming = false
            return true

        case "loop_status":
            let loopId = event.stringValue("loop_id") ?? ""
            let status = event.stringValue("status") ?? ""
            let stopReason = event.stringValue("stop_reason")
            // attempt may arrive as Int (new parser) or String (fallback)
            let attempt = (event.data["attempt"] as? Int) ?? event.stringValue("attempt").flatMap { Int($0) }
            let task = event.stringValue("task")

            var budgetRemaining: LoopStatusInfo.LoopBudgetRemaining?
            if let br = event.data["budget_remaining"] as? [String: Any] {
                // New parser: arrives as a nested JSON object directly.
                budgetRemaining = LoopStatusInfo.LoopBudgetRemaining(
                    attempts: br["attempts"] as? Int,
                    commands: br["commands"] as? Int,
                    wallSeconds: br["wall_seconds"] as? Double
                )
            } else if let brJSON = event.stringValue("budget_remaining"),
                      let brData = brJSON.data(using: .utf8),
                      let br = try? JSONSerialization.jsonObject(with: brData) as? [String: Any] {
                // Fallback: string-encoded JSON (old parser behavior).
                budgetRemaining = LoopStatusInfo.LoopBudgetRemaining(
                    attempts: br["attempts"] as? Int,
                    commands: br["commands"] as? Int,
                    wallSeconds: br["wall_seconds"] as? Double
                )
            }

            messages[messageIndex].loopStatus = LoopStatusInfo(
                loopId: loopId,
                status: status,
                stopReason: stopReason,
                attempt: attempt,
                budgetRemaining: budgetRemaining,
                task: task
            )

        case "loop_attempt":
            // attempt_number may arrive as Int (new parser) or String (fallback)
            let attemptNum = (event.data["attempt_number"] as? Int) ?? event.stringValue("attempt_number").flatMap { Int($0) }
            if let attemptNum, let ls = messages[messageIndex].loopStatus {
                messages[messageIndex].loopStatus = LoopStatusInfo(
                    loopId: ls.loopId,
                    status: "running",
                    stopReason: nil,
                    attempt: attemptNum,
                    budgetRemaining: ls.budgetRemaining,
                    task: ls.task
                )
            }

        default:
            break
        }

        return false
    }

    private func messageIndex(for messageId: UUID) -> Int? {
        messages.firstIndex { $0.id == messageId }
    }

    private func executionStepIndex(in messageIndex: Int, stepId: String) -> Int? {
        messages[messageIndex].executionSteps.firstIndex { $0.id == stepId }
    }

    private func lastToolStepIndex(in messageIndex: Int) -> Int? {
        messages[messageIndex].executionSteps.lastIndex { $0.kind == .tool }
    }

    private func markLatestTransferStepSuccessful(in messageIndex: Int) {
        guard let stepIndex = messages[messageIndex].executionSteps.lastIndex(where: {
            $0.kind == .tool && $0.name.hasPrefix("transfer_to_") && $0.state != .success
        }) else {
            return
        }
        messages[messageIndex].executionSteps[stepIndex].state = .success
    }

    private func refreshPermissionCount() {
        pendingPermissionCount = messages
            .flatMap(\.executionSteps)
            .filter { $0.state == .waitingPermission && $0.permissionRequest != nil }
            .count
    }

    private func sendPermissionNotificationIfNeeded(request: PermissionRequest) {
        let approvalId = request.approvalId
        guard !sentNotificationIds.contains(approvalId) else { return }
        sentNotificationIds.insert(approvalId)

        // Only notify when the app is not the frontmost application.
        guard NSApplication.shared.isActive == false else { return }

        let center = UNUserNotificationCenter.current()
        center.requestAuthorization(options: [.alert, .sound]) { granted, _ in
            guard granted else { return }
            let content = UNMutableNotificationContent()
            content.title = "Boss needs approval"
            content.body = request.description
            let req = UNNotificationRequest(
                identifier: "boss.permission.\(approvalId)",
                content: content,
                trigger: nil  // deliver immediately
            )
            center.add(req)
        }
    }

    // MARK: - Sidebar refresh

    func refreshSidebar() async {
        var failures: [String] = []

        do {
            projects = try await api.fetchProjects()
        } catch {
            failures.append("Projects unavailable: \(errorMessage(error))")
        }

        do {
            facts = try await api.fetchFacts()
        } catch {
            failures.append("Memory facts unavailable: \(errorMessage(error))")
        }

        do {
            memoryState.memoryStats = try await api.fetchStats()
        } catch {
            failures.append("Memory stats unavailable: \(errorMessage(error))")
        }

        do {
            memoryState.memoryOverview = try await api.fetchMemoryOverview(
                sessionId: sessionId,
                message: currentMemoryPreviewMessage()
            )
        } catch {
            failures.append("Memory preview unavailable: \(errorMessage(error))")
        }

        if failures.isEmpty {
            sidebarRefreshError = nil
        } else {
            sidebarRefreshError = "Sidebar refresh incomplete. \(failures.joined(separator: "  "))"
        }
    }

    func refreshPermissions() async {
        do {
            permissions = try await api.fetchPermissions()
            permissionsRefreshError = nil
        } catch {
            permissionsRefreshError = "Permissions refresh failed. \(errorMessage(error))"
        }
    }

    func refreshDiagnosticsSurface() async {
        do {
            systemStatus = try await api.fetchSystemStatus()
            promptDiagnostics = try? await api.fetchPromptDiagnostics(mode: selectedMode.rawValue)
            await deployState.refreshStatus()
            diagnosticsRefreshError = nil
        } catch {
            diagnosticsRefreshError = "Diagnostics refresh failed. \(errorMessage(error))"
        }
    }

    func showChat() {
        selectedSurface = .chat
    }

    func showMemory(projectPath: String? = nil) {
        selectedProjectPath = projectPath
        selectedSurface = .memory
        Task { await memoryState.refreshOverview(sessionId: sessionId, message: currentMemoryPreviewMessage()) }
    }

    func showDiagnostics() {
        selectedSurface = .diagnostics
        Task { await refreshDiagnosticsSurface() }
    }

    func showJobs() {
        selectedSurface = .jobs
        Task { await jobsState.refresh() }
    }

    func showPermissions() {
        selectedSurface = .permissions
        Task { await refreshPermissions() }
    }

    func showReview(projectPath: String? = nil) {
        if let projectPath {
            reviewState.selectedReviewProjectPath = projectPath
        } else if reviewState.selectedReviewProjectPath == nil {
            reviewState.selectedReviewProjectPath = selectedProjectPath
        }
        selectedSurface = .review
        Task { await reviewState.refresh(fallbackProjectPath: selectedProjectPath) }
    }

    func showPreview() {
        selectedSurface = .preview
        Task { await refreshPreviewSurface() }
    }

    func showWorkers() {
        selectedSurface = .workers
        Task { await workersState.refresh() }
    }

    func showDeploy() {
        selectedSurface = .deploy
        Task { await deployState.refresh() }
    }

    func refreshPreviewSurface() async {
        do {
            previewStatus = try await api.fetchPreviewStatus()
            previewRefreshError = nil
        } catch {
            previewRefreshError = "Preview refresh failed. \(errorMessage(error))"
        }
    }

    func launchBackgroundJob() {
        let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        let attachments = draftAttachments
        let requestText = requestMessage(text: text, attachments: attachments)
        guard !requestText.isEmpty, !jobsState.isLaunchingBackgroundJob else { return }

        jobsState.isLaunchingBackgroundJob = true
        jobsState.jobsRefreshError = nil

        let jobSessionId = sessionId
        let mode = selectedMode
        let projectPath = selectedProjectPath
        let execStyle = selectedExecutionStyle

        Task {
            guard await ensureBackendReadyForBackgroundAction() else {
                jobsState.isLaunchingBackgroundJob = false
                return
            }

            do {
                let job = try await api.launchBackgroundJob(
                    message: requestText,
                    sessionId: jobSessionId,
                    mode: mode,
                    projectPath: projectPath,
                    executionStyle: execStyle
                )
                inputText = ""
                draftAttachments = []
                selectedSurface = .jobs
                jobsState.replaceJob(job)
                jobsState.selectedJob = job
                jobsState.selectedJobLog = try? await api.fetchJobLog(jobId: job.jobId, limit: 240)
                jobsState.jobsRefreshError = nil
                await jobsState.refresh()
            } catch {
                jobsState.jobsRefreshError = "Couldn't launch background job. \(errorMessage(error))"
            }
            jobsState.isLaunchingBackgroundJob = false
        }
    }

    func takeOverJob(_ job: BackgroundJobInfo) {
        Task {
            do {
                let takeover = try await api.takeOverJob(jobId: job.jobId)
                applyJobTakeover(takeover)
                jobsState.jobsRefreshError = nil
                await jobsState.refresh()
            } catch {
                jobsState.jobsRefreshError = "Couldn't take over background job. \(errorMessage(error))"
            }
        }
    }

    func revokePermission(_ entry: PermissionEntry) {
        let previousPermissions = permissions
        permissions.removeAll { $0.id == entry.id }

        Task {
            do {
                try await api.revokePermission(tool: entry.tool, scopeKey: entry.scopeKey)
                await refreshPermissions()
            } catch {
                permissions = previousPermissions
                permissionsRefreshError = "Couldn't revoke permission. \(errorMessage(error))"
            }
        }
    }

    func newSession() {
        messages = [ChatMessage(role: .system, content: "New session started.")]
        sessionId = UUID().uuidString
        pendingPermissionCount = 0
        activeToolName = nil
        isLoading = false
        currentAgent = AgentInfo.entryAgentName
        draftAttachments = []
        selectedProjectPath = nil
        reviewState.selectedReviewProjectPath = nil
        memoryState.reset()
        reviewState.reset()
        sidebarRefreshError = nil
        diagnosticsRefreshError = nil
        permissionsRefreshError = nil
        selectedSurface = .chat
        Task { await refreshSidebar() }
    }

    func loadSession(_ id: String) {
        guard let restored = SessionStore.shared.load(sessionId: id) else { return }
        messages = restored
        sessionId = id
        pendingPermissionCount = 0
        activeToolName = nil
        isLoading = false
        currentAgent = AgentInfo.entryAgentName
        draftAttachments = []
        selectedSurface = .chat
    }

    func deleteSession(_ id: String) {
        SessionStore.shared.delete(sessionId: id)
        savedSessions = SessionStore.shared.listSessions()
        // If we deleted the current session, start a new one.
        if id == sessionId {
            newSession()
        }
    }

    // MARK: - Message Actions

    func deleteMessage(_ messageId: UUID) {
        messages.removeAll { $0.id == messageId }
        SessionStore.shared.save(sessionId: sessionId, messages: messages)
        savedSessions = SessionStore.shared.listSessions()
    }

    func editLastUserMessage() {
        guard !isLoading else { return }
        let realMessages = messages.filter { $0.role != .system }
        guard let lastUser = realMessages.last(where: { $0.role == .user }) else { return }
        // Only allow editing the very last user message
        guard let lastUserIndex = realMessages.lastIndex(where: { $0.role == .user }),
              lastUserIndex == realMessages.count - 1 || lastUserIndex == realMessages.count - 2 else {
            return
        }

        inputText = lastUser.content
        draftAttachments = lastUser.attachments

        // Remove the last user message and any following assistant message
        if let userIdx = messages.firstIndex(where: { $0.id == lastUser.id }) {
            let followingAssistantIdx = messages.index(after: userIdx)
            if followingAssistantIdx < messages.count && messages[followingAssistantIdx].role == .assistant {
                messages.remove(at: followingAssistantIdx)
            }
            messages.remove(at: userIdx)
        }

        SessionStore.shared.save(sessionId: sessionId, messages: messages)
        savedSessions = SessionStore.shared.listSessions()
    }

    // MARK: - Conversation Export

    func exportConversation(asMarkdown: Bool) {
        let realMessages = messages.filter { $0.role == .user || $0.role == .assistant }
            .filter { !$0.content.isEmpty }
        guard !realMessages.isEmpty else { return }

        let text: String
        let ext: String
        if asMarkdown {
            ext = "md"
            text = realMessages.map { msg in
                switch msg.role {
                case .user:
                    return "## User\n\n\(msg.content)\n\n---\n"
                case .assistant:
                    let agentLabel = msg.agent ?? "Boss"
                    return "## Assistant (\(agentLabel))\n\n\(msg.content)\n\n---\n"
                default:
                    return ""
                }
            }.joined(separator: "\n")
        } else {
            ext = "txt"
            text = realMessages.map { msg in
                switch msg.role {
                case .user:
                    return "User: \(msg.content)\n"
                case .assistant:
                    return "Assistant: \(msg.content)\n"
                default:
                    return ""
                }
            }.joined(separator: "\n")
        }

        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd-HHmm"
        let dateStr = formatter.string(from: Date())
        let defaultName = "boss-chat-\(dateStr).\(ext)"

        let panel = NSSavePanel()
        panel.nameFieldStringValue = defaultName
        panel.allowedContentTypes = asMarkdown
            ? [.init(filenameExtension: "md") ?? .plainText]
            : [.plainText]

        if panel.runModal() == .OK, let url = panel.url {
            try? text.write(to: url, atomically: true, encoding: .utf8)
        }
    }

    func selectMode(_ mode: WorkMode) {
        selectedMode = mode
    }

    private func requestMessage(text: String, attachments: [AttachmentItem]) -> String {
        let trimmedText = text.trimmingCharacters(in: .whitespacesAndNewlines)
        let attachmentContext = serializedAttachmentContext(attachments)

        if trimmedText.isEmpty {
            return attachmentContext
        }
        if attachmentContext.isEmpty {
            return trimmedText
        }
        return "\(trimmedText)\n\n\(attachmentContext)"
    }

    private func serializedAttachmentContext(_ attachments: [AttachmentItem]) -> String {
        guard !attachments.isEmpty else {
            return ""
        }

        var lines = ["Attached local files:"]
        var previewBudget = 24_000

        for attachment in attachments {
            lines.append("- \(attachment.displayName): \(attachment.path)")

            guard attachment.isPreviewableText,
                  previewBudget > 0,
                  let preview = textPreview(for: attachment, maxCharacters: min(8_000, previewBudget)) else {
                continue
            }

            lines.append("Contents of \(attachment.displayName):")
            lines.append("```text")
            lines.append(preview)
            lines.append("```")
            previewBudget -= preview.count
        }

        return lines.joined(separator: "\n")
    }

    private func textPreview(for attachment: AttachmentItem, maxCharacters: Int) -> String? {
        guard maxCharacters > 0 else {
            return nil
        }

        guard let values = try? attachment.url.resourceValues(forKeys: [.fileSizeKey]),
              let fileSize = values.fileSize,
              fileSize > 0,
              fileSize <= 16_384 else {
            return nil
        }

        guard let data = try? Data(contentsOf: attachment.url, options: .mappedIfSafe),
              !data.contains(0),
              let text = String(data: data, encoding: .utf8) ?? String(data: data, encoding: .ascii) else {
            return nil
        }

        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            return nil
        }

        if trimmed.count <= maxCharacters {
            return trimmed
        }
        return String(trimmed.prefix(maxCharacters)).trimmingCharacters(in: .whitespacesAndNewlines) + "\n...[truncated]"
    }

    func scanSystem(showChat: Bool = false) {
        if showChat {
            selectedSurface = .chat
        }
        Task {
            do {
                let result = try await api.triggerScan()
                let found = result["projects_found"] as? Int ?? 0
                let updated = result["projects_updated"] as? Int ?? 0
                let filesIndexed = result["files_indexed"] as? Int ?? 0
                let summariesRefreshed = result["summaries_refreshed"] as? Int ?? 0
                messages.append(
                    ChatMessage(
                        role: .system,
                        content: "System scan complete. Found \(found) projects, updated \(updated), indexed \(filesIndexed) files, refreshed \(summariesRefreshed) summaries."
                    )
                )
                await refreshSidebar()
            } catch {
                messages.append(ChatMessage(role: .error, content: "Scan failed: \(error.localizedDescription)"))
            }
        }
    }

    private func currentMemoryPreviewMessage() -> String? {
        memoryPreviewMessage(nil)
    }

    private func bootstrapRuntimeAndRefresh() async {
        let bootstrap = await LocalBackendBootstrapper.shared.ensureBackendReady(api: api)
        switch bootstrap {
        case .ready, .started:
            startupIssue = nil
        case .warning(let message), .failure(let message):
            startupIssue = message
        }

        await refreshSidebar()
        await refreshDiagnosticsSurface()
        await refreshPermissions()
    }

    private func ensureBackendReadyForBackgroundAction() async -> Bool {
        let bootstrap = await LocalBackendBootstrapper.shared.ensureBackendReady(api: api)
        switch bootstrap {
        case .ready, .started:
            startupIssue = nil
            return true
        case .warning(let message):
            startupIssue = message
            return true
        case .failure(let message):
            startupIssue = message
            return false
        }
    }

    private func ensureBackendReadyForUserAction(messageId: UUID) async -> Bool {
        let bootstrap = await LocalBackendBootstrapper.shared.ensureBackendReady(api: api)
        switch bootstrap {
        case .ready, .started:
            startupIssue = nil
            return true
        case .warning(let message):
            startupIssue = message
            return true
        case .failure(let message):
            startupIssue = message
            if let index = messageIndex(for: messageId) {
                messages.remove(at: index)
            }
            messages.append(ChatMessage(role: .error, content: message))
            isLoading = false
            activeToolName = nil
            return false
        }
    }

    private func ensureBackendReadyForResumedAction(messageId: UUID) async -> Bool {
        let bootstrap = await LocalBackendBootstrapper.shared.ensureBackendReady(api: api)
        switch bootstrap {
        case .ready, .started:
            startupIssue = nil
            return true
        case .warning(let message):
            startupIssue = message
            return true
        case .failure(let message):
            startupIssue = message
            if let index = messageIndex(for: messageId) {
                messages[index].isStreaming = false
            }
            messages.append(ChatMessage(role: .error, content: message))
            isLoading = false
            activeToolName = nil
            return false
        }
    }

    private func applyJobTakeover(_ takeover: BackgroundJobTakeoverInfo) {
        sessionId = takeover.sessionId
        selectedMode = WorkMode(rawValue: takeover.mode) ?? .default
        selectedProjectPath = takeover.projectPath
        messages = takeover.messages.map(chatMessage(from:))
        if messages.isEmpty {
            messages = [ChatMessage(role: .system, content: "Background job ready in foreground chat.")]
        }
        selectedSurface = .chat
        isLoading = false
        activeToolName = nil
        pendingPermissionCount = 0
        currentAgent = AgentInfo.entryAgentName
    }

    private func chatMessage(from info: SessionMessageInfo) -> ChatMessage {
        let role: ChatMessage.Role
        switch info.role.lowercased() {
        case "user":
            role = .user
        case "assistant":
            role = .assistant
        case "error":
            role = .error
        default:
            role = .system
        }
        return ChatMessage(role: role, content: info.content)
    }

    private func errorMessage(_ error: Error) -> String {
        if let apiError = error as? APIError {
            return apiError.userMessage
        }
        return error.localizedDescription
    }

    private func memoryPreviewMessage(_ messageOverride: String?) -> String? {
        if let override = messageOverride?.trimmingCharacters(in: .whitespacesAndNewlines), !override.isEmpty {
            return override
        }

        let draft = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        if !draft.isEmpty {
            return draft
        }

        return messages.last(where: { $0.role == .user })?.content
    }
}
