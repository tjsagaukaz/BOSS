import SwiftUI

@MainActor
final class IOSDeliveryState: ObservableObject {
    @Published var status: IOSDeliveryStatusInfo?
    @Published var runs: [IOSDeliveryRunInfo] = []
    @Published var selectedRun: IOSDeliveryRunInfo?
    @Published var selectedRunEvents: [IOSDeliveryEventInfo] = []
    @Published var refreshError: String?
    @Published var actionError: String?
    @Published var isCreatingRun: Bool = false

    /// Pipeline phase for optimistic UI updates
    enum PipelineActivity: Equatable {
        case idle
        case creating
        case running(runId: String)
    }
    @Published var activity: PipelineActivity = .idle

    private let api = APIClient.shared

    // MARK: - Refresh

    func refresh() async {
        do {
            let s = try await api.fetchIOSDeliveryStatus()
            status = s
            runs = s.activeRuns + s.recentCompleted
            if let current = selectedRun,
               let refreshed = runs.first(where: { $0.runId == current.runId }) {
                selectedRun = refreshed
            } else if selectedRun == nil {
                selectedRun = runs.first
            }
            refreshError = nil
        } catch {
            refreshError = "iOS Delivery refresh failed. \(errorMessage(error))"
        }
    }

    func refreshSelectedRunEvents() async {
        guard let run = selectedRun else { return }
        do {
            selectedRunEvents = try await api.fetchIOSDeliveryRunEvents(runId: run.runId)
        } catch {
            // Non-fatal — events are supplementary
            selectedRunEvents = []
        }
    }

    // MARK: - Actions

    func createAndStartRun(
        projectPath: String,
        scheme: String? = nil,
        configuration: String = "Release",
        exportMethod: String = "app-store",
        uploadTarget: String = "none"
    ) async {
        isCreatingRun = true
        activity = .creating
        actionError = nil
        do {
            let run = try await api.createIOSDeliveryRun(
                projectPath: projectPath,
                scheme: scheme,
                configuration: configuration,
                exportMethod: exportMethod,
                uploadTarget: uploadTarget
            )
            selectedRun = run
            // Actually start the pipeline — create only makes a pending record
            _ = try await api.startIOSDeliveryRun(runId: run.runId)
            activity = .running(runId: run.runId)
            await refresh()
        } catch {
            actionError = "Failed to create run: \(errorMessage(error))"
            activity = .idle
        }
        isCreatingRun = false
    }

    func cancelRun(_ runId: String) async {
        actionError = nil
        do {
            let updated = try await api.cancelIOSDeliveryRun(runId: runId)
            selectedRun = updated
            activity = .idle
            await refresh()
        } catch {
            actionError = "Cancel failed: \(errorMessage(error))"
        }
    }

    func triggerUpload(_ runId: String) async {
        actionError = nil
        do {
            let updated = try await api.triggerIOSDeliveryUpload(runId: runId)
            selectedRun = updated
            await refresh()
        } catch {
            actionError = "Upload failed: \(errorMessage(error))"
        }
    }

    func selectRun(_ run: IOSDeliveryRunInfo) {
        selectedRun = run
        Task { await refreshSelectedRunEvents() }
    }

    // MARK: - Polling for active runs

    func pollActiveRun() async {
        guard let run = selectedRun, !run.isTerminal else {
            if case .running = activity { activity = .idle }
            return
        }
        // Only poll upload-status once the upload has finished and the build
        // is processing on App Store Connect.  "uploading" means the upload
        // subprocess is still running — polling /upload-status during that
        // phase would incorrectly persist a premature processing transition.
        if run.uploadStatus == "processing" {
            _ = try? await api.fetchIOSDeliveryUploadStatus(runId: run.runId)
        }
        await refresh()
        // If the run completed, stop activity
        if let updated = selectedRun, updated.isTerminal {
            activity = .idle
        }
    }

    // MARK: - Deep link / chat handoff

    func focusRun(_ runId: String) async {
        await refresh()
        if let match = runs.first(where: { $0.runId == runId }) {
            selectedRun = match
            Task { await refreshSelectedRunEvents() }
        }
    }

    private func errorMessage(_ error: Error) -> String {
        if let apiError = error as? APIError {
            return apiError.userMessage
        }
        return error.localizedDescription
    }
}
