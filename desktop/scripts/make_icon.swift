import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

// Regenerate with: swift desktop/scripts/make_icon.swift desktop/src-tauri/icons/icon.png
let size = 512
let context = CGContext(data: nil, width: size, height: size, bitsPerComponent: 8,
                        bytesPerRow: 0, space: CGColorSpace(name: CGColorSpace.sRGB)!,
                        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
context.translateBy(x: 0, y: CGFloat(size))
context.scaleBy(x: 1, y: -1)
let bounds = CGRect(x: 47, y: 47, width: 418, height: 418)
context.addPath(CGPath(roundedRect: bounds, cornerWidth: 96, cornerHeight: 96, transform: nil))
context.saveGState()
context.clip()
let colors = [CGColor(red: 0.38, green: 0.83, blue: 1, alpha: 1),
              CGColor(red: 0.10, green: 0.54, blue: 1, alpha: 1),
              CGColor(red: 0.0, green: 0.36, blue: 0.95, alpha: 1)] as CFArray
let gradient = CGGradient(colorsSpace: CGColorSpace(name: CGColorSpace.sRGB), colors: colors, locations: [0, 0.45, 1])!
context.drawLinearGradient(gradient, start: CGPoint(x: 256, y: 47), end: CGPoint(x: 256, y: 465), options: [])
context.restoreGState()
context.setStrokeColor(red: 1, green: 0.98, blue: 0.94, alpha: 1)
context.setLineWidth(15)
context.setLineCap(.round)
context.setLineJoin(.round)
context.move(to: CGPoint(x: 256, y: 193))
context.addQuadCurve(to: CGPoint(x: 153, y: 174), control: CGPoint(x: 214, y: 160))
context.addLine(to: CGPoint(x: 153, y: 320))
context.addQuadCurve(to: CGPoint(x: 256, y: 339), control: CGPoint(x: 214, y: 306))
context.addQuadCurve(to: CGPoint(x: 359, y: 320), control: CGPoint(x: 298, y: 306))
context.addLine(to: CGPoint(x: 359, y: 174))
context.addQuadCurve(to: CGPoint(x: 256, y: 193), control: CGPoint(x: 298, y: 160))
context.addLine(to: CGPoint(x: 256, y: 339))
context.strokePath()
let output = URL(fileURLWithPath: CommandLine.arguments[1])
let destination = CGImageDestinationCreateWithURL(output as CFURL, UTType.png.identifier as CFString, 1, nil)!
CGImageDestinationAddImage(destination, context.makeImage()!, nil)
CGImageDestinationFinalize(destination)
