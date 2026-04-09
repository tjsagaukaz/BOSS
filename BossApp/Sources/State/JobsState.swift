import SwiftUI

@MainActor
final class JobsState: ObservableObject {
    @Published var jobs: [BackgroundJobInfo] = []
    @Published var selectedJob: BackgroundJobInfo?
    @Published var selectedJobLog: BackgroundJobLogTailInfo?
    @Published var jobsRefreshError: String?
    @Published var isLaunchingBackgroundJob: Bool = false

    private let api = APIClient.shared

    func refresh() async {
        do {
            jobs = try await api.fetchJobs(limit: 80)
            if let current = selectedJob,
               let refreshed = jobs.first(where: { $0.jobId == current.jobId }) {
                selectedJob = refreshed
                selectedJobLog = try? await api.fetchJobLog(jobId: refreshed.jobId, limit: 240)
            } else if selectedJob == nil {
                selectedJob = jobs.first
                if let first = selectedJob {
                    selectedJobLog = try? await api.fetchJobLog(jobId: first.jobId, limit: 240)
                } else {
                    selectedJobLog = nil
                }
            }
            jobsRefreshError = nil
        } catch {
            jobsRefreshError = "Jobs refresh failed. \(errorMessage(error))"
        }
    }

    func selectJob(_ job: BackgroundJobInfo) {
        selectedJob = job
        Task {
            do {
                selectedJobLog = try await api.fetchJobLog(jobId: job.jobId, limit: 240)
                jobsRefreshError = nil
            } catch {
                jobsRefreshError = "Couldn't load job log. \(errorMessage(error))"
            }
        }
    }

    func cancelJob(_ job: BackgroundJobInfo) {
        Task {
            do {
                let updated = try await api.cancelJob(jobId: job.jobId)
                replaceJob(updated)
                selectedJob = updated
                selectedJobLog = try? await api.fetchJobLog(jobId: updated.jobId, limit: 240)
                jobsRefreshError = nil
            } catch {
                jobsRefreshError = "Couldn't cancel background job. \(errorMessage(error))"
            }
        }
    }

    func resumeJob(_ job: BackgroundJobInfo) {
        Task {
            do {
                let updated = try await api.resumeJob(jobId: job.jobId)
                replaceJob(updated)
                selectedJob = updated
                selectedJobLog = try? await api.fetchJobLog(jobId: updated.jobId, limit: 240)
                jobsRefreshError = nil
            } catch {
                jobsRefreshError = "Couldn't resume background job. \(errorMessage(error))"
            }
        }
    }

    func replaceJob(_ updated: BackgroundJobInfo) {
        if let index = jobs.firstIndex(where: { $0.jobId == updated.jobId }) {
            jobs[index] = updated
        } else {
            jobs.insert(updated, at: 0)
        }
    }

    private func errorMessage(_ error: Error) -> String {
        if let apiError = error as? APIError {
            return apiError.userMessage
        }
        return error.localizedDescription
    }
}
