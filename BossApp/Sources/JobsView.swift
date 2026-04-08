import AppKit
import SwiftUI

struct JobsView: View {
    @EnvironmentObject var vm: ChatViewModel

    var body: some View {
        ScrollView(.vertical, showsIndicators: false) {
            VStack(alignment: .leading, spacing: 24) {
                header
                    .padding(.top, 80)

                if let message = vm.jobsRefreshError {
                    InlineStatusBanner(message: message)
                }

                controlsCard

                if !vm.jobs.isEmpty {
                    jobsSection
                }

                if let job = vm.selectedJob {
                    detailSection(job)
                } else {
                    emptyState
                }
            }
            .frame(maxWidth: 680, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .center)
            .padding(.bottom, 32)
        }
        .task {
            await vm.refreshJobsSurface()
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Jobs")
                .font(.system(size: 18, weight: .semibold))
                .foregroundColor(BossColor.textPrimary)

            Text("Inspect local background tasks, tail their logs, and take them over into chat when needed")
                .font(.system(size: 13))
                .foregroundColor(Color.white.opacity(0.38))
        }
    }

    private var controlsCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Background Tasks")
                        .font(.system(size: 14, weight: .medium))
                        .foregroundColor(Color.white.opacity(0.9))

                    Text("Jobs run locally under the same safety and permission rules as foreground chat.")
                        .font(.system(size: 12))
                        .foregroundColor(Color.white.opacity(0.34))
                }

                Spacer()

                Button(action: { Task { await vm.refreshJobsSurface() } }) {
                    Text("Refresh")
                        .font(.system(size: 12))
                        .foregroundColor(Color.white.opacity(0.64))
                }
                .buttonStyle(.plain)
            }

            HStack(spacing: 24) {
                metric(label: "Jobs", value: vm.jobs.count)
                metric(label: "Running", value: vm.jobs.filter { $0.status == "running" }.count)
                metric(label: "Waiting", value: vm.jobs.filter { $0.status == "waiting_permission" }.count)
            }
        }
        .padding(16)
        .background(
            RoundedRectangle(cornerRadius: 12)
                .fill(Color.white.opacity(0.03))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(Color.white.opacity(0.05), lineWidth: 1)
        )
    }

    private func metric(label: String, value: Int) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("\(value)")
                .font(.system(size: 15, weight: .medium))
                .foregroundColor(Color.white.opacity(0.88))
            Text(label)
                .font(.system(size: 11))
                .foregroundColor(Color.white.opacity(0.32))
        }
    }

    private var jobsSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            sectionTitle("Recent Jobs", subtitle: "Persistent local history for asynchronous work")
                .padding(.bottom, 10)

            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(vm.jobs.prefix(12).enumerated()), id: \.element.id) { index, job in
                    jobRow(job)
                    if index < min(vm.jobs.count, 12) - 1 {
                        Rectangle()
                            .fill(Color.white.opacity(0.05))
                            .frame(height: 1)
                    }
                }
            }
        }
    }

    private func jobRow(_ job: BackgroundJobInfo) -> some View {
        let isSelected = vm.selectedJob?.jobId == job.jobId
        return Button {
            vm.selectJob(job)
        } label: {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(job.title)
                        .font(.system(size: 13, weight: .medium))
                        .foregroundColor(Color.white.opacity(0.88))
                        .lineLimit(1)

                    Text(job.latestEvent ?? job.prompt)
                        .font(.system(size: 11))
                        .foregroundColor(Color.white.opacity(0.34))
                        .lineLimit(1)
                }

                Spacer()

                statusPill(job.status)

                if let updatedAt = job.updatedAt {
                    Text(relativeDate(updatedAt))
                        .font(.system(size: 10))
                        .foregroundColor(Color.white.opacity(0.3))
                }
            }
            .padding(.vertical, 12)
            .padding(.horizontal, 10)
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(isSelected ? Color.white.opacity(0.04) : .clear)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private func detailSection(_ job: BackgroundJobInfo) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            sectionTitle("Job Detail", subtitle: job.prompt)
                .padding(.bottom, 10)

            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .top, spacing: 10) {
                    statusPill(job.status)
                    Text(job.mode.capitalized)
                        .font(.system(size: 11))
                        .foregroundColor(Color.white.opacity(0.42))
                    Spacer()
                    actionRow(job)
                }

                metadataLine(label: "Session", value: job.sessionId)
                if let projectPath = job.projectPath {
                    metadataLine(label: "Project", value: projectPath)
                }
                metadataLine(label: "Log", value: job.logPath)

                if let branchMessage = job.branchMessage, !branchMessage.isEmpty {
                    metadataLine(label: "Branch", value: branchMessage)
                }

                if let errorMessage = job.errorMessage, !errorMessage.isEmpty {
                    metadataLine(label: "Error", value: errorMessage)
                }

                if !job.approvals.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("PENDING APPROVALS")
                            .font(.system(size: 10, weight: .semibold))
                            .foregroundColor(Color.white.opacity(0.28))
                            .tracking(1.0)
                        ForEach(job.approvals) { approval in
                            Text("\(approval.title) · \(approval.scopeLabel)")
                                .font(.system(size: 12))
                                .foregroundColor(Color.white.opacity(0.58))
                        }
                    }
                }

                if !job.assistantPreview.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("ASSISTANT PREVIEW")
                            .font(.system(size: 10, weight: .semibold))
                            .foregroundColor(Color.white.opacity(0.28))
                            .tracking(1.0)
                        Text(job.assistantPreview)
                            .font(.system(size: 12))
                            .foregroundColor(Color.white.opacity(0.58))
                            .textSelection(.enabled)
                    }
                }

                if let log = vm.selectedJobLog {
                    logSection(log)
                }
            }
        }
    }

    private func actionRow(_ job: BackgroundJobInfo) -> some View {
        HStack(spacing: 10) {
            if !terminalStatuses.contains(job.status) {
                Button(action: { vm.cancelJob(job) }) {
                    Text("Cancel")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(Color.white.opacity(0.58))
                }
                .buttonStyle(.plain)
            }

            if resumableStatuses.contains(job.status) {
                Button(action: { vm.resumeJob(job) }) {
                    Text("Resume")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(Color.white.opacity(0.72))
                }
                .buttonStyle(.plain)
            }

            Button(action: { vm.takeOverJob(job) }) {
                Text("Take Over")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundColor(.white)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(
                        Capsule()
                            .fill(BossColor.accent)
                    )
            }
            .buttonStyle(.plain)

            Button(action: { openPath(job.logPath) }) {
                Text("Open Log")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundColor(Color.white.opacity(0.58))
            }
            .buttonStyle(.plain)
        }
    }

    private func metadataLine(label: String, value: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label.uppercased())
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(Color.white.opacity(0.28))
                .tracking(1.0)
            Text(value)
                .font(.system(size: 12))
                .foregroundColor(Color.white.opacity(0.58))
                .textSelection(.enabled)
        }
    }

    private func logSection(_ log: BackgroundJobLogTailInfo) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("LOG TAIL")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundColor(Color.white.opacity(0.28))
                    .tracking(1.0)

                Spacer()

                if log.truncated {
                    Text("Showing tail")
                        .font(.system(size: 10))
                        .foregroundColor(Color.white.opacity(0.28))
                }
            }

            Text(log.text.isEmpty ? "No log output yet." : log.text)
                .font(.system(size: 11, design: .monospaced))
                .foregroundColor(Color.white.opacity(0.62))
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(10)
                .background(
                    RoundedRectangle(cornerRadius: 10)
                        .fill(Color.white.opacity(0.04))
                )
        }
    }

    private var emptyState: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("No jobs yet")
                .font(.system(size: 14, weight: .medium))
                .foregroundColor(Color.white.opacity(0.82))

            Text("Launch a prompt from chat with the background button to let Boss work asynchronously on this machine.")
                .font(.system(size: 13))
                .foregroundColor(Color.white.opacity(0.34))
        }
    }

    private func sectionTitle(_ title: String, subtitle: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.system(size: 14, weight: .medium))
                .foregroundColor(Color.white.opacity(0.86))

            Text(subtitle)
                .font(.system(size: 12))
                .foregroundColor(Color.white.opacity(0.32))
                .lineLimit(2)
        }
    }

    private func statusPill(_ status: String) -> some View {
        Text(status.replacingOccurrences(of: "_", with: " ").uppercased())
            .font(.system(size: 10, weight: .semibold))
            .foregroundColor(.white)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(
                Capsule()
                    .fill(statusColor(status))
            )
    }

    private func statusColor(_ status: String) -> Color {
        switch status {
        case "running":
            return BossColor.accent.opacity(0.82)
        case "waiting_permission":
            return Color.white.opacity(0.28)
        case "completed":
            return Color.white.opacity(0.18)
        case "failed", "cancelled":
            return BossColor.accent
        case "taken_over":
            return Color.white.opacity(0.22)
        default:
            return Color.white.opacity(0.16)
        }
    }

    private var terminalStatuses: Set<String> {
        ["completed", "failed", "cancelled", "taken_over"]
    }

    private var resumableStatuses: Set<String> {
        ["waiting_permission", "failed", "cancelled", "interrupted", "taken_over"]
    }

    private func relativeDate(_ date: Date) -> String {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .short
        return formatter.localizedString(for: date, relativeTo: Date())
    }

    private func openPath(_ path: String) {
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
    }
}