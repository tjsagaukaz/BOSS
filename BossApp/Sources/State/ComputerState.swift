import SwiftUI

// MARK: - Computer Session State

@MainActor
final class ComputerState: ObservableObject {
    @Published var session: ComputerSessionInfo?
    @Published var events: [ComputerEventInfo] = []
    @Published var capabilities: ComputerCapabilitiesInfo?
    @Published var refreshError: String?
    @Published var isActive: Bool = false

    private let api = APIClient.shared

    // Dummy data for layout testing — replaced when API endpoints ship
    func loadDummy() {
        let now = Date()
        session = ComputerSessionInfo(
            sessionId: "a1b2c3d4e5f6",
            targetUrl: "https://github.com/settings/tokens",
            targetDomain: "github.com",
            status: .running,
            browserStatus: .active,
            activeModel: "gpt-5.4",
            turnIndex: 7,
            latestScreenshotPath: nil,
            latestScreenshotTimestamp: now.addingTimeInterval(-3),
            lastActionBatch: [
                ComputerActionInfo(type: "click", x: 412, y: 308),
                ComputerActionInfo(type: "type", text: "boss-token"),
            ],
            lastActionResults: [
                ComputerActionResultInfo(actionType: "click", success: true),
                ComputerActionResultInfo(actionType: "type", success: true),
            ],
            createdAt: now.addingTimeInterval(-45),
            updatedAt: now.addingTimeInterval(-3),
            error: nil
        )
        events = [
            ComputerEventInfo(timestamp: now.addingTimeInterval(-45), event: "session_created", detail: "Target: github.com/settings/tokens"),
            ComputerEventInfo(timestamp: now.addingTimeInterval(-42), event: "browser_launching", detail: nil),
            ComputerEventInfo(timestamp: now.addingTimeInterval(-40), event: "browser_ready", detail: nil),
            ComputerEventInfo(timestamp: now.addingTimeInterval(-39), event: "navigated", detail: "https://github.com/settings/tokens"),
            ComputerEventInfo(timestamp: now.addingTimeInterval(-35), event: "screenshot", detail: "Turn 1"),
            ComputerEventInfo(timestamp: now.addingTimeInterval(-30), event: "action_executed", detail: "click (412, 308)"),
            ComputerEventInfo(timestamp: now.addingTimeInterval(-28), event: "action_executed", detail: "type \"Generate new…\""),
            ComputerEventInfo(timestamp: now.addingTimeInterval(-25), event: "turn_completed", detail: "Turn 1 — 2 actions, 0 failures"),
            ComputerEventInfo(timestamp: now.addingTimeInterval(-20), event: "screenshot", detail: "Turn 2"),
            ComputerEventInfo(timestamp: now.addingTimeInterval(-15), event: "action_executed", detail: "click (520, 440)"),
            ComputerEventInfo(timestamp: now.addingTimeInterval(-12), event: "turn_completed", detail: "Turn 2 — 1 action, 0 failures"),
            ComputerEventInfo(timestamp: now.addingTimeInterval(-8), event: "screenshot", detail: "Turn 7"),
            ComputerEventInfo(timestamp: now.addingTimeInterval(-5), event: "action_executed", detail: "click (412, 308)"),
            ComputerEventInfo(timestamp: now.addingTimeInterval(-3), event: "action_executed", detail: "type \"boss-token\""),
        ]
        capabilities = ComputerCapabilitiesInfo(
            playwrightInstalled: true,
            browsersInstalled: true,
            screenshotSupported: true,
            modelReady: true,
            model: "gpt-5.4",
            canRunSession: true
        )
        isActive = true
    }

    func clearSession() {
        session = nil
        events = []
        isActive = false
    }
}

// MARK: - Data Models

enum ComputerSessionStatus: String, Codable {
    case created
    case launching
    case running
    case paused
    case waitingApproval = "waiting_approval"
    case completed
    case failed
    case cancelled

    var label: String {
        switch self {
        case .created: return "Created"
        case .launching: return "Launching"
        case .running: return "Running"
        case .paused: return "Paused"
        case .waitingApproval: return "Waiting Approval"
        case .completed: return "Completed"
        case .failed: return "Failed"
        case .cancelled: return "Cancelled"
        }
    }

    var isTerminal: Bool {
        self == .completed || self == .failed || self == .cancelled
    }

    var color: Color {
        switch self {
        case .running: return BossColor.accent
        case .paused, .waitingApproval: return Color(hex: "#FBBF24")
        case .completed: return Color(hex: "#34D399")
        case .failed, .cancelled: return Color(hex: "#F87171")
        case .created, .launching: return BossColor.textSecondary
        }
    }
}

enum ComputerBrowserStatus: String, Codable {
    case notStarted = "not_started"
    case launching
    case ready
    case navigating
    case active
    case closed
    case error
}

struct ComputerSessionInfo: Identifiable {
    var id: String { sessionId }
    let sessionId: String
    let targetUrl: String?
    let targetDomain: String?
    let status: ComputerSessionStatus
    let browserStatus: ComputerBrowserStatus
    let activeModel: String
    let turnIndex: Int
    let latestScreenshotPath: String?
    let latestScreenshotTimestamp: Date?
    let lastActionBatch: [ComputerActionInfo]
    let lastActionResults: [ComputerActionResultInfo]
    let createdAt: Date
    let updatedAt: Date
    let error: String?
}

struct ComputerActionInfo: Identifiable {
    let id = UUID()
    let type: String
    var x: Int? = nil
    var y: Int? = nil
    var text: String? = nil
    var key: String? = nil
    var url: String? = nil

    var summary: String {
        switch type {
        case "click", "double_click", "move":
            if let x, let y { return "\(type)(\(x), \(y))" }
            return type
        case "type":
            return "type \"\(text?.prefix(20) ?? "")…\""
        case "keypress":
            return "key(\(key ?? "?"))"
        case "scroll":
            return "scroll"
        case "navigate":
            return "→ \(url?.prefix(30) ?? "?")"
        case "wait":
            return "wait"
        default:
            return type
        }
    }
}

struct ComputerActionResultInfo: Identifiable {
    let id = UUID()
    let actionType: String
    let success: Bool
    var error: String? = nil
}

struct ComputerEventInfo: Identifiable {
    let id = UUID()
    let timestamp: Date
    let event: String
    let detail: String?
}

struct ComputerCapabilitiesInfo {
    let playwrightInstalled: Bool
    let browsersInstalled: Bool
    let screenshotSupported: Bool
    let modelReady: Bool
    let model: String?
    let canRunSession: Bool
}
