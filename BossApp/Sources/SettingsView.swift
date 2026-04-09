import AppKit
import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var vm: ChatViewModel
    @AppStorage("bossFontSize") private var fontSize: Double = 15
    @AppStorage("bossDefaultMode") private var defaultMode: String = WorkMode.default.rawValue
    @AppStorage("bossDefaultExecStyle") private var defaultExecStyle: String = ExecutionStyle.singlePass.rawValue
    @AppStorage("bossAutoScroll") private var autoScroll: Bool = true
    @AppStorage("bossShowThinking") private var showThinking: Bool = false

    var body: some View {
        ScrollView(.vertical, showsIndicators: false) {
            VStack(alignment: .leading, spacing: 24) {
                Text("Settings")
                    .font(.system(size: 22, weight: .semibold))
                    .foregroundColor(Color.white.opacity(0.92))
                    .padding(.bottom, 4)

                appearanceSection
                chatSection
                memorySection
                backendSection
                aboutSection
            }
            .padding(24)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(BossColor.black)
    }

    // MARK: - Appearance

    private var appearanceSection: some View {
        SettingsCard(title: "Appearance") {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text("Font Size")
                        .font(.system(size: 13, weight: .medium))
                        .foregroundColor(Color.white.opacity(0.82))
                    Spacer()
                    Text("\(Int(fontSize)) pt")
                        .font(.system(size: 12, design: .monospaced))
                        .foregroundColor(Color.white.opacity(0.55))
                }
                Slider(value: $fontSize, in: 12...18, step: 1)
                    .tint(BossColor.accent)
            }
        }
    }

    // MARK: - Chat

    private var chatSection: some View {
        SettingsCard(title: "Chat") {
            VStack(alignment: .leading, spacing: 14) {
                settingsRow("Default Mode") {
                    Picker("", selection: $defaultMode) {
                        ForEach(WorkMode.allCases, id: \.rawValue) { mode in
                            Text(mode.label).tag(mode.rawValue)
                        }
                    }
                    .pickerStyle(.segmented)
                    .frame(maxWidth: 280)
                }

                settingsRow("Execution Style") {
                    Picker("", selection: $defaultExecStyle) {
                        Text("Single Pass").tag(ExecutionStyle.singlePass.rawValue)
                        Text("Iterative").tag(ExecutionStyle.iterative.rawValue)
                    }
                    .pickerStyle(.segmented)
                    .frame(maxWidth: 200)
                }

                settingsToggle("Auto-scroll during streaming", isOn: $autoScroll)
                settingsToggle("Show thinking content by default", isOn: $showThinking)
            }
        }
    }

    // MARK: - Memory

    private var memorySection: some View {
        SettingsCard(title: "Memory") {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text("Auto-inject memory into prompts")
                        .font(.system(size: 13))
                        .foregroundColor(Color.white.opacity(0.72))
                    Spacer()
                    Text("Backend setting")
                        .font(.system(size: 11))
                        .foregroundColor(Color.white.opacity(0.35))
                }

                Text("Edit ~/.boss/config.toml to change memory injection behavior.")
                    .font(.system(size: 11))
                    .foregroundColor(Color.white.opacity(0.35))

                if let status = vm.systemStatus {
                    if let pending = status.pendingApprovalsCount {
                        HStack {
                            Text("Pending approvals")
                                .font(.system(size: 13))
                                .foregroundColor(Color.white.opacity(0.72))
                            Spacer()
                            Text("\(pending)")
                                .font(.system(size: 12, design: .monospaced))
                                .foregroundColor(Color.white.opacity(0.55))
                        }
                    }
                }
            }
        }
    }

    // MARK: - Backend

    private var backendSection: some View {
        SettingsCard(title: "Backend") {
            VStack(alignment: .leading, spacing: 10) {
                readOnlyRow("API Base URL", value: APIClient.shared.baseURL)
                readOnlyRow("Port", value: "8321")

                if let status = vm.systemStatus {
                    if let ws = status.workspacePath {
                        readOnlyRow("Workspace", value: ws)
                    }
                    if let provider = status.providerMode {
                        readOnlyRow("Provider", value: provider)
                    }
                }

                HStack(spacing: 12) {
                    Button(action: restartBackend) {
                        HStack(spacing: 6) {
                            Image(systemName: "arrow.clockwise")
                                .font(.system(size: 11, weight: .medium))
                            Text("Restart Backend")
                                .font(.system(size: 12, weight: .medium))
                        }
                        .foregroundColor(Color.white.opacity(0.72))
                        .padding(.horizontal, 12)
                        .padding(.vertical, 7)
                        .background(
                            RoundedRectangle(cornerRadius: 8)
                                .fill(Color.white.opacity(0.06))
                        )
                    }
                    .buttonStyle(.plain)

                    Button(action: openConfig) {
                        HStack(spacing: 6) {
                            Image(systemName: "doc.text")
                                .font(.system(size: 11, weight: .medium))
                            Text("Open Config")
                                .font(.system(size: 12, weight: .medium))
                        }
                        .foregroundColor(Color.white.opacity(0.72))
                        .padding(.horizontal, 12)
                        .padding(.vertical, 7)
                        .background(
                            RoundedRectangle(cornerRadius: 8)
                                .fill(Color.white.opacity(0.06))
                        )
                    }
                    .buttonStyle(.plain)
                }
                .padding(.top, 4)
            }
        }
    }

    // MARK: - About

    private var aboutSection: some View {
        SettingsCard(title: "About") {
            VStack(alignment: .leading, spacing: 10) {
                readOnlyRow("App Version", value: Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "dev")
                if let status = vm.systemStatus, let ver = status.appVersion {
                    readOnlyRow("Backend Version", value: ver)
                }
                if let status = vm.systemStatus, let build = status.buildMarker {
                    readOnlyRow("Build Marker", value: build)
                }

                Button(action: {
                    let bossDir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".boss")
                    NSWorkspace.shared.open(bossDir)
                }) {
                    HStack(spacing: 6) {
                        Image(systemName: "folder")
                            .font(.system(size: 11, weight: .medium))
                        Text("Open Data Directory")
                            .font(.system(size: 12, weight: .medium))
                    }
                    .foregroundColor(Color.white.opacity(0.72))
                    .padding(.horizontal, 12)
                    .padding(.vertical, 7)
                    .background(
                        RoundedRectangle(cornerRadius: 8)
                            .fill(Color.white.opacity(0.06))
                    )
                }
                .buttonStyle(.plain)
                .padding(.top, 4)
            }
        }
    }

    // MARK: - Helpers

    private func settingsRow<Content: View>(_ label: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(.system(size: 13, weight: .medium))
                .foregroundColor(Color.white.opacity(0.82))
            content()
        }
    }

    private func settingsToggle(_ label: String, isOn: Binding<Bool>) -> some View {
        Toggle(isOn: isOn) {
            Text(label)
                .font(.system(size: 13))
                .foregroundColor(Color.white.opacity(0.82))
        }
        .toggleStyle(.switch)
        .tint(BossColor.accent)
    }

    private func readOnlyRow(_ label: String, value: String) -> some View {
        HStack {
            Text(label)
                .font(.system(size: 13))
                .foregroundColor(Color.white.opacity(0.72))
            Spacer()
            Text(value)
                .font(.system(size: 12, design: .monospaced))
                .foregroundColor(Color.white.opacity(0.55))
                .lineLimit(1)
                .textSelection(.enabled)
        }
    }

    // MARK: - Actions

    private func restartBackend() {
        Task {
            let result = await LocalBackendBootstrapper.shared.ensureBackendReady(api: APIClient.shared)
            await MainActor.run {
                switch result {
                case .ready, .started:
                    vm.startupIssue = nil
                case .warning(let msg):
                    vm.startupIssue = msg
                case .failure(let msg):
                    vm.startupIssue = msg
                }
            }
        }
    }

    private func openConfig() {
        let configPath = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".boss/config.toml")
        NSWorkspace.shared.open(configPath)
    }
}

// MARK: - Card wrapper matching existing dark theme

private struct SettingsCard<Content: View>: View {
    let title: String
    @ViewBuilder let content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title.uppercased())
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(Color.white.opacity(0.35))
                .tracking(1.2)

            content()
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 12)
                .fill(Color.white.opacity(0.035))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(Color.white.opacity(0.06), lineWidth: 1)
        )
    }
}
