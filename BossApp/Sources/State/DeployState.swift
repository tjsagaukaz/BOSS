import SwiftUI

@MainActor
final class DeployState: ObservableObject {
    @Published var deployStatus: DeployStatusInfo?
    @Published var deployments: [DeploymentInfo] = []
    @Published var selectedDeployment: DeploymentInfo?
    @Published var deployRefreshError: String?

    private let api = APIClient.shared

    func refresh() async {
        do {
            deployStatus = try await api.fetchDeployStatus()
            deployments = try await api.fetchDeployments(limit: 50)
            if let current = selectedDeployment,
               let refreshed = deployments.first(where: { $0.deploymentId == current.deploymentId }) {
                selectedDeployment = refreshed
            } else if selectedDeployment == nil {
                selectedDeployment = deployments.first
            }
            deployRefreshError = nil
        } catch {
            deployRefreshError = "Deploy refresh failed. \(errorMessage(error))"
        }
    }

    func refreshStatus() async {
        deployStatus = try? await api.fetchDeployStatus()
    }

    private func errorMessage(_ error: Error) -> String {
        if let apiError = error as? APIError {
            return apiError.userMessage
        }
        return error.localizedDescription
    }
}
