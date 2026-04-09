import SwiftUI

struct IOSDeliveryView: View {
    @EnvironmentObject var vm: ChatViewModel

    private var state: IOSDeliveryState { vm.iosDeliveryState }

    @State private var showNewRunForm = false
    @State private var newRunProjectPath = ""
    @State private var newRunScheme = ""
    @State private var newRunConfiguration = "Release"
    @State private var newRunExportMethod = "app-store"
    @State private var newRunUploadTarget = "none"

    var body: some View {
        ScrollView(.vertical, showsIndicators: false) {
            VStack(alignment: .leading, spacing: 24) {
                header
                    .padding(.top, 80)

                if let error = state.refreshError {
                    InlineStatusBanner(message: error)
                }
                if let error = state.actionError {
                    InlineStatusBanner(message: error)
                }

                signingReadinessCard

                if showNewRunForm {
                    newRunCard
                }

                if !state.runs.isEmpty {
                    runsSection
                }

                if let run = state.selectedRun {
                    runDetailCard(run)
                } else if !showNewRunForm {
                    emptyState
                }
            }
            .frame(maxWidth: 680, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .center)
            .padding(.bottom, 32)
        }
        .task {
            await state.refresh()
        }
    }

    // MARK: - Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text("iOS Delivery")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundColor(BossColor.textPrimary)

                Spacer()

                BossSecondaryButton(title: "New Run", icon: "plus") {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        showNewRunForm.toggle()
                    }
                }

                BossTertiaryButton(title: "Refresh") {
                    Task { await state.refresh() }
                }
            }

            Text("Archive, export, and upload iOS builds to TestFlight")
                .font(.system(size: 13))
                .foregroundColor(Color.white.opacity(0.38))
        }
    }

    // MARK: - New Run Form

    private var newRunCard: some View {
        BossCard {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("New Delivery Run")
                        .font(.system(size: 14, weight: .medium))
                        .foregroundColor(Color.white.opacity(0.9))
                    Spacer()
                    Button(action: { withAnimation { showNewRunForm = false } }) {
                        Image(systemName: "xmark")
                            .font(.system(size: 11, weight: .medium))
                            .foregroundColor(Color.white.opacity(0.4))
                    }
                    .buttonStyle(.plain)
                }

                // Project path
                formField(label: "Project Path") {
                    TextField("~/Developer/MyApp", text: $newRunProjectPath)
                        .textFieldStyle(.plain)
                        .font(.system(size: 12, design: .monospaced))
                        .padding(6)
                        .background(RoundedRectangle(cornerRadius: 6).fill(Color.white.opacity(0.05)))
                        .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.white.opacity(0.1), lineWidth: 1))
                }

                // Scheme (optional)
                formField(label: "Scheme") {
                    TextField("Auto-detect", text: $newRunScheme)
                        .textFieldStyle(.plain)
                        .font(.system(size: 12))
                        .padding(6)
                        .background(RoundedRectangle(cornerRadius: 6).fill(Color.white.opacity(0.05)))
                        .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.white.opacity(0.1), lineWidth: 1))
                }

                HStack(spacing: 16) {
                    // Configuration
                    formField(label: "Config") {
                        Picker("", selection: $newRunConfiguration) {
                            Text("Release").tag("Release")
                            Text("Debug").tag("Debug")
                        }
                        .pickerStyle(.segmented)
                        .frame(maxWidth: 160)
                    }

                    // Export method
                    formField(label: "Export") {
                        Picker("", selection: $newRunExportMethod) {
                            Text("App Store").tag("app-store")
                            Text("Ad Hoc").tag("ad-hoc")
                            Text("Development").tag("development")
                        }
                        .labelsHidden()
                        .frame(maxWidth: 140)
                    }

                    // Upload target
                    formField(label: "Upload") {
                        Picker("", selection: $newRunUploadTarget) {
                            Text("None").tag("none")
                            Text("TestFlight").tag("testflight")
                        }
                        .labelsHidden()
                        .frame(maxWidth: 130)
                    }
                }

                HStack {
                    Spacer()
                    if state.isCreatingRun {
                        ProgressView()
                            .scaleEffect(0.7)
                    }
                    BossPrimaryButton(title: state.isCreatingRun ? "Starting…" : "Start Pipeline") {
                        Task {
                            await state.createAndStartRun(
                                projectPath: newRunProjectPath,
                                scheme: newRunScheme.isEmpty ? nil : newRunScheme,
                                configuration: newRunConfiguration,
                                exportMethod: newRunExportMethod,
                                uploadTarget: newRunUploadTarget
                            )
                            if state.actionError == nil {
                                withAnimation { showNewRunForm = false }
                            }
                        }
                    }
                    .disabled(newRunProjectPath.trimmingCharacters(in: .whitespaces).isEmpty || state.isCreatingRun)
                }
            }
        }
    }

    private func formField<Content: View>(label: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.system(size: 11, weight: .medium))
                .foregroundColor(Color.white.opacity(0.4))
            content()
        }
    }

    // MARK: - Signing Readiness

    private var signingReadinessCard: some View {
        BossCard {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("Signing & Credentials")
                        .font(.system(size: 14, weight: .medium))
                        .foregroundColor(Color.white.opacity(0.9))
                    Spacer()

                    if let signing = state.status?.signing {
                        HStack(spacing: 8) {
                            readinessPill("Sign", ready: signing.canSign)
                            readinessPill("Upload", ready: signing.canUpload)
                        }
                    }
                }

                if let signing = state.status?.signing {
                    if signing.configFileCorrupt {
                        HStack(spacing: 6) {
                            Image(systemName: "exclamationmark.triangle")
                                .font(.system(size: 11))
                                .foregroundColor(BossColor.accent)
                            Text(signing.configCorruptReason ?? "Config file is corrupt")
                                .font(.system(size: 11))
                                .foregroundColor(BossColor.accent.opacity(0.8))
                        }
                    }

                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(signing.checks) { check in
                            HStack(spacing: 6) {
                                credentialIcon(check.status)
                                Text(check.name.replacingOccurrences(of: "_", with: " "))
                                    .font(.system(size: 12, weight: .medium))
                                    .foregroundColor(Color.white.opacity(0.7))
                                Text(check.detail)
                                    .font(.system(size: 11))
                                    .foregroundColor(Color.white.opacity(0.34))
                                    .lineLimit(1)
                                Spacer()
                                StatusPill(text: check.status, color: credentialColor(check.status))
                            }
                        }
                    }
                } else {
                    Text("Loading…")
                        .font(.system(size: 12))
                        .foregroundColor(Color.white.opacity(0.34))
                }
            }
        }
    }

    // MARK: - Runs List

    private var runsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Runs")
                    .font(.system(size: 14, weight: .medium))
                    .foregroundColor(Color.white.opacity(0.7))
                Spacer()
                if let status = state.status {
                    Text("\(status.totalRuns) total")
                        .font(.system(size: 11))
                        .foregroundColor(Color.white.opacity(0.34))
                }
            }

            VStack(spacing: 2) {
                ForEach(state.runs) { run in
                    Button(action: { state.selectRun(run) }) {
                        runRow(run)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    private func runRow(_ run: IOSDeliveryRunInfo) -> some View {
        HStack(spacing: 10) {
            phaseBadge(run.phase)

            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(run.scheme ?? run.projectName)
                        .font(.system(size: 12, weight: .medium))
                        .foregroundColor(Color.white.opacity(0.9))
                        .lineLimit(1)

                    if run.uploadTarget != "none" {
                        Image(systemName: "icloud.and.arrow.up")
                            .font(.system(size: 9))
                            .foregroundColor(Color.white.opacity(0.3))
                    }
                }
                Text(formatEpoch(run.createdAt))
                    .font(.system(size: 10))
                    .foregroundColor(Color.white.opacity(0.34))
            }

            Spacer()

            if let duration = runDuration(run) {
                Text(duration)
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundColor(Color.white.opacity(0.3))
            }

            if run.phase == "failed" {
                Image(systemName: "xmark.circle")
                    .font(.system(size: 12))
                    .foregroundColor(BossColor.accent.opacity(0.7))
            }
        }
        .padding(.vertical, 6)
        .padding(.horizontal, 10)
        .background(
            run.runId == state.selectedRun?.runId
                ? Color.white.opacity(0.06)
                : Color.clear
        )
        .cornerRadius(6)
    }

    // MARK: - Run Detail

    private func runDetailCard(_ run: IOSDeliveryRunInfo) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            // Header
            HStack {
                Text("Run Detail")
                    .font(.system(size: 14, weight: .medium))
                    .foregroundColor(Color.white.opacity(0.9))
                Spacer()
                phaseBadge(run.phase)
            }

            // Phase progress
            phaseProgress(run)

            // Metadata grid
            VStack(alignment: .leading, spacing: 6) {
                metadataLine(label: "Run ID", value: String(run.runId.prefix(12)), copyable: run.runId)
                metadataLine(label: "Project", value: run.projectName, copyable: run.projectPath)
                if let scheme = run.scheme {
                    metadataLine(label: "Scheme", value: scheme)
                }
                metadataLine(label: "Config", value: run.configuration)
                metadataLine(label: "Export", value: run.exportMethod)
                if let bundleId = run.bundleIdentifier {
                    metadataLine(label: "Bundle ID", value: bundleId)
                }
                if run.signingMode != "unknown" {
                    metadataLine(label: "Signing", value: run.signingMode)
                }
                if let teamId = run.teamId {
                    metadataLine(label: "Team", value: teamId)
                }
            }

            // Artifact paths
            if run.archivePath != nil || run.ipaPath != nil || run.dsymPath != nil {
                artifactPaths(run)
            }

            // Upload section
            if run.uploadTarget != "none" {
                uploadSection(run)
            }

            // Error detail
            if let error = run.error {
                errorSection(error)
            }

            // Actions
            actionBar(run)

            // Logs
            if !run.buildLog.isEmpty {
                logSection(title: "Build Log", text: run.buildLog)
            }
            if !run.exportLog.isEmpty {
                logSection(title: "Export Log", text: run.exportLog)
            }
            if !run.uploadLog.isEmpty {
                logSection(title: "Upload Log", text: run.uploadLog)
            }
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color.white.opacity(0.035)))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.white.opacity(0.06), lineWidth: 1))
        .task(id: run.runId) {
            await state.refreshSelectedRunEvents()
            // Poll while active
            if !run.isTerminal {
                await pollUntilDone(run.runId)
            }
        }
    }

    // MARK: - Phase Progress

    private func phaseProgress(_ run: IOSDeliveryRunInfo) -> some View {
        let phases = ["pending", "inspecting", "archiving", "exporting", "uploading", "completed"]
        let labels = ["Pending", "Inspect", "Archive", "Export", "Upload", "Done"]
        let currentIndex = phases.firstIndex(of: run.phase) ?? 0
        let failed = run.phase == "failed"
        let cancelled = run.phase == "cancelled"

        return HStack(spacing: 0) {
            ForEach(Array(zip(phases.indices, labels)), id: \.0) { index, label in
                HStack(spacing: 4) {
                    if failed && index == currentIndex {
                        Image(systemName: "xmark.circle.fill")
                            .font(.system(size: 10))
                            .foregroundColor(BossColor.accent)
                    } else if cancelled && index == currentIndex {
                        Image(systemName: "slash.circle.fill")
                            .font(.system(size: 10))
                            .foregroundColor(Color.white.opacity(0.3))
                    } else if index < currentIndex || (run.phase == "completed" && index <= phases.count - 1) {
                        Image(systemName: "checkmark.circle.fill")
                            .font(.system(size: 10))
                            .foregroundColor(Color.green.opacity(0.7))
                    } else if index == currentIndex && !run.isTerminal {
                        ProgressView()
                            .scaleEffect(0.5)
                            .frame(width: 10, height: 10)
                    } else {
                        Circle()
                            .stroke(Color.white.opacity(0.15), lineWidth: 1)
                            .frame(width: 10, height: 10)
                    }

                    Text(label)
                        .font(.system(size: 10, weight: index == currentIndex ? .semibold : .regular))
                        .foregroundColor(
                            index == currentIndex
                                ? Color.white.opacity(0.8)
                                : index < currentIndex
                                    ? Color.white.opacity(0.5)
                                    : Color.white.opacity(0.2)
                        )
                }
                if index < phases.count - 1 {
                    Spacer()
                }
            }
        }
        .padding(.vertical, 8)
        .padding(.horizontal, 4)
    }

    // MARK: - Artifact Paths

    private func artifactPaths(_ run: IOSDeliveryRunInfo) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Artifacts")
                .font(.system(size: 11, weight: .medium))
                .foregroundColor(Color.white.opacity(0.4))

            if let path = run.archivePath {
                copyablePath(label: "Archive", path: path)
            }
            if let path = run.ipaPath {
                copyablePath(label: "IPA", path: path)
            }
            if let path = run.dsymPath {
                copyablePath(label: "dSYM", path: path)
            }
        }
    }

    private func copyablePath(label: String, path: String) -> some View {
        HStack(spacing: 6) {
            Text(label)
                .font(.system(size: 10, weight: .medium))
                .foregroundColor(Color.white.opacity(0.4))
                .frame(width: 50, alignment: .trailing)

            Text((path as NSString).lastPathComponent)
                .font(.system(size: 11, design: .monospaced))
                .foregroundColor(Color.white.opacity(0.6))
                .lineLimit(1)
                .textSelection(.enabled)

            Spacer()

            Button(action: { copyToClipboard(path) }) {
                Image(systemName: "doc.on.doc")
                    .font(.system(size: 10))
                    .foregroundColor(Color.white.opacity(0.35))
            }
            .buttonStyle(.plain)
            .help("Copy full path")
        }
    }

    // MARK: - Upload Section

    private func uploadSection(_ run: IOSDeliveryRunInfo) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("Upload")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundColor(Color.white.opacity(0.4))
                Spacer()
                uploadStatusBadge(run.uploadStatus)
            }

            VStack(alignment: .leading, spacing: 4) {
                metadataLine(label: "Target", value: run.uploadTarget)
                if run.uploadMethod != "none" {
                    metadataLine(label: "Method", value: run.uploadMethod.replacingOccurrences(of: "_", with: " "))
                }
                if let uploadId = run.uploadId {
                    metadataLine(label: "Upload ID", value: uploadId, copyable: uploadId)
                }
            }
        }
    }

    // MARK: - Error Section

    private func errorSection(_ error: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Image(systemName: "exclamationmark.triangle")
                    .font(.system(size: 11))
                    .foregroundColor(BossColor.accent)
                Text("Error")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundColor(BossColor.accent.opacity(0.8))
                Spacer()
                Button(action: { copyToClipboard(error) }) {
                    HStack(spacing: 4) {
                        Image(systemName: "doc.on.doc")
                            .font(.system(size: 10))
                        Text("Copy")
                            .font(.system(size: 10))
                    }
                    .foregroundColor(Color.white.opacity(0.4))
                }
                .buttonStyle(.plain)
            }

            Text(error)
                .font(.system(size: 11, design: .monospaced))
                .foregroundColor(BossColor.accent.opacity(0.7))
                .textSelection(.enabled)
                .padding(8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(
                    RoundedRectangle(cornerRadius: 6)
                        .fill(BossColor.accent.opacity(0.06))
                )
        }
    }

    // MARK: - Action Bar

    private func actionBar(_ run: IOSDeliveryRunInfo) -> some View {
        HStack(spacing: 10) {
            if !run.isTerminal {
                BossPrimaryButton(title: "Cancel") {
                    Task { await state.cancelRun(run.runId) }
                }
            }

            if run.phase == "failed" || run.phase == "cancelled" {
                BossSecondaryButton(title: "Retry", icon: "arrow.counterclockwise") {
                    Task {
                        await state.createAndStartRun(
                            projectPath: run.projectPath,
                            scheme: run.scheme,
                            configuration: run.configuration,
                            exportMethod: run.exportMethod,
                            uploadTarget: run.uploadTarget
                        )
                    }
                }
            }

            // Upload trigger: IPA exists, upload target set, upload not started
            if run.ipaPath != nil && run.uploadTarget != "none" && run.uploadStatus == "not_started" {
                BossSecondaryButton(title: "Upload to TestFlight", icon: "icloud.and.arrow.up") {
                    Task { await state.triggerUpload(run.runId) }
                }
            }

            Spacer()

            if !run.buildLog.isEmpty || !run.exportLog.isEmpty || !run.uploadLog.isEmpty {
                Button(action: { copyAllLogs(run) }) {
                    HStack(spacing: 4) {
                        Image(systemName: "doc.on.doc")
                            .font(.system(size: 10))
                        Text("Copy Logs")
                            .font(.system(size: 10))
                    }
                    .foregroundColor(Color.white.opacity(0.4))
                }
                .buttonStyle(.plain)
            }
        }
    }

    // MARK: - Log Section

    private func logSection(title: String, text: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(title)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundColor(Color.white.opacity(0.4))
                Spacer()
                Button(action: { copyToClipboard(text) }) {
                    Image(systemName: "doc.on.doc")
                        .font(.system(size: 10))
                        .foregroundColor(Color.white.opacity(0.3))
                }
                .buttonStyle(.plain)
            }

            ScrollView([.horizontal, .vertical], showsIndicators: true) {
                Text(text.suffix(4000))
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundColor(Color.white.opacity(0.5))
                    .textSelection(.enabled)
            }
            .frame(maxHeight: 160)
            .background(
                RoundedRectangle(cornerRadius: 6)
                    .fill(Color.white.opacity(0.02))
            )
        }
    }

    // MARK: - Empty State

    private var emptyState: some View {
        VStack(spacing: 8) {
            Image(systemName: "shippingbox")
                .font(.system(size: 24))
                .foregroundColor(Color.white.opacity(0.2))
            Text("No delivery runs yet")
                .font(.system(size: 14, weight: .medium))
                .foregroundColor(Color.white.opacity(0.5))
            Text("Use New Run above, or ask Boss in chat to build and upload your app.")
                .font(.system(size: 12))
                .foregroundColor(Color.white.opacity(0.3))
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 40)
    }

    // MARK: - Helpers

    private func metadataLine(label: String, value: String, copyable: String? = nil) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Text(label)
                .font(.system(size: 11, weight: .medium))
                .foregroundColor(Color.white.opacity(0.4))
                .frame(width: 80, alignment: .trailing)
            Text(value)
                .font(.system(size: 12))
                .foregroundColor(Color.white.opacity(0.7))
                .textSelection(.enabled)
                .lineLimit(1)
            if let copyable {
                Button(action: { copyToClipboard(copyable) }) {
                    Image(systemName: "doc.on.doc")
                        .font(.system(size: 9))
                        .foregroundColor(Color.white.opacity(0.25))
                }
                .buttonStyle(.plain)
            }
        }
    }

    private func phaseBadge(_ phase: String) -> some View {
        let color: Color = {
            switch phase {
            case "completed": return .green
            case "failed": return BossColor.accent
            case "cancelled": return .gray
            case "pending": return .yellow
            case "inspecting", "archiving", "exporting", "uploading":
                return .blue
            default: return .white.opacity(0.5)
            }
        }()
        return StatusPill(text: phase, color: color)
    }

    private func uploadStatusBadge(_ status: String) -> some View {
        let color: Color = {
            switch status {
            case "ready": return .green
            case "failed": return BossColor.accent
            case "uploading", "processing": return .blue
            case "credential_check": return .yellow
            case "not_started": return Color.white.opacity(0.3)
            default: return Color.white.opacity(0.4)
            }
        }()
        return StatusPill(text: status.replacingOccurrences(of: "_", with: " "), color: color)
    }

    private func readinessPill(_ label: String, ready: Bool) -> some View {
        HStack(spacing: 4) {
            Circle()
                .fill(ready ? Color.green.opacity(0.7) : Color.white.opacity(0.2))
                .frame(width: 6, height: 6)
            Text(label)
                .font(.system(size: 10, weight: .medium))
                .foregroundColor(ready ? Color.green.opacity(0.7) : Color.white.opacity(0.34))
        }
    }

    private func credentialIcon(_ status: String) -> some View {
        let (icon, color): (String, Color) = {
            switch status {
            case "available": return ("checkmark.circle.fill", .green.opacity(0.7))
            case "missing", "not_configured": return ("circle", Color.white.opacity(0.2))
            case "invalid", "insecure_permissions": return ("exclamationmark.triangle.fill", BossColor.accent)
            default: return ("questionmark.circle", Color.white.opacity(0.3))
            }
        }()
        return Image(systemName: icon)
            .font(.system(size: 11))
            .foregroundColor(color)
    }

    private func credentialColor(_ status: String) -> Color {
        switch status {
        case "available": return .green
        case "missing", "not_configured": return .gray
        case "invalid", "insecure_permissions", "unreadable": return BossColor.accent
        default: return Color.white.opacity(0.5)
        }
    }

    private func formatEpoch(_ epoch: Double) -> String {
        let date = Date(timeIntervalSince1970: epoch)
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .abbreviated
        return formatter.localizedString(for: date, relativeTo: Date())
    }

    private func runDuration(_ run: IOSDeliveryRunInfo) -> String? {
        guard let fin = run.finishedAt else {
            if !run.isTerminal {
                let elapsed = Date().timeIntervalSince1970 - run.createdAt
                return formatDuration(elapsed)
            }
            return nil
        }
        return formatDuration(fin - run.createdAt)
    }

    private func formatDuration(_ seconds: Double) -> String {
        let s = Int(seconds)
        if s < 60 { return "\(s)s" }
        if s < 3600 { return "\(s / 60)m \(s % 60)s" }
        return "\(s / 3600)h \(s % 3600 / 60)m"
    }

    private func copyToClipboard(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }

    private func copyAllLogs(_ run: IOSDeliveryRunInfo) {
        var combined = ""
        if !run.buildLog.isEmpty {
            combined += "=== Build Log ===\n\(run.buildLog)\n\n"
        }
        if !run.exportLog.isEmpty {
            combined += "=== Export Log ===\n\(run.exportLog)\n\n"
        }
        if !run.uploadLog.isEmpty {
            combined += "=== Upload Log ===\n\(run.uploadLog)\n\n"
        }
        copyToClipboard(combined)
    }

    private func pollUntilDone(_ runId: String) async {
        while !Task.isCancelled {
            try? await Task.sleep(for: .seconds(3))
            if Task.isCancelled { break }
            await state.pollActiveRun()
            if state.selectedRun?.isTerminal == true || state.selectedRun?.runId != runId {
                break
            }
        }
    }
}
