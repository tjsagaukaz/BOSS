import SwiftUI

@MainActor
final class ReviewState: ObservableObject {
    @Published var reviewCapabilities: ReviewCapabilitiesInfo?
    @Published var reviewHistory: [ReviewRunInfo] = []
    @Published var selectedReviewRun: ReviewRunInfo?
    @Published var selectedReviewProjectPath: String?
    @Published var selectedReviewTarget: ReviewTargetKind = .auto
    @Published var reviewBaseRef: String = ""
    @Published var reviewHeadRef: String = ""
    @Published var reviewFilePathsText: String = ""
    @Published var isRunningReview: Bool = false
    @Published var reviewRefreshError: String?

    private let api = APIClient.shared

    func refresh(fallbackProjectPath: String?) async {
        let requestedProjectPath = selectedReviewProjectPath ?? fallbackProjectPath
        var failures: [String] = []

        do {
            let capabilities = try await api.fetchReviewCapabilities(projectPath: requestedProjectPath)
            reviewCapabilities = capabilities
            if selectedReviewProjectPath == nil || selectedReviewProjectPath?.isEmpty == true {
                selectedReviewProjectPath = capabilities.projectPath
            }
            if selectedReviewTarget != .auto,
               !capabilities.availableTargets.contains(selectedReviewTarget.rawValue),
               let fallback = ReviewTargetKind(rawValue: capabilities.defaultTarget) {
                selectedReviewTarget = fallback
            }
        } catch {
            failures.append("Review capabilities unavailable: \(errorMessage(error))")
        }

        do {
            reviewHistory = try await api.fetchReviewHistory(limit: 30)
            if let current = selectedReviewRun,
               let refreshed = reviewHistory.first(where: { $0.reviewId == current.reviewId }) {
                selectedReviewRun = refreshed
            } else if selectedReviewRun == nil {
                selectedReviewRun = reviewHistory.first
            }
        } catch {
            failures.append("Review history unavailable: \(errorMessage(error))")
        }

        if failures.isEmpty {
            reviewRefreshError = nil
        } else {
            reviewRefreshError = failures.joined(separator: "  ")
        }
    }

    func selectTarget(_ target: ReviewTargetKind) {
        selectedReviewTarget = target
    }

    func selectRun(_ run: ReviewRunInfo) {
        selectedReviewRun = run
    }

    func runReview() {
        guard !isRunningReview else { return }
        isRunningReview = true
        reviewRefreshError = nil

        let target = selectedReviewTarget
        let projectPath = normalizedValue(selectedReviewProjectPath)
        let baseRef = normalizedValue(reviewBaseRef)
        let headRef = normalizedValue(reviewHeadRef)
        let filePaths = parsedFilePaths()

        Task {
            do {
                let result = try await api.runReview(
                    target: target,
                    projectPath: projectPath,
                    baseRef: baseRef,
                    headRef: headRef,
                    filePaths: filePaths
                )
                reviewHistory.removeAll { $0.reviewId == result.reviewId }
                reviewHistory.insert(result, at: 0)
                selectedReviewRun = result
                reviewRefreshError = nil
                await refresh(fallbackProjectPath: nil)
            } catch {
                reviewRefreshError = "Review failed. \(errorMessage(error))"
            }
            isRunningReview = false
        }
    }

    func reset() {
        reviewCapabilities = nil
        reviewHistory = []
        selectedReviewRun = nil
        reviewRefreshError = nil
    }

    func parsedFilePaths() -> [String] {
        reviewFilePathsText
            .split(whereSeparator: { $0 == "\n" || $0 == "," })
            .map { String($0).trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }

    func normalizedValue(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private func errorMessage(_ error: Error) -> String {
        if let apiError = error as? APIError {
            return apiError.userMessage
        }
        return error.localizedDescription
    }
}
