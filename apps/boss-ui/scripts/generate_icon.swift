import AppKit
import Foundation

let fileManager = FileManager.default
let projectRoot = URL(fileURLWithPath: fileManager.currentDirectoryPath)
let iconsRoot = projectRoot.appendingPathComponent("src-tauri/icons")
let iconsetRoot = iconsRoot.appendingPathComponent("BOSS.iconset")

try? fileManager.removeItem(at: iconsetRoot)
try fileManager.createDirectory(at: iconsetRoot, withIntermediateDirectories: true)

struct IconSpec {
    let filename: String
    let size: CGFloat
}

let specs: [IconSpec] = [
    .init(filename: "icon_16x16.png", size: 16),
    .init(filename: "icon_16x16@2x.png", size: 32),
    .init(filename: "icon_32x32.png", size: 32),
    .init(filename: "icon_32x32@2x.png", size: 64),
    .init(filename: "icon_128x128.png", size: 128),
    .init(filename: "icon_128x128@2x.png", size: 256),
    .init(filename: "icon_256x256.png", size: 256),
    .init(filename: "icon_256x256@2x.png", size: 512),
    .init(filename: "icon_512x512.png", size: 512),
    .init(filename: "icon_512x512@2x.png", size: 1024),
]

func pngData(from image: NSImage, size: CGFloat) -> Data? {
    let representation = NSBitmapImageRep(
        bitmapDataPlanes: nil,
        pixelsWide: Int(size),
        pixelsHigh: Int(size),
        bitsPerSample: 8,
        samplesPerPixel: 4,
        hasAlpha: true,
        isPlanar: false,
        colorSpaceName: .deviceRGB,
        bytesPerRow: 0,
        bitsPerPixel: 0
    )
    representation?.size = NSSize(width: size, height: size)

    NSGraphicsContext.saveGraphicsState()
    if let context = representation.flatMap({ NSGraphicsContext(bitmapImageRep: $0) }) {
        NSGraphicsContext.current = context
        context.cgContext.interpolationQuality = .high
        image.draw(in: NSRect(x: 0, y: 0, width: size, height: size))
        context.flushGraphics()
    }
    NSGraphicsContext.restoreGraphicsState()

    return representation?.representation(using: .png, properties: [:])
}

func roundedRect(_ rect: NSRect, radius: CGFloat) -> NSBezierPath {
    NSBezierPath(roundedRect: rect, xRadius: radius, yRadius: radius)
}

func drawIcon(size: CGFloat) -> NSImage {
    let image = NSImage(size: NSSize(width: size, height: size))
    image.lockFocus()

    let rect = NSRect(x: 0, y: 0, width: size, height: size)
    NSColor.clear.setFill()
    rect.fill()

    let inset = size * 0.06
    let cardRect = rect.insetBy(dx: inset, dy: inset)
    let radius = size * 0.22
    let card = roundedRect(cardRect, radius: radius)

    let baseGradient = NSGradient(colors: [
        NSColor(calibratedRed: 0.03, green: 0.13, blue: 0.28, alpha: 1.0),
        NSColor(calibratedRed: 0.02, green: 0.05, blue: 0.15, alpha: 1.0),
    ])!
    baseGradient.draw(in: card, angle: -32)

    let spotlightRect = NSRect(
        x: cardRect.minX - size * 0.08,
        y: cardRect.maxY - size * 0.58,
        width: size * 0.78,
        height: size * 0.72
    )
    let spotlight = NSGradient(colors: [
        NSColor(calibratedRed: 0.13, green: 0.93, blue: 0.83, alpha: 0.28),
        NSColor(calibratedRed: 0.13, green: 0.93, blue: 0.83, alpha: 0.0),
    ])!
    spotlight.draw(in: NSBezierPath(ovalIn: spotlightRect), relativeCenterPosition: NSPoint(x: 0, y: 0))

    let borderPath = roundedRect(cardRect.insetBy(dx: size * 0.01, dy: size * 0.01), radius: radius * 0.92)
    NSColor(calibratedWhite: 1.0, alpha: 0.12).setStroke()
    borderPath.lineWidth = max(2, size * 0.018)
    borderPath.stroke()

    let haloRect = cardRect.insetBy(dx: size * 0.17, dy: size * 0.17)
    let halo = NSBezierPath(ovalIn: haloRect)
    NSColor(calibratedRed: 0.18, green: 0.92, blue: 0.80, alpha: 0.90).setStroke()
    halo.lineWidth = max(6, size * 0.05)
    halo.stroke()

    let innerHaloRect = haloRect.insetBy(dx: size * 0.05, dy: size * 0.05)
    let innerHalo = NSBezierPath(ovalIn: innerHaloRect)
    NSColor(calibratedRed: 0.52, green: 0.98, blue: 0.90, alpha: 0.36).setStroke()
    innerHalo.lineWidth = max(2, size * 0.015)
    innerHalo.stroke()

    let font = NSFont.systemFont(ofSize: size * 0.40, weight: .black)
    let paragraph = NSMutableParagraphStyle()
    paragraph.alignment = .center
    let attrs: [NSAttributedString.Key: Any] = [
        .font: font,
        .foregroundColor: NSColor(calibratedWhite: 0.98, alpha: 0.97),
        .paragraphStyle: paragraph,
        .kern: -size * 0.015,
    ]
    let glyph = NSAttributedString(string: "B", attributes: attrs)
    let glyphRect = NSRect(
        x: cardRect.minX,
        y: cardRect.minY + size * 0.24,
        width: cardRect.width,
        height: size * 0.42
    )
    glyph.draw(in: glyphRect)

    let accentLine = NSBezierPath()
    accentLine.move(to: NSPoint(x: cardRect.minX + size * 0.22, y: cardRect.minY + size * 0.28))
    accentLine.line(to: NSPoint(x: cardRect.maxX - size * 0.18, y: cardRect.minY + size * 0.28))
    NSColor(calibratedRed: 0.34, green: 0.96, blue: 0.84, alpha: 0.85).setStroke()
    accentLine.lineWidth = max(3, size * 0.018)
    accentLine.lineCapStyle = .round
    accentLine.stroke()

    let nodeFill = NSColor(calibratedRed: 0.55, green: 0.99, blue: 0.90, alpha: 0.98)
    let nodeGlow = NSColor(calibratedRed: 0.20, green: 0.94, blue: 0.82, alpha: 0.25)
    let nodes = [
        NSPoint(x: cardRect.minX + size * 0.25, y: cardRect.minY + size * 0.28),
        NSPoint(x: cardRect.midX, y: cardRect.minY + size * 0.28),
        NSPoint(x: cardRect.maxX - size * 0.21, y: cardRect.minY + size * 0.28),
    ]
    for point in nodes {
        let glowRect = NSRect(x: point.x - size * 0.045, y: point.y - size * 0.045, width: size * 0.09, height: size * 0.09)
        nodeGlow.setFill()
        NSBezierPath(ovalIn: glowRect).fill()
        let dotRect = NSRect(x: point.x - size * 0.016, y: point.y - size * 0.016, width: size * 0.032, height: size * 0.032)
        nodeFill.setFill()
        NSBezierPath(ovalIn: dotRect).fill()
    }

    let wordmarkAttrs: [NSAttributedString.Key: Any] = [
        .font: NSFont.monospacedSystemFont(ofSize: size * 0.06, weight: .semibold),
        .foregroundColor: NSColor(calibratedRed: 0.46, green: 0.96, blue: 0.84, alpha: 0.92),
        .kern: size * 0.01,
    ]
    let wordmark = NSAttributedString(string: "BOSS", attributes: wordmarkAttrs)
    wordmark.draw(at: NSPoint(x: cardRect.minX + size * 0.12, y: cardRect.maxY - size * 0.17))

    image.unlockFocus()
    return image
}

for spec in specs {
    let image = drawIcon(size: spec.size)
    if let data = pngData(from: image, size: spec.size) {
        try data.write(to: iconsetRoot.appendingPathComponent(spec.filename))
    }
}

let iconUtil = Process()
iconUtil.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
iconUtil.arguments = ["-c", "icns", iconsetRoot.path, "-o", iconsRoot.appendingPathComponent("icon.icns").path]
try iconUtil.run()
iconUtil.waitUntilExit()

let preferredPNG = iconsetRoot.appendingPathComponent("icon_512x512@2x.png")
let appPNG = iconsRoot.appendingPathComponent("icon.png")
if fileManager.fileExists(atPath: appPNG.path) {
    try? fileManager.removeItem(at: appPNG)
}
try fileManager.copyItem(at: preferredPNG, to: appPNG)

print("Generated BOSS icon set at \(iconsRoot.path)")
