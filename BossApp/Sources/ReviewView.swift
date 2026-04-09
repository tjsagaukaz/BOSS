import AppKit
import SwiftUI

struct ReviewView: View {
    @EnvironmentObject var vm: ChatViewModel

    var body: some View {
        ScrollView(.vertical, showsIndicators: false) {
            VStack(alignment: .leading, spacing: 24) {
                header
                    .padding(.top, 80)

                if let message = vm.reviewState.reviewRefreshError {
                    InlineStatusBanner(message: message)
                }

                controlsCard

                if !vm.reviewState.reviewHistory.isEmpty {
                    historySection
                }

                if let run = vm.reviewState.selectedReviewRun {
                    reviewResultSection(run)
                } else {
                    emptyState
                }
            }
            .frame(maxWidth: 680, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .center)
            .padding(.bottom, 32)
        }
        .task {
            await vm.reviewState.refresh(fallbackProjectPath: vm.selectedProjectPath)
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Review")
                .font(.system(size: 18, weight: .semibold))
                .foregroundColor(BossColor.textPrimary)

            Text("Review local diffs, files, or indexed project context with Boss-native findings-first output")
                .font(.system(size: 13))
                .foregroundColor(Color.white.opacity(0.38))
        }
    }

    private var controlsCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Review Target")
                        .font(.system(size: 14, weight: .medium))
                        .foregroundColor(Color.white.opacity(0.9))

                    Text("Pick the local evidence Boss should review. Review mode stays read-only.")
                        .font(.system(size: 12))
                        .foregroundColor(Color.white.opacity(0.34))
                }

                Spacer()

                BossTertiaryButton(title: "Refresh") {
                    Task { await vm.reviewState.refresh(fallbackProjectPath: vm.selectedProjectPath) }
                }
                .help("Refresh")
            }

            if !vm.projects.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Project")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundColor(Color.white.opacity(0.32))
                        .tracking(1.0)

                    Picker("Project", selection: Binding<String>(
                        get: { vm.reviewState.selectedReviewProjectPath ?? (vm.reviewState.reviewCapabilities?.projectPath ?? vm.projects.first?.path ?? "") },
                        set: { newValue in
                            vm.reviewState.selectedReviewProjectPath = newValue.isEmpty ? nil : newValue
                            Task { await vm.reviewState.refresh(fallbackProjectPath: vm.selectedProjectPath) }
                        }
                    )) {
                        ForEach(vm.projects) { project in
                            Text(project.name).tag(project.path)
                        }
                    }
                    .pickerStyle(.menu)
                }
            }

            VStack(alignment: .leading, spacing: 6) {
                Text("Mode")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(Color.white.opacity(0.32))
                    .tracking(1.0)

                Picker("Review target", selection: Binding<ReviewTargetKind>(
                    get: { vm.reviewState.selectedReviewTarget },
                    set: { vm.reviewState.selectTarget($0) }
                )) {
                    ForEach(availableTargets, id: \.rawValue) { target in
                        Text(target.label).tag(target)
                    }
                }
                .pickerStyle(.menu)
            }

            if let capabilities = vm.reviewState.reviewCapabilities {
                capabilitiesCard(capabilities)
            }

            if vm.reviewState.selectedReviewTarget == .branchDiff {
                HStack(spacing: 12) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Base Ref")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundColor(Color.white.opacity(0.32))
                            .tracking(1.0)

                        TextField("main", text: $vm.reviewState.reviewBaseRef)
                            .textFieldStyle(.plain)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 8)
                            .background(
                                RoundedRectangle(cornerRadius: 8)
                                    .fill(Color.white.opacity(0.04))
                            )
                    }

                    VStack(alignment: .leading, spacing: 6) {
                        Text("Head Ref")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundColor(Color.white.opacity(0.32))
                            .tracking(1.0)

                        TextField(vm.reviewState.reviewCapabilities?.currentBranch ?? "feature", text: $vm.reviewState.reviewHeadRef)
                            .textFieldStyle(.plain)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 8)
                            .background(
                                RoundedRectangle(cornerRadius: 8)
                                    .fill(Color.white.opacity(0.04))
                            )
                    }
                }
            }

            if vm.reviewState.selectedReviewTarget == .files {
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text("Files")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundColor(Color.white.opacity(0.32))
                            .tracking(1.0)

                        Spacer()

                        BossTertiaryButton(title: "Choose Files") { pickFiles() }
                    }

                    TextEditor(text: $vm.reviewState.reviewFilePathsText)
                        .font(.system(size: 12))
                        .foregroundColor(Color.white.opacity(0.82))
                        .scrollContentBackground(.hidden)
                        .frame(minHeight: 88)
                        .padding(8)
                        .background(
                            RoundedRectangle(cornerRadius: 10)
                                .fill(Color.white.opacity(0.04))
                        )
                }
            }

            HStack(spacing: 12) {
                BossPrimaryButton(title: vm.reviewState.isRunningReview ? "Reviewing" : "Run Review") {
                    vm.reviewState.runReview()
                }
                .disabled(vm.reviewState.isRunningReview)

                Text(runButtonHint)
                    .font(.system(size: 11))
                    .foregroundColor(Color.white.opacity(0.34))
            }
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color.white.opacity(0.035)))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.white.opacity(0.06), lineWidth: 1))
    }

    private func capabilitiesCard(_ capabilities: ReviewCapabilitiesInfo) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 24) {
                metric(label: "Working Tree", value: capabilities.workingTreeFiles.count)
                metric(label: "Staged", value: capabilities.stagedFiles.count)
                metric(label: "Indexed", value: capabilities.indexedProjectAvailable ? 1 : 0)
            }

            VStack(alignment: .leading, spacing: 4) {
                if let repoRoot = capabilities.repoRoot {
                    Text("Repo: \(repoRoot)")
                }
                if let branch = capabilities.currentBranch, !branch.isEmpty {
                    Text("Branch: \(branch)")
                }
                Text("Default target: \(capabilities.defaultTarget.replacingOccurrences(of: "_", with: " "))")
            }
            .font(.system(size: 11))
            .foregroundColor(Color.white.opacity(0.32))
        }
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

    private var historySection: some View {
        VStack(alignment: .leading, spacing: 0) {
            sectionTitle("Recent Reviews", subtitle: "Local-first review history stored by Boss")
                .padding(.bottom, 10)

            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(vm.reviewState.reviewHistory.prefix(8).enumerated()), id: \.element.id) { index, run in
                    historyRow(run)
                    if index < min(vm.reviewState.reviewHistory.count, 8) - 1 {
                        Rectangle()
                            .fill(Color.white.opacity(0.05))
                            .frame(height: 1)
                    }
                }
            }
        }
    }

    private func historyRow(_ run: ReviewRunInfo) -> some View {
        let isSelected = vm.reviewState.selectedReviewRun?.reviewId == run.reviewId
        return Button {
            vm.reviewState.selectRun(run)
        } label: {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(run.title)
                        .font(.system(size: 13, weight: .medium))
                        .foregroundColor(Color.white.opacity(0.88))
                        .lineLimit(1)

                    Text(run.scopeSummary)
                        .font(.system(size: 11))
                        .foregroundColor(Color.white.opacity(0.34))
                        .lineLimit(1)
                }

                Spacer()

                Text(summaryBadgeText(run))
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundColor(Color.white.opacity(0.68))
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(
                        Capsule()
                            .fill(Color.white.opacity(0.07))
                    )

                if let createdAt = run.createdAt {
                    Text(relativeDate(createdAt))
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

    private func reviewResultSection(_ run: ReviewRunInfo) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            sectionTitle("Findings", subtitle: run.scopeSummary)
                .padding(.bottom, 10)

            VStack(alignment: .leading, spacing: 14) {
                Text(run.summary)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundColor(Color.white.opacity(0.86))

                if !run.residualRisk.isEmpty {
                    Text("Residual risk: \(run.residualRisk)")
                        .font(.system(size: 12))
                        .foregroundColor(Color.white.opacity(0.42))
                }

                if run.findings.isEmpty {
                    Text("No actionable findings were reported for this review target.")
                        .font(.system(size: 12))
                        .foregroundColor(Color.white.opacity(0.36))
                } else {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(Array(run.findings.enumerated()), id: \.element.id) { index, finding in
                            findingRow(finding, run: run)
                            if index < run.findings.count - 1 {
                                Rectangle()
                                    .fill(Color.white.opacity(0.05))
                                    .frame(height: 1)
                            }
                        }
                    }
                }
            }
        }
    }

    private func findingRow(_ finding: ReviewFindingInfo, run: ReviewRunInfo) -> some View {
        let fileURL = resolvedFileURL(for: finding, in: run)
        return VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                severityPill(finding.severity)

                if let fileURL {
                    Button(action: { openFile(fileURL) }) {
                        Text(finding.filePath)
                            .font(.system(size: 13, weight: .medium))
                            .foregroundColor(Color.white.opacity(0.88))
                            .lineLimit(1)
                    }
                    .buttonStyle(.plain)
                    .help(fileURL.path)
                } else {
                    Text(finding.filePath)
                        .font(.system(size: 13, weight: .medium))
                        .foregroundColor(Color.white.opacity(0.88))
                        .lineLimit(1)
                }

                Spacer(minLength: 0)

                if let fileURL {
                    Button(action: { revealFile(fileURL) }) {
                        Text("Reveal")
                            .font(.system(size: 11, weight: .medium))
                            .foregroundColor(Color.white.opacity(0.52))
                    }
                    .buttonStyle(.plain)

                    Button(action: { openFile(fileURL) }) {
                        Text("Open")
                            .font(.system(size: 11, weight: .medium))
                            .foregroundColor(Color.white.opacity(0.72))
                    }
                    .buttonStyle(.plain)
                }
            }

            reviewLine(label: "Evidence", text: finding.evidence)
            reviewLine(label: "Risk", text: finding.risk)
            reviewLine(label: "Recommended Fix", text: finding.recommendedFix)
        }
        .padding(.vertical, 12)
    }

    private func reviewLine(label: String, text: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label.uppercased())
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(Color.white.opacity(0.28))
                .tracking(1.0)
            Text(text)
                .font(.system(size: 12))
                .foregroundColor(Color.white.opacity(0.58))
                .textSelection(.enabled)
        }
    }

    private func severityPill(_ severity: String) -> some View {
        Text(severity.uppercased())
            .font(.system(size: 10, weight: .semibold))
            .foregroundColor(.white)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(
                Capsule()
                    .fill(severityColor(severity))
            )
    }

    private func severityColor(_ severity: String) -> Color {
        switch severity.lowercased() {
        case "critical":
            return BossColor.accent
        case "high":
            return BossColor.accent.opacity(0.82)
        case "medium":
            return Color.white.opacity(0.28)
        default:
            return Color.white.opacity(0.16)
        }
    }

    private var emptyState: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("No review selected")
                .font(.system(size: 14, weight: .medium))
                .foregroundColor(Color.white.opacity(0.82))

            Text("Run a review or choose a result from history to inspect findings.")
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
        }
    }

    private var availableTargets: [ReviewTargetKind] {
        var targets: [ReviewTargetKind] = [.auto]
        if let capabilities = vm.reviewState.reviewCapabilities {
            targets.append(contentsOf: capabilities.availableTargets.compactMap(ReviewTargetKind.init(rawValue:)))
        } else {
            targets.append(contentsOf: [.workingTree, .staged, .branchDiff, .files, .projectSummary])
        }
        var seen: Set<String> = []
        return targets.filter { seen.insert($0.rawValue).inserted }
    }

    private var runButtonHint: String {
        switch vm.reviewState.selectedReviewTarget {
        case .branchDiff:
            return "Base and head refs are required."
        case .files:
            return "Add one or more local file paths or choose files."
        default:
            return "Boss will review local evidence and return findings only."
        }
    }

    private func summaryBadgeText(_ run: ReviewRunInfo) -> String {
        if run.findings.isEmpty {
            return "Clear"
        }
        return "\(run.findings.count) finding(s)"
    }

    private func pickFiles() {
        let panel = NSOpenPanel()
        panel.title = "Choose Files to Review"
        panel.message = "Select local files for Boss to review."
        panel.prompt = "Use Files"
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = true
        panel.resolvesAliases = true

        if panel.runModal() == .OK {
            let paths = panel.urls.map(\.path)
            vm.reviewState.reviewFilePathsText = paths.joined(separator: "\n")
        }
    }

    private func relativeDate(_ date: Date) -> String {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .short
        return formatter.localizedString(for: date, relativeTo: Date())
    }

    private func resolvedFileURL(for finding: ReviewFindingInfo, in run: ReviewRunInfo) -> URL? {
        let rawPath = finding.filePath.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !rawPath.isEmpty, rawPath.lowercased() != "unknown" else {
            return nil
        }

        if rawPath.hasPrefix("/") {
            let absoluteURL = URL(fileURLWithPath: rawPath).standardizedFileURL
            if FileManager.default.fileExists(atPath: absoluteURL.path) {
                return absoluteURL
            }
        }

        let basePaths = [run.repoRoot, run.projectPath]
            .compactMap { $0 }
            .map { URL(fileURLWithPath: $0, isDirectory: true).standardizedFileURL }

        for baseURL in basePaths {
            let candidate = baseURL.appendingPathComponent(rawPath).standardizedFileURL
            if FileManager.default.fileExists(atPath: candidate.path) {
                return candidate
            }
        }

        return nil
    }

    private func openFile(_ url: URL) {
        NSWorkspace.shared.open(url)
    }

    private func revealFile(_ url: URL) {
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }
}
