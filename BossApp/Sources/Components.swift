import SwiftUI

// MARK: - Buttons

/// Red accent background, white text, capsule shape.
/// Use for destructive or important actions (Cancel, Deny, Take Over).
struct BossPrimaryButton: View {
    let title: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 12, weight: .medium))
                .foregroundColor(.white)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(Capsule().fill(BossColor.accent))
        }
        .buttonStyle(.plain)
    }
}

/// White opacity background, white text, rounded rect.
/// Use for standard actions (Refresh, Allow Once, Retry).
struct BossSecondaryButton: View {
    let title: String
    var icon: String? = nil
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if let icon {
                    Image(systemName: icon)
                        .font(.system(size: 11))
                }
                Text(title)
                    .font(.system(size: 12, weight: .medium))
            }
            .foregroundColor(Color.white.opacity(0.82))
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(Color.white.opacity(0.08))
            )
        }
        .buttonStyle(.plain)
    }
}

/// No background, subtle text.
/// Use for tertiary actions (Revoke, Remove, Forget, Reject).
struct BossTertiaryButton: View {
    let title: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 12))
                .foregroundColor(Color.white.opacity(0.55))
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Card

/// Standard card container with dark background and subtle border.
struct BossCard<Content: View>: View {
    @ViewBuilder let content: () -> Content

    var body: some View {
        content()
            .padding(14)
            .background(RoundedRectangle(cornerRadius: 12).fill(Color.white.opacity(0.035)))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.white.opacity(0.06), lineWidth: 1))
    }
}

// MARK: - Status Pill

/// Consistent status pill with colored background at 0.15 opacity, text in full color.
struct StatusPill: View {
    let text: String
    var color: Color = Color.white.opacity(0.55)

    var body: some View {
        Text(text.replacingOccurrences(of: "_", with: " "))
            .font(.system(size: 10, weight: .semibold))
            .foregroundColor(color)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(Capsule().fill(color.opacity(0.15)))
    }
}
