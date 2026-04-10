import SwiftUI

// MARK: - Computer View (Spectator Surface)

struct ComputerView: View {
    @EnvironmentObject var vm: ChatViewModel

    private var state: ComputerState { vm.computerState }

    var body: some View {
        Group {
            if let session = state.session {
                sessionView(session)
            } else {
                emptyState
            }
        }
    }

    // MARK: - Empty State

    private var emptyState: some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "desktopcomputer")
                .font(.system(size: 32, weight: .thin))
                .foregroundColor(Color.white.opacity(0.18))
            Text("No Active Session")
                .font(.system(size: 18, weight: .semibold))
                .foregroundColor(BossColor.textPrimary)
            Text("Computer-use sessions will appear here when active.\nBoss drives a browser, you spectate.")
                .font(.system(size: 13))
                .foregroundColor(Color.white.opacity(0.38))
                .multilineTextAlignment(.center)
                .lineSpacing(4)
            Spacer()
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: - Active Session

    private func sessionView(_ session: ComputerSessionInfo) -> some View {
        ScrollView(.vertical, showsIndicators: false) {
            VStack(alignment: .leading, spacing: 0) {
                statusHeader(session)
                    .padding(.bottom, 20)

                screenshotDisplay(session)
                    .padding(.bottom, 16)

                telemetryBar(session)
                    .padding(.bottom, 20)

                actionControls(session)
                    .padding(.bottom, 24)

                Divider().background(BossColor.divider)
                    .padding(.bottom, 20)

                timelineSection
            }
            .padding(.horizontal, 24)
            .padding(.top, 20)
            .padding(.bottom, 40)
            .frame(maxWidth: 680)
            .frame(maxWidth: .infinity)
        }
    }

    // MARK: - Status Header

    private func statusHeader(_ session: ComputerSessionInfo) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text("Computer")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundColor(BossColor.textPrimary)
                Spacer()
                statusPill(session.status)
            }

            HStack(spacing: 6) {
                Image(systemName: "globe")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundColor(BossColor.textSecondary)
                    .frame(width: 14)
                Text(session.targetDomain ?? session.targetUrl ?? "—")
                    .font(.system(size: 13, weight: .medium, design: .monospaced))
                    .foregroundColor(BossColor.textSecondary)
                    .lineLimit(1)
            }

            HStack(spacing: 6) {
                Image(systemName: "cpu")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundColor(Color.white.opacity(0.3))
                    .frame(width: 14)
                Text(session.activeModel)
                    .font(.system(size: 12, weight: .regular, design: .monospaced))
                    .foregroundColor(Color.white.opacity(0.3))
            }
        }
    }

    private func statusPill(_ status: ComputerSessionStatus) -> some View {
        HStack(spacing: 6) {
            if status == .running {
                PulsingDot()
            } else {
                Circle()
                    .fill(status.color)
                    .frame(width: 7, height: 7)
            }
            Text(status.label)
                .font(.system(size: 12, weight: .medium))
                .foregroundColor(status.color)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(status.color.opacity(0.12))
        .clipShape(Capsule())
    }

    // MARK: - Screenshot Display

    private func screenshotDisplay(_ session: ComputerSessionInfo) -> some View {
        ZStack {
            // Placeholder screenshot — aspect-fit, letterboxed
            if let path = session.latestScreenshotPath,
               let nsImage = NSImage(contentsOfFile: path) {
                Image(nsImage: nsImage)
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .frame(maxWidth: .infinity)
            } else {
                // Placeholder
                Rectangle()
                    .fill(Color(hex: "#18181B"))
                    .aspectRatio(16.0 / 10.0, contentMode: .fit)
                    .overlay(
                        VStack(spacing: 8) {
                            Image(systemName: "photo")
                                .font(.system(size: 24, weight: .thin))
                                .foregroundColor(Color.white.opacity(0.12))
                            Text("Screenshot will appear here")
                                .font(.system(size: 11))
                                .foregroundColor(Color.white.opacity(0.2))
                        }
                    )
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(Color(hex: "#27272A"), lineWidth: 1)
        )
        .shadow(color: .black.opacity(0.4), radius: 12, y: 4)
    }

    // MARK: - Telemetry Bar

    private func telemetryBar(_ session: ComputerSessionInfo) -> some View {
        HStack(spacing: 0) {
            telemetryCell("TURN", value: "\(session.turnIndex)")
            telemetryDivider
            telemetryCell("ACTIONS", value: "\(session.lastActionBatch.count)")
            telemetryDivider
            telemetryCell("STATUS", value: lastActionOutcome(session))
            telemetryDivider
            telemetryCell("ELAPSED", value: elapsed(since: session.createdAt))

            Spacer(minLength: 0)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .background(Color.white.opacity(0.025))
        .clipShape(RoundedRectangle(cornerRadius: 7))
        .overlay(
            RoundedRectangle(cornerRadius: 7)
                .stroke(Color.white.opacity(0.06), lineWidth: 1)
        )
    }

    private func telemetryCell(_ label: String, value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.system(size: 9, weight: .semibold, design: .monospaced))
                .foregroundColor(Color.white.opacity(0.25))
                .tracking(1.2)
            Text(value)
                .font(.system(size: 12, weight: .medium, design: .monospaced))
                .foregroundColor(BossColor.textPrimary)
        }
        .frame(minWidth: 70, alignment: .leading)
    }

    private var telemetryDivider: some View {
        Rectangle()
            .fill(Color.white.opacity(0.06))
            .frame(width: 1, height: 28)
            .padding(.horizontal, 10)
    }

    // MARK: - Action Controls

    private func actionControls(_ session: ComputerSessionInfo) -> some View {
        HStack(spacing: 8) {
            if session.status == .running {
                ghostButton("Pause", icon: "pause.fill", disabled: true) {}
            }
            if session.status == .paused {
                ghostButton("Resume", icon: "play.fill", disabled: true) {}
            }
            if !session.status.isTerminal {
                destructiveButton("Stop", icon: "stop.fill", disabled: true) {}
            }

            Spacer()

            ghostButton("Refresh", icon: "arrow.clockwise", disabled: true) {}
            ghostButton("Open in Browser", icon: "arrow.up.forward.square") {
                if let url = session.targetUrl, let u = URL(string: url) {
                    NSWorkspace.shared.open(u)
                }
            }
        }
    }

    private func ghostButton(_ label: String, icon: String, disabled: Bool = false, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 5) {
                Image(systemName: icon)
                    .font(.system(size: 10, weight: .semibold))
                    .frame(width: 14)
                Text(label)
                    .font(.system(size: 12, weight: .medium))
            }
            .foregroundColor(Color.white.opacity(disabled ? 0.2 : 0.55))
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(
                RoundedRectangle(cornerRadius: 7)
                    .stroke(Color.white.opacity(disabled ? 0.05 : 0.12), lineWidth: 1)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(disabled)
    }

    private func destructiveButton(_ label: String, icon: String, disabled: Bool = false, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 5) {
                Image(systemName: icon)
                    .font(.system(size: 10, weight: .semibold))
                    .frame(width: 14)
                Text(label)
                    .font(.system(size: 12, weight: .medium))
            }
            .foregroundColor(disabled ? Color.white.opacity(0.35) : .white)
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(disabled ? BossColor.accent.opacity(0.3) : BossColor.accent)
            .clipShape(RoundedRectangle(cornerRadius: 7))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(disabled)
    }

    // MARK: - Timeline

    private var timelineSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("TIMELINE")
                .font(.system(size: 11, weight: .medium))
                .foregroundColor(Color.white.opacity(0.25))
                .tracking(1.8)
                .padding(.bottom, 4)

            if state.events.isEmpty {
                Text("No events yet")
                    .font(.system(size: 12))
                    .foregroundColor(Color.white.opacity(0.25))
            } else {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(state.events.reversed()) { event in
                        timelineRow(event)
                    }
                }
            }
        }
    }

    private func timelineRow(_ event: ComputerEventInfo) -> some View {
        HStack(alignment: .top, spacing: 10) {
            // Timestamp
            Text(timelineTimestamp(event.timestamp))
                .font(.system(size: 10, weight: .regular, design: .monospaced))
                .foregroundColor(Color.white.opacity(0.2))
                .frame(width: 52, alignment: .trailing)

            // Dot connector
            VStack(spacing: 0) {
                Circle()
                    .fill(eventDotColor(event.event))
                    .frame(width: 5, height: 5)
                    .padding(.top, 4)
                Rectangle()
                    .fill(Color.white.opacity(0.06))
                    .frame(width: 1)
                    .frame(minHeight: 16)
            }
            .frame(width: 5)

            // Content
            VStack(alignment: .leading, spacing: 2) {
                Text(eventLabel(event.event))
                    .font(.system(size: 12, weight: .medium))
                    .foregroundColor(BossColor.textSecondary)
                if let detail = event.detail {
                    Text(detail)
                        .font(.system(size: 11, weight: .regular, design: .monospaced))
                        .foregroundColor(Color.white.opacity(0.25))
                        .lineLimit(2)
                }
            }
            .padding(.bottom, 8)

            Spacer(minLength: 0)
        }
    }

    // MARK: - Helpers

    private func lastActionOutcome(_ session: ComputerSessionInfo) -> String {
        guard let last = session.lastActionResults.last else { return "—" }
        return last.success ? "OK" : "FAIL"
    }

    private func elapsed(since date: Date) -> String {
        let seconds = Int(Date().timeIntervalSince(date))
        if seconds < 60 { return "\(seconds)s" }
        let minutes = seconds / 60
        let secs = seconds % 60
        return "\(minutes)m \(secs)s"
    }

    private func timelineTimestamp(_ date: Date) -> String {
        let fmt = DateFormatter()
        fmt.dateFormat = "HH:mm:ss"
        return fmt.string(from: date)
    }

    private func eventDotColor(_ event: String) -> Color {
        switch event {
        case "error", "budget_exhausted":
            return BossColor.accent
        case "completed":
            return Color(hex: "#34D399")
        case "action_executed":
            return Color.white.opacity(0.25)
        default:
            return Color.white.opacity(0.12)
        }
    }

    private func eventLabel(_ event: String) -> String {
        event.replacingOccurrences(of: "_", with: " ").localizedCapitalized
    }
}

// MARK: - Pulsing Dot

private struct PulsingDot: View {
    @State private var isPulsing = false

    var body: some View {
        ZStack {
            Circle()
                .fill(BossColor.accent.opacity(0.3))
                .frame(width: 12, height: 12)
                .scaleEffect(isPulsing ? 1.4 : 0.8)
                .opacity(isPulsing ? 0.0 : 0.6)
            Circle()
                .fill(BossColor.accent)
                .frame(width: 7, height: 7)
        }
        .onAppear {
            withAnimation(.easeInOut(duration: 1.2).repeatForever(autoreverses: false)) {
                isPulsing = true
            }
        }
    }
}

// MARK: - Active Session Pip

struct ComputerSessionPip: View {
    let domain: String
    let status: ComputerSessionStatus
    let onTap: () -> Void

    @State private var isHovered = false

    var body: some View {
        Button(action: onTap) {
            HStack(spacing: 7) {
                if status == .running {
                    PulsingDot()
                        .scaleEffect(0.7)
                } else {
                    Circle()
                        .fill(status.color)
                        .frame(width: 6, height: 6)
                }
                Image(systemName: "desktopcomputer")
                    .font(.system(size: 10, weight: .medium))
                    .foregroundColor(Color.white.opacity(0.7))
                    .frame(width: 14)
                Text(domain)
                    .font(.system(size: 11, weight: .medium, design: .monospaced))
                    .foregroundColor(Color.white.opacity(0.6))
                    .lineLimit(1)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(
                Capsule()
                    .fill(Color.white.opacity(isHovered ? 0.08 : 0.04))
            )
            .overlay(
                Capsule()
                    .stroke(Color.white.opacity(0.08), lineWidth: 1)
            )
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .onHover { isHovered = $0 }
        .animation(.easeInOut(duration: 0.15), value: isHovered)
    }
}
