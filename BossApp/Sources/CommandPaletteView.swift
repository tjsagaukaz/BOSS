import SwiftUI

// MARK: - Command Palette (⌘K)

struct CommandPaletteView: View {
    @EnvironmentObject var vm: ChatViewModel
    @Binding var isPresented: Bool
    @State private var query: String = ""
    @State private var selectedIndex: Int = 0
    @FocusState private var isFocused: Bool

    private var filteredCommands: [PaletteCommand] {
        let all = PaletteCommand.all(vm: vm)
        guard !query.trimmingCharacters(in: .whitespaces).isEmpty else { return all }
        let q = query.lowercased()
        return all.filter { cmd in
            cmd.label.lowercased().contains(q) ||
            cmd.category.rawValue.lowercased().contains(q) ||
            (cmd.keywords?.contains(where: { $0.contains(q) }) ?? false)
        }
    }

    var body: some View {
        ZStack {
            // Scrim
            Color.black.opacity(0.55)
                .ignoresSafeArea()
                .onTapGesture { dismiss() }

            VStack(spacing: 0) {
                // Search input
                HStack(spacing: 10) {
                    Image(systemName: "magnifyingglass")
                        .font(.system(size: 14, weight: .medium))
                        .foregroundColor(Color.white.opacity(0.4))
                    TextField("Type a command…", text: $query)
                        .textFieldStyle(.plain)
                        .font(.system(size: 15))
                        .foregroundColor(Color.white.opacity(0.9))
                        .focused($isFocused)
                        .onKeyPress(.downArrow) {
                            moveSelection(1)
                            return .handled
                        }
                        .onKeyPress(.upArrow) {
                            moveSelection(-1)
                            return .handled
                        }
                        .onKeyPress(.return) {
                            executeSelected()
                            return .handled
                        }
                        .onKeyPress(.escape) {
                            dismiss()
                            return .handled
                        }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 14)

                Rectangle()
                    .fill(Color.white.opacity(0.06))
                    .frame(height: 1)

                // Results
                ScrollViewReader { proxy in
                    ScrollView(.vertical, showsIndicators: false) {
                        LazyVStack(alignment: .leading, spacing: 0) {
                            let grouped = groupedCommands
                            ForEach(grouped, id: \.category) { group in
                                Text(group.category.rawValue.uppercased())
                                    .font(.system(size: 10, weight: .medium))
                                    .foregroundColor(Color.white.opacity(0.3))
                                    .tracking(1.4)
                                    .padding(.horizontal, 16)
                                    .padding(.top, 10)
                                    .padding(.bottom, 4)

                                ForEach(group.commands) { cmd in
                                    let idx = filteredCommands.firstIndex(where: { $0.id == cmd.id }) ?? 0
                                    commandRow(cmd, isSelected: idx == selectedIndex)
                                        .id(cmd.id)
                                        .onTapGesture {
                                            selectedIndex = idx
                                            executeSelected()
                                        }
                                }
                            }
                        }
                        .padding(.vertical, 6)
                    }
                    .frame(maxHeight: 320)
                    .onChange(of: selectedIndex) { _, newIdx in
                        if let cmd = filteredCommands[safe: newIdx] {
                            proxy.scrollTo(cmd.id, anchor: .center)
                        }
                    }
                }
            }
            .frame(width: 480)
            .background(
                RoundedRectangle(cornerRadius: 14)
                    .fill(Color(hex: "#141414"))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 14)
                    .stroke(Color.white.opacity(0.08), lineWidth: 1)
            )
            .shadow(color: .black.opacity(0.6), radius: 40, y: 10)
            .padding(.top, 120)
            .frame(maxHeight: .infinity, alignment: .top)
        }
        .onAppear {
            isFocused = true
            selectedIndex = 0
        }
        .onChange(of: query) { _, _ in
            selectedIndex = 0
        }
    }

    // MARK: - Row

    private func commandRow(_ cmd: PaletteCommand, isSelected: Bool) -> some View {
        HStack(spacing: 10) {
            Image(systemName: cmd.icon)
                .font(.system(size: 12, weight: .medium))
                .foregroundColor(isSelected ? .white : Color.white.opacity(0.45))
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 1) {
                Text(cmd.label)
                    .font(.system(size: 13, weight: isSelected ? .semibold : .medium))
                    .foregroundColor(isSelected ? .white : Color.white.opacity(0.72))
                if let detail = cmd.detail {
                    Text(detail)
                        .font(.system(size: 11))
                        .foregroundColor(Color.white.opacity(0.35))
                }
            }
            Spacer()
            if let shortcut = cmd.shortcut {
                Text(shortcut)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundColor(Color.white.opacity(0.3))
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 7)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(isSelected ? Color.white.opacity(0.08) : .clear)
                .padding(.horizontal, 6)
        )
        .contentShape(Rectangle())
    }

    // MARK: - Grouped

    private struct CommandGroup: Identifiable {
        let category: PaletteCommand.Category
        let commands: [PaletteCommand]
        var id: String { category.rawValue }
    }

    private var groupedCommands: [CommandGroup] {
        let ordered: [PaletteCommand.Category] = [.navigation, .action, .mode, .setting]
        let cmds = filteredCommands
        return ordered.compactMap { cat in
            let items = cmds.filter { $0.category == cat }
            return items.isEmpty ? nil : CommandGroup(category: cat, commands: items)
        }
    }

    // MARK: - Helpers

    private func moveSelection(_ delta: Int) {
        let count = filteredCommands.count
        guard count > 0 else { return }
        selectedIndex = (selectedIndex + delta + count) % count
    }

    private func executeSelected() {
        guard let cmd = filteredCommands[safe: selectedIndex] else { return }
        dismiss()
        cmd.action()
    }

    private func dismiss() {
        withAnimation(.easeOut(duration: 0.15)) {
            isPresented = false
        }
        query = ""
    }
}

// MARK: - Command Definition

struct PaletteCommand: Identifiable {
    let id: String
    let icon: String
    let label: String
    let detail: String?
    let shortcut: String?
    let category: Category
    let keywords: [String]?
    let action: () -> Void

    enum Category: String {
        case navigation = "Navigation"
        case action = "Actions"
        case mode = "Mode"
        case setting = "Settings"
    }

    @MainActor
    static func all(vm: ChatViewModel) -> [PaletteCommand] {
        var cmds: [PaletteCommand] = []

        // Navigation
        cmds.append(PaletteCommand(
            id: "nav-chat", icon: "bubble.left.and.bubble.right", label: "Go to Chat",
            detail: nil, shortcut: "⌘1", category: .navigation,
            keywords: ["chat", "conversation", "messages"]) { vm.showChat() })
        cmds.append(PaletteCommand(
            id: "nav-memory", icon: "brain", label: "Go to Memory",
            detail: nil, shortcut: "⌘2", category: .navigation,
            keywords: ["memory", "knowledge", "facts"]) { vm.showMemory() })
        cmds.append(PaletteCommand(
            id: "nav-review", icon: "eye", label: "Go to Review",
            detail: nil, shortcut: "⌘3", category: .navigation,
            keywords: ["review", "code review", "diff"]) { vm.showReview() })
        cmds.append(PaletteCommand(
            id: "nav-deploy", icon: "shippingbox", label: "Go to Deploy",
            detail: nil, shortcut: "⌘4", category: .navigation,
            keywords: ["deploy", "jobs", "preview", "ios"]) { vm.showDeploy() })
        cmds.append(PaletteCommand(
            id: "nav-settings", icon: "gearshape", label: "Go to Settings",
            detail: nil, shortcut: "⌘5", category: .navigation,
            keywords: ["settings", "preferences", "config"]) { vm.selectedSurface = .settings })
        cmds.append(PaletteCommand(
            id: "nav-diagnostics", icon: "stethoscope", label: "Go to Diagnostics",
            detail: nil, shortcut: nil, category: .navigation,
            keywords: ["diagnostics", "health", "status"]) { vm.showDiagnostics() })
        cmds.append(PaletteCommand(
            id: "nav-permissions", icon: "lock.shield", label: "Go to Permissions",
            detail: nil, shortcut: nil, category: .navigation,
            keywords: ["permissions", "approval", "security"]) { vm.showPermissions() })
        cmds.append(PaletteCommand(
            id: "nav-workers", icon: "server.rack", label: "Go to Workers",
            detail: nil, shortcut: nil, category: .navigation,
            keywords: ["workers", "parallel", "agents"]) { vm.showWorkers() })

        // Actions
        cmds.append(PaletteCommand(
            id: "act-new-chat", icon: "plus", label: "New Chat",
            detail: "Start a fresh conversation", shortcut: nil, category: .action,
            keywords: ["new", "fresh", "reset", "clear"]) { vm.newSession() })
        cmds.append(PaletteCommand(
            id: "act-scan", icon: "arrow.clockwise", label: "Scan System",
            detail: "Discover projects and environment", shortcut: nil, category: .action,
            keywords: ["scan", "discover", "refresh", "system"]) { vm.scanSystem() })
        cmds.append(PaletteCommand(
            id: "act-export-md", icon: "square.and.arrow.up", label: "Export as Markdown",
            detail: nil, shortcut: nil, category: .action,
            keywords: ["export", "markdown", "save"]) { vm.exportConversation(asMarkdown: true) })
        cmds.append(PaletteCommand(
            id: "act-export-txt", icon: "doc.plaintext", label: "Export as Text",
            detail: nil, shortcut: nil, category: .action,
            keywords: ["export", "text", "save"]) { vm.exportConversation(asMarkdown: false) })

        // Mode
        for mode in WorkMode.allCases {
            let isActive = vm.selectedMode == mode
            cmds.append(PaletteCommand(
                id: "mode-\(mode.rawValue)", icon: isActive ? "checkmark.circle.fill" : "circle",
                label: "Mode: \(mode.label)",
                detail: mode.detail, shortcut: nil, category: .mode,
                keywords: ["mode", mode.rawValue, mode.label.lowercased()]) { vm.selectMode(mode) })
        }

        // Settings toggles
        cmds.append(PaletteCommand(
            id: "set-autoscroll", icon: "arrow.down.to.line",
            label: "Toggle Auto-scroll",
            detail: autoScrollValue ? "Currently on" : "Currently off",
            shortcut: nil, category: .setting,
            keywords: ["autoscroll", "scroll", "follow"]) {
                let current = UserDefaults.standard.bool(forKey: "bossAutoScroll")
                UserDefaults.standard.set(!current, forKey: "bossAutoScroll")
        })
        cmds.append(PaletteCommand(
            id: "set-thinking", icon: "thought.bubble",
            label: "Toggle Show Thinking",
            detail: showThinkingValue ? "Currently on" : "Currently off",
            shortcut: nil, category: .setting,
            keywords: ["thinking", "reasoning", "thought"]) {
                let current = UserDefaults.standard.bool(forKey: "bossShowThinking")
                UserDefaults.standard.set(!current, forKey: "bossShowThinking")
        })

        return cmds
    }

    private static var autoScrollValue: Bool {
        UserDefaults.standard.object(forKey: "bossAutoScroll") as? Bool ?? true
    }

    private static var showThinkingValue: Bool {
        UserDefaults.standard.bool(forKey: "bossShowThinking")
    }
}

// MARK: - Safe Collection Access

private extension Collection {
    subscript(safe index: Index) -> Element? {
        indices.contains(index) ? self[index] : nil
    }
}
