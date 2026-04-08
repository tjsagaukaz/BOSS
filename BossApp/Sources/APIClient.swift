import Foundation

// MARK: - SSE Event Parser

struct SSEEvent: Sendable {
    var type: String = ""
    var data: [String: String] = [:]
}

enum APIError: LocalizedError {
    case invalidURL(String)
    case invalidResponse
    case transport(String)
    case http(statusCode: Int, message: String)
    case decoding(context: String, message: String)

    var userMessage: String {
        switch self {
        case .invalidURL(let path):
            return "Invalid request URL for \(path)."
        case .invalidResponse:
            return "The server returned an invalid response."
        case .transport(let message):
            return "Couldn't reach Boss. \(message)"
        case .http(let statusCode, let message):
            return "Request failed (\(statusCode)): \(message)"
        case .decoding(let context, let message):
            return "Unexpected response from \(context). \(message)"
        }
    }

    var errorDescription: String? {
        userMessage
    }
}

// MARK: - API Client

final class APIClient: Sendable {
    static let shared = APIClient()

    let baseURL: String
    private let session: URLSession

    init(baseURL: String = "http://127.0.0.1:8321") {
        self.baseURL = baseURL
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        config.timeoutIntervalForResource = 120
        config.httpMaximumConnectionsPerHost = 4
        self.session = URLSession(configuration: config)
    }

    // MARK: - Streaming Chat

    func streamChat(message: String, sessionId: String?, mode: WorkMode = .default) -> AsyncStream<SSEEvent> {
        var req = URLRequest(url: URL(string: "\(baseURL)/api/chat")!)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")

        struct Body: Encodable {
            let message: String
            let session_id: String?
            let mode: String
        }

        req.httpBody = try? JSONEncoder().encode(Body(message: message, session_id: sessionId, mode: mode.rawValue))
        return stream(request: req)
    }

    func streamPermissionDecision(
        runId: String,
        approvalId: String,
        decision: PermissionDecision
    ) -> AsyncStream<SSEEvent> {
        var req = URLRequest(url: URL(string: "\(baseURL)/api/chat/permissions")!)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")

        struct Body: Encodable {
            let run_id: String
            let approval_id: String
            let decision: String
        }

        req.httpBody = try? JSONEncoder().encode(
            Body(run_id: runId, approval_id: approvalId, decision: decision.rawValue)
        )
        return stream(request: req)
    }

    // MARK: - REST Endpoints

    func fetchProjects() async throws -> [ProjectInfo] {
        let data = try await get("/api/memory/projects")
        return try decode([ProjectInfo].self, from: data, context: "/api/memory/projects")
    }

    func fetchFacts() async throws -> [FactInfo] {
        let data = try await get("/api/memory/facts")
        return try decode([FactInfo].self, from: data, context: "/api/memory/facts")
    }

    func fetchStats() async throws -> MemoryStats {
        let data = try await get("/api/memory/stats")
        return try decode(
            MemoryStats.self,
            from: data,
            context: "/api/memory/stats",
            dateDecodingStrategy: .iso8601
        )
    }

    func fetchMemoryOverview(sessionId: String?, message: String?) async throws -> MemoryOverview {
        var items: [URLQueryItem] = []
        if let sessionId, !sessionId.isEmpty {
            items.append(URLQueryItem(name: "session_id", value: sessionId))
        }
        if let message, !message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            items.append(URLQueryItem(name: "message", value: message))
        }
        let data = try await get("/api/memory/overview", queryItems: items)
        return try decode(
            MemoryOverview.self,
            from: data,
            context: "/api/memory/overview",
            dateDecodingStrategy: .iso8601
        )
    }

    func fetchPermissions() async throws -> [PermissionEntry] {
        let data = try await get("/api/permissions")
        return try decode(
            [PermissionEntry].self,
            from: data,
            context: "/api/permissions",
            dateDecodingStrategy: .iso8601
        )
    }

    func fetchSystemStatus() async throws -> SystemStatusInfo {
        let data = try await get("/api/system/status")
        return try decode(
            SystemStatusInfo.self,
            from: data,
            context: "/api/system/status",
            dateDecodingStrategy: .iso8601
        )
    }

    func fetchReviewCapabilities(projectPath: String?) async throws -> ReviewCapabilitiesInfo {
        var items: [URLQueryItem] = []
        if let projectPath, !projectPath.isEmpty {
            items.append(URLQueryItem(name: "project_path", value: projectPath))
        }
        let data = try await get("/api/review/capabilities", queryItems: items)
        return try decode(
            ReviewCapabilitiesInfo.self,
            from: data,
            context: "/api/review/capabilities",
            dateDecodingStrategy: .iso8601
        )
    }

    func fetchReviewHistory(limit: Int = 30) async throws -> [ReviewRunInfo] {
        let data = try await get("/api/review/history", queryItems: [
            URLQueryItem(name: "limit", value: String(limit)),
        ])
        return try decode(
            [ReviewRunInfo].self,
            from: data,
            context: "/api/review/history",
            dateDecodingStrategy: .iso8601
        )
    }

    func runReview(
        target: ReviewTargetKind,
        projectPath: String?,
        baseRef: String?,
        headRef: String?,
        filePaths: [String]
    ) async throws -> ReviewRunInfo {
        struct Body: Encodable {
            let target: String
            let project_path: String?
            let base_ref: String?
            let head_ref: String?
            let file_paths: [String]
        }

        let body = try JSONEncoder().encode(
            Body(
                target: target.rawValue,
                project_path: projectPath,
                base_ref: baseRef,
                head_ref: headRef,
                file_paths: filePaths
            )
        )
        let data = try await post("/api/review/run", body: body)
        return try decode(
            ReviewRunInfo.self,
            from: data,
            context: "/api/review/run",
            dateDecodingStrategy: .iso8601
        )
    }

    func fetchJobs(limit: Int = 50) async throws -> [BackgroundJobInfo] {
        let data = try await get("/api/jobs", queryItems: [
            URLQueryItem(name: "limit", value: String(limit)),
        ])
        return try decode(
            [BackgroundJobInfo].self,
            from: data,
            context: "/api/jobs",
            dateDecodingStrategy: .iso8601
        )
    }

    func fetchJob(jobId: String) async throws -> BackgroundJobInfo {
        let data = try await get("/api/jobs/\(jobId)")
        return try decode(
            BackgroundJobInfo.self,
            from: data,
            context: "/api/jobs/\(jobId)",
            dateDecodingStrategy: .iso8601
        )
    }

    func launchBackgroundJob(
        message: String,
        sessionId: String?,
        mode: WorkMode,
        projectPath: String?,
        branchMode: String? = nil
    ) async throws -> BackgroundJobInfo {
        struct Body: Encodable {
            let message: String
            let session_id: String?
            let mode: String
            let project_path: String?
            let branch_mode: String?
        }

        let body = try JSONEncoder().encode(
            Body(
                message: message,
                session_id: sessionId,
                mode: mode.rawValue,
                project_path: projectPath,
                branch_mode: branchMode
            )
        )
        let data = try await post("/api/jobs", body: body)
        return try decode(
            BackgroundJobInfo.self,
            from: data,
            context: "/api/jobs",
            dateDecodingStrategy: .iso8601
        )
    }

    func fetchJobLog(jobId: String, limit: Int = 200) async throws -> BackgroundJobLogTailInfo {
        let data = try await get("/api/jobs/\(jobId)/logs", queryItems: [
            URLQueryItem(name: "limit", value: String(limit)),
        ])
        return try decode(
            BackgroundJobLogTailInfo.self,
            from: data,
            context: "/api/jobs/\(jobId)/logs",
            dateDecodingStrategy: .iso8601
        )
    }

    func cancelJob(jobId: String) async throws -> BackgroundJobInfo {
        let data = try await post("/api/jobs/\(jobId)/cancel", body: nil)
        return try decode(
            BackgroundJobInfo.self,
            from: data,
            context: "/api/jobs/\(jobId)/cancel",
            dateDecodingStrategy: .iso8601
        )
    }

    func resumeJob(jobId: String) async throws -> BackgroundJobInfo {
        let data = try await post("/api/jobs/\(jobId)/resume", body: nil)
        return try decode(
            BackgroundJobInfo.self,
            from: data,
            context: "/api/jobs/\(jobId)/resume",
            dateDecodingStrategy: .iso8601
        )
    }

    func takeOverJob(jobId: String) async throws -> BackgroundJobTakeoverInfo {
        let data = try await post("/api/jobs/\(jobId)/takeover", body: nil)
        return try decode(
            BackgroundJobTakeoverInfo.self,
            from: data,
            context: "/api/jobs/\(jobId)/takeover",
            dateDecodingStrategy: .iso8601
        )
    }

    func revokePermission(tool: String, scopeKey: String) async throws {
        _ = try await delete("/api/permissions", queryItems: [
            URLQueryItem(name: "tool", value: tool),
            URLQueryItem(name: "scope_key", value: scopeKey),
        ])
    }

    func deleteMemoryItem(sourceTable: String, itemId: Int) async throws {
        _ = try await delete("/api/memory/items/\(sourceTable)/\(itemId)")
    }

    func updateMemoryCandidate(candidateId: Int, label: String, text: String, evidence: String?) async throws {
        struct Body: Encodable {
            let key: String
            let value: String
            let evidence: String?
        }

        let body = try JSONEncoder().encode(Body(key: label, value: text, evidence: evidence))
        _ = try await patch("/api/memory/candidates/\(candidateId)", body: body)
    }

    func approveMemoryCandidate(
        candidateId: Int,
        label: String,
        text: String,
        evidence: String?,
        pin: Bool = false
    ) async throws {
        struct Body: Encodable {
            let key: String
            let value: String
            let evidence: String?
            let pin: Bool
        }

        let body = try JSONEncoder().encode(Body(key: label, value: text, evidence: evidence, pin: pin))
        _ = try await post("/api/memory/candidates/\(candidateId)/approve", body: body)
    }

    func rejectMemoryCandidate(candidateId: Int) async throws {
        _ = try await post("/api/memory/candidates/\(candidateId)/reject", body: nil)
    }

    func expireMemoryCandidate(candidateId: Int) async throws {
        _ = try await post("/api/memory/candidates/\(candidateId)/expire", body: nil)
    }

    func setMemoryPinned(itemId: Int, pinned: Bool) async throws {
        let action = pinned ? "pin" : "unpin"
        _ = try await post("/api/memory/items/durable_memories/\(itemId)/\(action)", body: nil)
    }

    func triggerScan() async throws -> [String: Any] {
        let data = try await post("/api/system/scan", body: nil)
        return try decodeJSONObject(from: data, context: "/api/system/scan")
    }

    // MARK: - HTTP helpers

    private func get(_ path: String, queryItems: [URLQueryItem] = []) async throws -> Data {
        try await request(method: "GET", path: path, queryItems: queryItems)
    }

    private func post(_ path: String, body: Data?) async throws -> Data {
        try await request(method: "POST", path: path, body: body)
    }

    private func patch(_ path: String, body: Data?) async throws -> Data {
        try await request(method: "PATCH", path: path, body: body)
    }

    private func delete(_ path: String, queryItems: [URLQueryItem] = []) async throws -> Data {
        try await request(method: "DELETE", path: path, queryItems: queryItems)
    }

    private func request(
        method: String,
        path: String,
        queryItems: [URLQueryItem] = [],
        body: Data? = nil
    ) async throws -> Data {
        let url = try buildURL(path: path, queryItems: queryItems)
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.httpBody = body
        if body != nil {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return try await send(req)
    }

    private func buildURL(path: String, queryItems: [URLQueryItem] = []) throws -> URL {
        guard var components = URLComponents(string: "\(baseURL)\(path)") else {
            throw APIError.invalidURL(path)
        }
        if !queryItems.isEmpty {
            components.queryItems = queryItems
        }
        guard let url = components.url else {
            throw APIError.invalidURL(path)
        }
        return url
    }

    private func send(_ request: URLRequest) async throws -> Data {
        do {
            let (data, response) = try await session.data(for: request)
            try validate(response: response, data: data)
            return data
        } catch let error as APIError {
            throw error
        } catch {
            throw APIError.transport(error.localizedDescription)
        }
    }

    private func validate(response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else {
            throw APIError.invalidResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            let message = serverMessage(from: data)
                ?? HTTPURLResponse.localizedString(forStatusCode: http.statusCode).capitalized
            throw APIError.http(statusCode: http.statusCode, message: message)
        }
    }

    private func decode<T: Decodable>(
        _ type: T.Type,
        from data: Data,
        context: String,
        dateDecodingStrategy: JSONDecoder.DateDecodingStrategy = .deferredToDate
    ) throws -> T {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = dateDecodingStrategy
        do {
            return try decoder.decode(type, from: data)
        } catch {
            throw APIError.decoding(context: context, message: Self.describeDecodingError(error))
        }
    }

    private func decodeJSONObject(from data: Data, context: String) throws -> [String: Any] {
        do {
            let object = try JSONSerialization.jsonObject(with: data)
            guard let dictionary = object as? [String: Any] else {
                throw APIError.decoding(context: context, message: "Expected a JSON object.")
            }
            return dictionary
        } catch let error as APIError {
            throw error
        } catch {
            throw APIError.decoding(context: context, message: error.localizedDescription)
        }
    }

    private func serverMessage(from data: Data) -> String? {
        guard !data.isEmpty else { return nil }

        if let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            for key in ["detail", "message", "error"] {
                if let value = payload[key] as? String, !value.isEmpty {
                    return value
                }
            }
        }

        let text = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
        if let text, !text.isEmpty {
            return text
        }
        return nil
    }

    private static func describeDecodingError(_ error: Error) -> String {
        switch error {
        case let DecodingError.keyNotFound(key, context):
            return "Missing key '\(key.stringValue)' at \(codingPathDescription(context.codingPath))."
        case let DecodingError.typeMismatch(_, context):
            return "Type mismatch at \(codingPathDescription(context.codingPath)): \(context.debugDescription)"
        case let DecodingError.valueNotFound(_, context):
            return "Missing value at \(codingPathDescription(context.codingPath)): \(context.debugDescription)"
        case let DecodingError.dataCorrupted(context):
            return context.debugDescription
        default:
            return error.localizedDescription
        }
    }

    private static func codingPathDescription(_ codingPath: [CodingKey]) -> String {
        guard !codingPath.isEmpty else {
            return "the top level"
        }
        return codingPath.map(\.stringValue).joined(separator: ".")
    }

    private func stream(request: URLRequest) -> AsyncStream<SSEEvent> {
        let sess = session
        return AsyncStream { continuation in
            let task = Task {
                do {
                    let (stream, response) = try await sess.bytes(for: request)

                    guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                        var evt = SSEEvent()
                        evt.type = "error"
                        evt.data = ["message": "Server returned an error"]
                        continuation.yield(evt)
                        continuation.finish()
                        return
                    }

                    var rawBuffer = Data()
                    for try await byte in stream {
                        rawBuffer.append(byte)

                        guard rawBuffer.count >= 2,
                              rawBuffer.suffix(2) == Data([0x0A, 0x0A]) else { continue }

                        guard let bufferStr = String(data: rawBuffer, encoding: .utf8) else {
                            continue
                        }

                        let lines = bufferStr.split(separator: "\n", omittingEmptySubsequences: false)
                        var event = SSEEvent()

                        for line in lines {
                            let lineStr = String(line)
                            if lineStr.hasPrefix("data: ") {
                                let jsonStr = String(lineStr.dropFirst(6))
                                if let jsonData = jsonStr.data(using: .utf8),
                                   let dict = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any] {
                                    var stringDict: [String: String] = [:]
                                    for (key, value) in dict {
                                        stringDict[key] = "\(value)"
                                    }
                                    event.data = stringDict
                                    event.type = stringDict["type"] ?? ""
                                }
                            } else if lineStr.hasPrefix("event: ") {
                                event.type = String(lineStr.dropFirst(7))
                            }
                        }

                        if !event.type.isEmpty {
                            let isDone = event.type == "done"
                            continuation.yield(event)
                            if isDone { break }
                        }
                        rawBuffer.removeAll(keepingCapacity: true)
                    }
                } catch {
                    var evt = SSEEvent()
                    evt.type = "error"
                    evt.data = ["message": "Connection failed: \(error.localizedDescription)"]
                    continuation.yield(evt)
                }
                continuation.finish()
            }

            continuation.onTermination = { _ in task.cancel() }
        }
    }
}
