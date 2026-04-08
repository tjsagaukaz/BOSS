import SwiftUI

/// Inline widget displayed within a chat message during an iterative loop run.
struct LoopProgressView: View {
    let status: LoopStatusInfo

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                statusIcon
                Text(statusLabel)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundColor(Color.white.opacity(0.88))

                Spacer()

                if let attempt = status.attempt {
                    Text("Attempt \(attempt)")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(Color.white.opacity(0.52))
                }
            }

            if let stopReason = status.stopReason {
                HStack(spacing: 6) {
                    Text("Stop:")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundColor(Color.white.opacity(0.36))
                    Text(stopReason.replacingOccurrences(of: "_", with: " "))
                        .font(.system(size: 11))
                        .foregroundColor(stopReasonColor)
                }
            }

            if let remaining = status.budgetRemaining {
                HStack(spacing: 16) {
                    if let a = remaining.attempts {
                        budgetItem(label: "Attempts left", value: "\(a)")
                    }
                    if let c = remaining.commands {
                        budgetItem(label: "Commands left", value: "\(c)")
                    }
                    if let w = remaining.wallSeconds {
                        budgetItem(label: "Time left", value: formatSeconds(w))
                    }
                }
            }
        }
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(backgroundFill)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(borderColor, lineWidth: 1)
        )
    }

    private var statusLabel: String {
        switch status.status {
        case "started": return "Loop Started"
        case "running": return "Loop Running"
        case "paused": return "Loop Paused"
        case "completed": return "Loop Completed"
        case "stopped": return "Loop Stopped"
        default: return "Loop \(status.status.capitalized)"
        }
    }

    @ViewBuilder
    private var statusIcon: some View {
        switch status.status {
        case "started", "running":
            ProgressView()
                .controlSize(.mini)
        case "completed":
            Image(systemName: "checkmark.circle.fill")
                .foregroundColor(.green)
                .font(.system(size: 13))
        case "paused":
            Image(systemName: "pause.circle.fill")
                .foregroundColor(.yellow)
                .font(.system(size: 13))
        case "stopped":
            Image(systemName: "xmark.circle.fill")
                .foregroundColor(BossColor.accent)
                .font(.system(size: 13))
        default:
            Image(systemName: "arrow.triangle.2.circlepath")
                .foregroundColor(Color.white.opacity(0.5))
                .font(.system(size: 13))
        }
    }

    private var stopReasonColor: Color {
        guard let reason = status.stopReason else { return Color.white.opacity(0.52) }
        switch reason {
        case "success": return .green
        case "approval_blocked": return .yellow
        default: return BossColor.accent
        }
    }

    private var backgroundFill: Color {
        switch status.status {
        case "completed": return Color.green.opacity(0.06)
        case "stopped": return BossColor.accent.opacity(0.06)
        case "paused": return Color.yellow.opacity(0.06)
        default: return Color.white.opacity(0.03)
        }
    }

    private var borderColor: Color {
        switch status.status {
        case "completed": return Color.green.opacity(0.15)
        case "stopped": return BossColor.accent.opacity(0.15)
        case "paused": return Color.yellow.opacity(0.15)
        default: return Color.white.opacity(0.06)
        }
    }

    private func budgetItem(label: String, value: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(value)
                .font(.system(size: 12, weight: .medium))
                .foregroundColor(Color.white.opacity(0.72))
            Text(label)
                .font(.system(size: 10))
                .foregroundColor(Color.white.opacity(0.32))
        }
    }

    private func formatSeconds(_ seconds: Double) -> String {
        if seconds >= 60 {
            let m = Int(seconds) / 60
            let s = Int(seconds) % 60
            return "\(m)m \(s)s"
        }
        return "\(Int(seconds))s"
    }
}
