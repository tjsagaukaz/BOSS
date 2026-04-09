import SwiftUI

@MainActor
final class MemoryState: ObservableObject {
    @Published var memoryOverview: MemoryOverview?
    @Published var memoryStats: MemoryStats?
    @Published var memoryRefreshError: String?

    /// Called after memory mutations so the parent can refresh sidebar data.
    var onMemoryChanged: (() async -> Void)?

    private let api = APIClient.shared

    func refreshOverview(sessionId: String, message: String?) async {
        do {
            memoryOverview = try await api.fetchMemoryOverview(
                sessionId: sessionId,
                message: message
            )
            memoryRefreshError = nil
        } catch {
            memoryRefreshError = "Memory refresh failed. \(errorMessage(error))"
        }
    }

    func refreshStats() async {
        memoryStats = try? await api.fetchStats()
    }

    func forgetMemory(sourceTable: String, itemId: Int) {
        Task {
            do {
                try await api.deleteMemoryItem(sourceTable: sourceTable, itemId: itemId)
                memoryRefreshError = nil
                await onMemoryChanged?()
            } catch {
                memoryRefreshError = "Couldn't update memory. \(errorMessage(error))"
            }
        }
    }

    func saveMemoryCandidate(candidateId: Int, label: String, text: String, evidence: String?) {
        Task {
            do {
                try await api.updateMemoryCandidate(candidateId: candidateId, label: label, text: text, evidence: evidence)
                memoryRefreshError = nil
                await onMemoryChanged?()
            } catch {
                memoryRefreshError = "Couldn't update pending memory. \(errorMessage(error))"
            }
        }
    }

    func approveMemoryCandidate(
        candidateId: Int,
        label: String,
        text: String,
        evidence: String?,
        pin: Bool = false
    ) {
        Task {
            do {
                try await api.approveMemoryCandidate(
                    candidateId: candidateId,
                    label: label,
                    text: text,
                    evidence: evidence,
                    pin: pin
                )
                memoryRefreshError = nil
                await onMemoryChanged?()
            } catch {
                memoryRefreshError = "Couldn't approve memory. \(errorMessage(error))"
            }
        }
    }

    func rejectMemoryCandidate(candidateId: Int) {
        Task {
            do {
                try await api.rejectMemoryCandidate(candidateId: candidateId)
                memoryRefreshError = nil
                await onMemoryChanged?()
            } catch {
                memoryRefreshError = "Couldn't reject memory. \(errorMessage(error))"
            }
        }
    }

    func expireMemoryCandidate(candidateId: Int) {
        Task {
            do {
                try await api.expireMemoryCandidate(candidateId: candidateId)
                memoryRefreshError = nil
                await onMemoryChanged?()
            } catch {
                memoryRefreshError = "Couldn't expire memory. \(errorMessage(error))"
            }
        }
    }

    func setMemoryPinned(itemId: Int, pinned: Bool) {
        Task {
            do {
                try await api.setMemoryPinned(itemId: itemId, pinned: pinned)
                memoryRefreshError = nil
                await onMemoryChanged?()
            } catch {
                memoryRefreshError = "Couldn't update pin state. \(errorMessage(error))"
            }
        }
    }

    func reset() {
        memoryOverview = nil
        memoryRefreshError = nil
    }

    private func errorMessage(_ error: Error) -> String {
        if let apiError = error as? APIError {
            return apiError.userMessage
        }
        return error.localizedDescription
    }
}
