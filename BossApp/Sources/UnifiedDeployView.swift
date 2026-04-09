import SwiftUI

struct UnifiedDeployView: View {
    @EnvironmentObject var vm: ChatViewModel
    @State private var selectedTab: DeployTab = .jobs

    enum DeployTab: String, CaseIterable {
        case jobs = "Jobs"
        case preview = "Preview"
        case deploy = "Deploy"
        case ios = "iOS"
    }

    var body: some View {
        VStack(spacing: 0) {
            tabBar
                .padding(.top, 80)

            switch selectedTab {
            case .jobs:
                JobsView()
            case .preview:
                PreviewView()
            case .deploy:
                DeployView()
            case .ios:
                IOSDeliveryView()
            }
        }
        .background(BossColor.black)
    }

    private var tabBar: some View {
        HStack(spacing: 2) {
            ForEach(DeployTab.allCases, id: \.self) { tab in
                Button {
                    withAnimation(.easeInOut(duration: 0.15)) {
                        selectedTab = tab
                    }
                } label: {
                    Text(tab.rawValue)
                        .font(.system(size: 12, weight: selectedTab == tab ? .semibold : .medium))
                        .foregroundColor(selectedTab == tab ? .white : Color.white.opacity(0.45))
                        .padding(.horizontal, 14)
                        .padding(.vertical, 7)
                        .background(
                            RoundedRectangle(cornerRadius: 7)
                                .fill(selectedTab == tab ? Color.white.opacity(0.08) : .clear)
                        )
                }
                .buttonStyle(.plain)
            }
        }
        .padding(3)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(Color.white.opacity(0.04))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(Color.white.opacity(0.06), lineWidth: 1)
        )
        .padding(.horizontal, 24)
        .padding(.bottom, 8)
    }
}
