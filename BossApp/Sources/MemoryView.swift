import SwiftUI

struct MemoryView: View {
    @EnvironmentObject var vm: ChatViewModel
    @State private var expandedProjectIDs: Set<String> = []
    @State private var editingCandidateIDs: Set<Int> = []
    @State private var candidateDraftLabels: [Int: String] = [:]
    @State private var candidateDraftTexts: [Int: String] = [:]
    @State private var candidateDraftEvidence: [Int: String] = [:]
    @State private var searchText: String = ""

    private var isSearching: Bool { !searchText.trimmingCharacters(in: .whitespaces).isEmpty }

    private func matchesSearch(_ record: MemoryRecord) -> Bool {
        let q = searchText.lowercased()
        return record.label.lowercased().contains(q)
            || record.text.lowercased().contains(q)
            || record.category.lowercased().contains(q)
    }

    private func matchesCandidateSearch(_ candidate: MemoryCandidateInfo) -> Bool {
        let q = searchText.lowercased()
        return candidate.label.lowercased().contains(q)
            || candidate.text.lowercased().contains(q)
    }

    var body: some View {
        ScrollView(.vertical, showsIndicators: false) {
            VStack(alignment: .leading, spacing: 0) {
                header
                    .padding(.top, 80)
                    .padding(.bottom, 28)

                SearchBar(text: $searchText, placeholder: "Search memories…")
                    .padding(.bottom, 16)

                if let message = vm.memoryState.memoryRefreshError {
                    InlineStatusBanner(message: message)
                        .padding(.bottom, 20)
                }

                if let overview = vm.memoryState.memoryOverview {
                    if isSearching {
                        let allRecords = overview.userProfile + overview.preferences + overview.recentMemories + overview.conversationSummaries
                        let matchedRecords = allRecords.filter { matchesSearch($0) }
                        let matchedCandidates = overview.pendingCandidates.filter { matchesCandidateSearch($0) }

                        if matchedRecords.isEmpty && matchedCandidates.isEmpty {
                            Text("No results for \"\(searchText)\"")
                                .font(.system(size: 13))
                                .foregroundColor(Color.white.opacity(0.34))
                                .padding(.top, 8)
                        } else {
                            LazyVStack(alignment: .leading, spacing: 0) {
                                ForEach(Array(matchedCandidates.enumerated()), id: \.element.id) { index, candidate in
                                    pendingCandidateRow(candidate)
                                    if index < matchedCandidates.count - 1 || !matchedRecords.isEmpty {
                                        Rectangle().fill(Color.white.opacity(0.05)).frame(height: 1)
                                    }
                                }
                                ForEach(Array(matchedRecords.enumerated()), id: \.element.id) { index, item in
                                    memoryRow(item)
                                    if index < matchedRecords.count - 1 {
                                        Rectangle().fill(Color.white.opacity(0.05)).frame(height: 1)
                                    }
                                }
                            }
                        }
                    } else {
                    VStack(alignment: .leading, spacing: 24) {
                        scanStatusCard(overview.scanStatus)

                        governanceCard(overview.governance)

                        if !overview.pendingCandidates.isEmpty {
                            pendingCandidatesSection(overview.pendingCandidates)
                        }

                        if let currentTurn = overview.currentTurnMemory {
                            currentTurnSection(currentTurn)
                        }

                        if !overview.userProfile.isEmpty {
                            memorySection(
                                title: "User Profile",
                                subtitle: "Durable facts Boss keeps about you",
                                items: overview.userProfile
                            )
                        }

                        if !overview.preferences.isEmpty {
                            memorySection(
                                title: "Preferences",
                                subtitle: "Stable choices and defaults",
                                items: overview.preferences
                            )
                        }

                        if !overview.recentMemories.isEmpty {
                            memorySection(
                                title: "Recent Memories",
                                subtitle: "Most recently confirmed or used memories",
                                items: overview.recentMemories
                            )
                        }

                        if !overview.conversationSummaries.isEmpty {
                            memorySection(
                                title: "Conversation Summaries",
                                subtitle: "Condensed session context",
                                items: overview.conversationSummaries
                            )
                        }

                        if !orderedProjectSummaries(overview).isEmpty {
                            projectSummariesSection(orderedProjectSummaries(overview))
                        }
                    }
                    }
                } else {
                    emptyState
                }
            }
            .frame(maxWidth: 680, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .center)
            .padding(.bottom, 32)
        }
        .task {
            await vm.memoryState.refreshOverview(sessionId: vm.sessionId, message: nil)
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Memory")
                .font(.system(size: 18, weight: .semibold))
                .foregroundColor(BossColor.textPrimary)

            Text("Inspect what Boss knows, what was injected, and what was scanned")
                .font(.system(size: 13))
                .foregroundColor(Color.white.opacity(0.38))
        }
    }

    private var emptyState: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Loading memory")
                .font(.system(size: 14, weight: .medium))
                .foregroundColor(Color.white.opacity(0.82))

            Text("Boss is fetching durable memory, summaries, and project context.")
                .font(.system(size: 13))
                .foregroundColor(Color.white.opacity(0.34))
        }
    }

    private func scanStatusCard(_ scanStatus: ScanStatusInfo) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Scan Status")
                        .font(.system(size: 14, weight: .medium))
                        .foregroundColor(Color.white.opacity(0.9))

                    Text(scanStatus.lastScanAt.map { "Last scan \(relativeDate($0))" } ?? "No project scan recorded yet")
                        .font(.system(size: 12))
                        .foregroundColor(Color.white.opacity(0.34))
                }

                Spacer()

                Button(action: { vm.scanSystem() }) {
                    Text("Rescan")
                        .font(.system(size: 12))
                        .foregroundColor(Color.white.opacity(0.64))
                }
                .buttonStyle(.plain)
            }

            HStack(spacing: 24) {
                scanMetric("Projects", value: scanStatus.projectsIndexed)
                scanMetric("Files", value: scanStatus.filesIndexed)
                scanMetric("Summaries", value: scanStatus.projectNotes)
                scanMetric("Chunks", value: scanStatus.fileChunks)
            }
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color.white.opacity(0.035)))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.white.opacity(0.06), lineWidth: 1))
    }

    private func scanMetric(_ label: String, value: Int) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("\(value)")
                .font(.system(size: 15, weight: .medium))
                .foregroundColor(Color.white.opacity(0.88))
            Text(label)
                .font(.system(size: 11))
                .foregroundColor(Color.white.opacity(0.32))
        }
    }

    private func currentTurnSection(_ injection: MemoryInjectionInfo) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            sectionTitle("Current Turn", subtitle: "Why memory matched the active or latest turn")
                .padding(.bottom, 10)

            VStack(alignment: .leading, spacing: 10) {
                Text(injection.message)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundColor(Color.white.opacity(0.82))

                if let projectPath = injection.projectPath {
                    Text("Project scope: \(projectPath)")
                        .font(.system(size: 12))
                        .foregroundColor(Color.white.opacity(0.36))
                }

                if !injection.reasons.isEmpty {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(Array(injection.reasons.enumerated()), id: \.element.id) { index, reason in
                            memoryReasonRow(reason)

                            if index < injection.reasons.count - 1 {
                                Rectangle()
                                    .fill(Color.white.opacity(0.05))
                                    .frame(height: 1)
                            }
                        }
                    }
                } else {
                    Text("No persisted memories were relevant for this turn.")
                        .font(.system(size: 12))
                        .foregroundColor(Color.white.opacity(0.34))
                }
            }
        }
    }

    private func memoryReasonRow(_ reason: MemoryInjectionReason) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 8) {
                        Text(reason.key)
                            .font(.system(size: 13, weight: .medium))
                            .foregroundColor(Color.white.opacity(0.88))

                        if reason.reviewState == "pending" {
                            statusPill("Pending review")
                        }
                        if reason.pinned {
                            statusPill("Pinned")
                        }
                    }
                }

                Spacer()

                if reason.deletable && reason.sourceTable != "memory_candidates" {
                    BossTertiaryButton(title: "Forget") { vm.memoryState.forgetMemory(sourceTable: reason.sourceTable, itemId: reason.memoryId) }
                }
            }

            Text(reason.text)
                .font(.system(size: 12))
                .foregroundColor(Color.white.opacity(0.58))

            Text(reason.why)
                .font(.system(size: 11))
                .foregroundColor(Color.white.opacity(0.32))
        }
        .padding(.vertical, 12)
    }

    private func governanceCard(_ governance: MemoryGovernanceInfo) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Memory Governance")
                        .font(.system(size: 14, weight: .medium))
                        .foregroundColor(Color.white.opacity(0.9))

                    Text(governance.autoApproveEnabled
                         ? String(format: "Auto-approve enabled at %.2f confidence", governance.autoApproveMinConfidence)
                         : "Cross-session memory requires review before it becomes durable")
                        .font(.system(size: 12))
                        .foregroundColor(Color.white.opacity(0.34))
                }

                Spacer()
            }

            HStack(spacing: 24) {
                scanMetric("Pending", value: governance.pendingCandidates)
                scanMetric("Pinned", value: governance.pinnedMemories)
                scanMetric("Approved", value: vm.memoryState.memoryOverview?.recentMemories.count ?? 0)
            }
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color.white.opacity(0.035)))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.white.opacity(0.06), lineWidth: 1))
    }

    private func pendingCandidatesSection(_ candidates: [MemoryCandidateInfo]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            sectionTitle("Pending Review", subtitle: "Session-local memories waiting for durable approval")
                .padding(.bottom, 10)

            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(candidates.enumerated()), id: \.element.id) { index, candidate in
                    pendingCandidateRow(candidate)

                    if index < candidates.count - 1 {
                        Rectangle()
                            .fill(Color.white.opacity(0.05))
                            .frame(height: 1)
                    }
                }
            }
        }
    }

    private func pendingCandidateRow(_ candidate: MemoryCandidateInfo) -> some View {
        let isEditing = editingCandidateIDs.contains(candidate.candidateId)
        let labelBinding = Binding<String>(
            get: { candidateDraftLabels[candidate.candidateId] ?? candidate.label },
            set: { candidateDraftLabels[candidate.candidateId] = $0 }
        )
        let textBinding = Binding<String>(
            get: { candidateDraftTexts[candidate.candidateId] ?? candidate.text },
            set: { candidateDraftTexts[candidate.candidateId] = $0 }
        )
        let evidenceBinding = Binding<String>(
            get: { candidateDraftEvidence[candidate.candidateId] ?? candidate.evidence },
            set: { candidateDraftEvidence[candidate.candidateId] = $0 }
        )

        return VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(candidate.label)
                    .font(.system(size: 14, weight: .medium))
                    .foregroundColor(Color.white.opacity(0.9))

                statusPill(candidate.proposedAction == "update" ? "Update" : "New")

                Spacer()

                if let updatedAt = candidate.updatedAt {
                    Text("Updated \(relativeDate(updatedAt))")
                        .font(.system(size: 11))
                        .foregroundColor(Color.white.opacity(0.3))
                }
            }

            if let existingText = candidate.existingText {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Current approved memory")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundColor(Color.white.opacity(0.28))
                        .tracking(1.0)

                    Text(existingText)
                        .font(.system(size: 12))
                        .foregroundColor(Color.white.opacity(0.42))
                }
            }

            if isEditing {
                VStack(alignment: .leading, spacing: 8) {
                    TextField("Label", text: labelBinding)
                        .textFieldStyle(.plain)
                        .font(.system(size: 13))
                        .foregroundColor(Color.white.opacity(0.84))
                        .padding(.horizontal, 10)
                        .padding(.vertical, 8)
                        .background(
                            RoundedRectangle(cornerRadius: 8)
                                .fill(Color.white.opacity(0.04))
                        )

                    TextEditor(text: textBinding)
                        .font(.system(size: 13))
                        .foregroundColor(Color.white.opacity(0.8))
                        .scrollContentBackground(.hidden)
                        .frame(minHeight: 72)
                        .padding(6)
                        .background(
                            RoundedRectangle(cornerRadius: 8)
                                .fill(Color.white.opacity(0.04))
                        )

                    TextEditor(text: evidenceBinding)
                        .font(.system(size: 12))
                        .foregroundColor(Color.white.opacity(0.68))
                        .scrollContentBackground(.hidden)
                        .frame(minHeight: 54)
                        .padding(6)
                        .background(
                            RoundedRectangle(cornerRadius: 8)
                                .fill(Color.white.opacity(0.03))
                        )
                }
            } else {
                Text(candidate.text)
                    .font(.system(size: 13))
                    .foregroundColor(Color.white.opacity(0.58))

                if !candidate.evidence.isEmpty {
                    Text("Source: \(candidate.evidence)")
                        .font(.system(size: 12))
                        .foregroundColor(Color.white.opacity(0.34))
                }
            }

            HStack(spacing: 10) {
                Text(candidate.memoryKind.replacingOccurrences(of: "_", with: " ").capitalized)
                Text(String(format: "Confidence %.2f", candidate.confidence))
                if let sessionId = candidate.sessionId {
                    Text("Session \(String(sessionId.prefix(8)))")
                }
                if let projectPath = candidate.projectPath {
                    Text(projectPath)
                }
            }
            .font(.system(size: 11))
            .foregroundColor(Color.white.opacity(0.3))

            HStack(spacing: 12) {
                if isEditing {
                    Button(action: {
                        vm.memoryState.saveMemoryCandidate(
                            candidateId: candidate.candidateId,
                            label: labelBinding.wrappedValue,
                            text: textBinding.wrappedValue,
                            evidence: evidenceBinding.wrappedValue
                        )
                    }) {
                        Text("Save")
                            .font(.system(size: 12))
                            .foregroundColor(Color.white.opacity(0.68))
                    }
                    .buttonStyle(.plain)

                    Button(action: {
                        vm.memoryState.approveMemoryCandidate(
                            candidateId: candidate.candidateId,
                            label: labelBinding.wrappedValue,
                            text: textBinding.wrappedValue,
                            evidence: evidenceBinding.wrappedValue
                        )
                        clearCandidateDraft(candidate.candidateId)
                    }) {
                        Text("Approve")
                            .font(.system(size: 12, weight: .medium))
                            .foregroundColor(Color.white.opacity(0.88))
                    }
                    .buttonStyle(.plain)

                    Button(action: {
                        clearCandidateDraft(candidate.candidateId)
                    }) {
                        Text("Cancel")
                            .font(.system(size: 12))
                            .foregroundColor(Color.white.opacity(0.5))
                    }
                    .buttonStyle(.plain)
                } else {
                    Button(action: {
                        vm.memoryState.approveMemoryCandidate(
                            candidateId: candidate.candidateId,
                            label: candidate.label,
                            text: candidate.text,
                            evidence: candidate.evidence
                        )
                    }) {
                        Text("Approve")
                            .font(.system(size: 12, weight: .medium))
                            .foregroundColor(Color.white.opacity(0.88))
                    }
                    .buttonStyle(.plain)

                    Button(action: {
                        beginEditing(candidate)
                    }) {
                        Text("Edit")
                            .font(.system(size: 12))
                            .foregroundColor(Color.white.opacity(0.64))
                    }
                    .buttonStyle(.plain)

                    Button(action: { vm.memoryState.rejectMemoryCandidate(candidateId: candidate.candidateId) }) {
                        Text("Reject")
                            .font(.system(size: 12))
                            .foregroundColor(Color.white.opacity(0.55))
                    }
                    .buttonStyle(.plain)

                    Button(action: { vm.memoryState.expireMemoryCandidate(candidateId: candidate.candidateId) }) {
                        Text("Expire")
                            .font(.system(size: 12))
                            .foregroundColor(Color.white.opacity(0.55))
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .padding(.vertical, 12)
    }

    private func memorySection(title: String, subtitle: String, items: [MemoryRecord]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            sectionTitle(title, subtitle: subtitle)
                .padding(.bottom, 10)

            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                    memoryRow(item)

                    if index < items.count - 1 {
                        Rectangle()
                            .fill(Color.white.opacity(0.05))
                            .frame(height: 1)
                    }
                }
            }
        }
    }

    private func memoryRow(_ item: MemoryRecord) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline) {
                HStack(spacing: 8) {
                    Text(item.label)
                        .font(.system(size: 14, weight: .medium))
                        .foregroundColor(Color.white.opacity(0.9))

                    if item.pinned {
                        statusPill("Pinned")
                    }
                }

                Spacer()

                if item.sourceTable == "durable_memories" {
                    BossTertiaryButton(title: item.pinned ? "Unpin" : "Pin") {
                        vm.memoryState.setMemoryPinned(itemId: item.memoryId, pinned: !item.pinned)
                    }
                }

                if item.deletable {
                    BossTertiaryButton(title: "Forget") {
                        vm.memoryState.forgetMemory(sourceTable: item.sourceTable, itemId: item.memoryId)
                    }
                }
            }

            Text(item.text)
                .font(.system(size: 13))
                .foregroundColor(Color.white.opacity(0.58))

            HStack(spacing: 10) {
                Text(item.memoryKind.replacingOccurrences(of: "_", with: " ").capitalized)
                if let projectPath = item.projectPath {
                    Text(projectPath)
                }
                if let updatedAt = item.updatedAt {
                    Text("Updated \(relativeDate(updatedAt))")
                }
            }
            .font(.system(size: 11))
            .foregroundColor(Color.white.opacity(0.3))
        }
        .padding(.vertical, 12)
    }

    private func projectSummariesSection(_ projects: [ProjectSummaryInfo]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            sectionTitle("Project Summaries", subtitle: "Scanned repo context and entry points")
                .padding(.bottom, 10)

            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(projects.enumerated()), id: \.element.id) { index, project in
                    projectSummaryRow(project)

                    if index < projects.count - 1 {
                        Rectangle()
                            .fill(Color.white.opacity(0.05))
                            .frame(height: 1)
                    }
                }
            }
        }
    }

    private func projectSummaryRow(_ project: ProjectSummaryInfo) -> some View {
        let isExpanded = expandedProjectIDs.contains(project.id) || vm.selectedProjectPath == project.projectPath

        return VStack(alignment: .leading, spacing: 8) {
            Button {
                if expandedProjectIDs.contains(project.id) {
                    expandedProjectIDs.remove(project.id)
                } else {
                    expandedProjectIDs.insert(project.id)
                }
                vm.selectedProjectPath = project.projectPath
            } label: {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(project.projectName)
                            .font(.system(size: 14, weight: .medium))
                            .foregroundColor(Color.white.opacity(0.9))

                        Text("\(project.projectType) · \(project.projectPath)")
                            .font(.system(size: 12))
                            .foregroundColor(Color.white.opacity(0.34))
                            .lineLimit(1)
                    }

                    Spacer()

                    if let branch = project.gitBranch {
                        Text(branch)
                            .font(.system(size: 11))
                            .foregroundColor(Color.white.opacity(0.34))
                    }

                    Image(systemName: isExpanded ? "chevron.down" : "chevron.right")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundColor(Color.white.opacity(0.4))
                        .accessibilityLabel(isExpanded ? "Collapse" : "Expand")
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if isExpanded {
                VStack(alignment: .leading, spacing: 10) {
                    Text(project.summaryText)
                        .font(.system(size: 13))
                        .foregroundColor(Color.white.opacity(0.6))
                        .textSelection(.enabled)

                    metadataList(title: "Stack", values: stringList(from: project.metadata?["stack"]?.value))
                    metadataList(title: "Entry Points", values: stringList(from: project.metadata?["entry_points"]?.value))
                    metadataList(title: "Useful Commands", values: stringList(from: project.metadata?["useful_commands"]?.value))
                    metadataList(title: "Notable Modules", values: stringList(from: project.metadata?["notable_modules"]?.value))

                    HStack {
                        if let lastScanned = project.lastScanned {
                            Text("Scanned \(relativeDate(lastScanned))")
                                .font(.system(size: 11))
                                .foregroundColor(Color.white.opacity(0.3))
                        }

                        Spacer()

                        if project.deletable {
                            BossTertiaryButton(title: "Forget") {
                                vm.memoryState.forgetMemory(sourceTable: project.sourceTable, itemId: project.memoryId)
                            }
                        }
                    }
                }
                .padding(.top, 4)
            }
        }
        .padding(.vertical, 12)
    }

    private func metadataList(title: String, values: [String]) -> some View {
        Group {
            if !values.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text(title.uppercased())
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundColor(Color.white.opacity(0.28))
                        .tracking(1.1)

                    ForEach(values.prefix(6), id: \.self) { value in
                        Text(value)
                            .font(.system(size: 12))
                            .foregroundColor(Color.white.opacity(0.5))
                    }
                }
            }
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

    private func statusPill(_ label: String) -> some View {
        StatusPill(text: label, color: Color.white.opacity(0.72))
    }

    private func orderedProjectSummaries(_ overview: MemoryOverview) -> [ProjectSummaryInfo] {
        guard let selected = vm.selectedProjectPath else {
            return overview.projectSummaries
        }
        return overview.projectSummaries.sorted { lhs, rhs in
            if lhs.projectPath == selected { return true }
            if rhs.projectPath == selected { return false }
            return lhs.projectName.localizedCaseInsensitiveCompare(rhs.projectName) == .orderedAscending
        }
    }

    private func stringList(from value: Any?) -> [String] {
        if let strings = value as? [String] {
            return strings
        }
        if let codables = value as? [AnyCodable] {
            return codables.compactMap { $0.value as? String }
        }
        return []
    }

    private func beginEditing(_ candidate: MemoryCandidateInfo) {
        editingCandidateIDs.insert(candidate.candidateId)
        candidateDraftLabels[candidate.candidateId] = candidate.label
        candidateDraftTexts[candidate.candidateId] = candidate.text
        candidateDraftEvidence[candidate.candidateId] = candidate.evidence
    }

    private func clearCandidateDraft(_ candidateId: Int) {
        editingCandidateIDs.remove(candidateId)
        candidateDraftLabels.removeValue(forKey: candidateId)
        candidateDraftTexts.removeValue(forKey: candidateId)
        candidateDraftEvidence.removeValue(forKey: candidateId)
    }

    private func relativeDate(_ date: Date) -> String {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .short
        return formatter.localizedString(for: date, relativeTo: Date())
    }
}