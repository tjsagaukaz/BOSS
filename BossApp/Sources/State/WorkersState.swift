import SwiftUI

@MainActor
final class WorkersState: ObservableObject {
    @Published var workPlans: [WorkPlanInfo] = []
    @Published var selectedWorkPlan: WorkPlanInfo?
    @Published var workersRefreshError: String?

    private let api = APIClient.shared

    func refresh() async {
        do {
            workPlans = try await api.fetchWorkPlans(limit: 50)
            if let current = selectedWorkPlan,
               let refreshed = workPlans.first(where: { $0.planId == current.planId }) {
                selectedWorkPlan = refreshed
            } else if selectedWorkPlan == nil {
                selectedWorkPlan = workPlans.first
            }
            workersRefreshError = nil
        } catch {
            workersRefreshError = "Workers refresh failed. \(errorMessage(error))"
        }
    }

    private func errorMessage(_ error: Error) -> String {
        if let apiError = error as? APIError {
            return apiError.userMessage
        }
        return error.localizedDescription
    }
}
