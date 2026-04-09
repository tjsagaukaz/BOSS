import Foundation

// MARK: - Persisted types

private struct PersistedStep: Codable {
    let id: String
    let kind: String      // "tool" | "handoff"
    let name: String
    let title: String
    let description: String
    let state: String
    let output: String?
    let executionType: String?
}

private struct PersistedAttachment: Codable {
    let path: String
}

private struct PersistedMessage: Codable {
    let id: String
    let role: String       // "user" | "assistant" | "system" | "error"
    let content: String
    let agent: String?
    let thinkingContent: String?
    let attachments: [PersistedAttachment]
    let executionSteps: [PersistedStep]
    let timestamp: Date
}

private struct PersistedSession: Codable {
    let sessionId: String
    var messages: [PersistedMessage]
    let createdAt: Date
    var updatedAt: Date
}

// MARK: - SessionStore

final class SessionStore: Sendable {
    static let shared = SessionStore()

    private let dir: URL
    private let maxSessions = 50

    private init() {
        let home = FileManager.default.homeDirectoryForCurrentUser
        dir = home.appendingPathComponent(".boss/app-sessions", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    // MARK: - Public API

    func save(sessionId: String, messages: [ChatMessage]) {
        let persisted = PersistedSession(
            sessionId: sessionId,
            messages: messages.compactMap { encode(message: $0) },
            createdAt: existingCreatedAt(for: sessionId) ?? Date(),
            updatedAt: Date()
        )

        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        guard let data = try? encoder.encode(persisted) else { return }
        try? data.write(to: file(for: sessionId))
        pruneOldSessions()
    }

    func load(sessionId: String) -> [ChatMessage]? {
        guard let data = try? Data(contentsOf: file(for: sessionId)),
              let session = decoded(data) else { return nil }
        return session.messages.compactMap { decode(message: $0) }
    }

    func listSessions() -> [(id: String, title: String, updatedAt: Date)] {
        let fm = FileManager.default
        guard let files = try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil) else { return [] }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601

        return files
            .filter { $0.pathExtension == "json" }
            .compactMap { url -> (id: String, title: String, updatedAt: Date)? in
                guard let data = try? Data(contentsOf: url),
                      let session = try? decoder.decode(PersistedSession.self, from: data) else { return nil }
                let title = session.messages
                    .first { $0.role == "user" }
                    .map { String($0.content.prefix(60)) } ?? "Empty session"
                return (id: session.sessionId, title: title, updatedAt: session.updatedAt)
            }
            .sorted { $0.updatedAt > $1.updatedAt }
    }

    func delete(sessionId: String) {
        try? FileManager.default.removeItem(at: file(for: sessionId))
    }

    // MARK: - Private helpers

    private func file(for sessionId: String) -> URL {
        dir.appendingPathComponent("\(sessionId).json")
    }

    private func existingCreatedAt(for sessionId: String) -> Date? {
        guard let data = try? Data(contentsOf: file(for: sessionId)),
              let session = decoded(data) else { return nil }
        return session.createdAt
    }

    private func decoded(_ data: Data) -> PersistedSession? {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try? decoder.decode(PersistedSession.self, from: data)
    }

    private func pruneOldSessions() {
        let all = listSessions()
        if all.count > maxSessions {
            all.dropFirst(maxSessions).forEach { delete(sessionId: $0.id) }
        }
    }

    // MARK: - Encode ChatMessage → PersistedMessage

    private func encode(message: ChatMessage) -> PersistedMessage? {
        // Skip intermediate system messages; they are recreated on load.
        let roleStr: String
        switch message.role {
        case .user:       roleStr = "user"
        case .assistant:  roleStr = "assistant"
        case .system:     roleStr = "system"
        case .error:      roleStr = "error"
        }

        let steps = message.executionSteps.map { step in
            PersistedStep(
                id: step.id,
                kind: step.kind == .handoff ? "handoff" : "tool",
                name: step.name,
                title: step.title,
                description: step.description,
                state: step.state.rawValue,
                output: step.output,
                executionType: step.executionType?.rawValue
            )
        }

        let attachments = message.attachments.map { PersistedAttachment(path: $0.path) }

        return PersistedMessage(
            id: message.id.uuidString,
            role: roleStr,
            content: message.content,
            agent: message.agent,
            thinkingContent: message.thinkingContent,
            attachments: attachments,
            executionSteps: steps,
            timestamp: message.timestamp
        )
    }

    // MARK: - Decode PersistedMessage → ChatMessage

    private func decode(message pm: PersistedMessage) -> ChatMessage? {
        let role: ChatMessage.Role
        switch pm.role {
        case "user":      role = .user
        case "assistant": role = .assistant
        case "system":    role = .system
        case "error":     role = .error
        default:          return nil
        }

        let steps = pm.executionSteps.map { ps in
            ExecutionStep(
                id: ps.id,
                kind: ps.kind == "handoff" ? .handoff : .tool,
                name: ps.name,
                title: ps.title,
                description: ps.description,
                output: ps.output,
                state: ToolState(rawValue: ps.state) ?? .success,
                executionType: ps.executionType.flatMap(ExecutionType.init(rawValue:))
            )
        }

        let attachments = pm.attachments.compactMap { pa -> AttachmentItem? in
            let url = URL(fileURLWithPath: pa.path)
            return AttachmentItem(url: url)
        }

        var msg = ChatMessage(role: role, content: pm.content, agent: pm.agent)
        msg.thinkingContent = pm.thinkingContent
        msg.executionSteps = steps
        // attachments are read-only on construction; rebuild via a mutating workaround
        // (AttachmentItem is value-type, ChatMessage has var attachments)
        var mutableMsg = msg
        mutableMsg.attachments = attachments
        return mutableMsg
    }
}
