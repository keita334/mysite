import CoreGraphics
import Foundation

@testable import DataSource

extension FrameImage {
    static func dummy() -> FrameImage {
        let context = CGContext(
            data: nil,
            width: 1,
            height: 1,
            bitsPerComponent: 8,
            bytesPerRow: 0,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        )!
        return FrameImage(id: UUID(), cgImage: context.makeImage()!)
    }
}
