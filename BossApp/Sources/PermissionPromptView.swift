import SwiftUI

struct PermissionPromptView: View {
    let request: PermissionRequest
    let onDecision: (PermissionDecision) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Boss wants to:")
                .font(.system(size: 11, weight: .medium))
                .foregroundColor(Color.white.opacity(0.45))

            Text(request.description)
                .font(.system(size: 14))
                .foregroundColor(Color.white.opacity(0.88))

            Text(request.scopeLabel)
                .font(.system(size: 11))
                .foregroundColor(Color.white.opacity(0.42))

            HStack(spacing: 8) {
                BossSecondaryButton(title: "Allow Once") {
                    onDecision(.allowOnce)
                }

                BossSecondaryButton(title: "Always Allow") {
                    onDecision(.alwaysAllow)
                }

                BossTertiaryButton(title: "Deny") {
                    onDecision(.deny)
                }
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(RoundedRectangle(cornerRadius: 14).fill(Color.white.opacity(0.045)))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color.white.opacity(0.06), lineWidth: 1))
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Permission request: \(request.description)")
    }
}