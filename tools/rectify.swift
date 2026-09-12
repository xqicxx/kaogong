import Foundation
import Vision
import CoreImage
import AppKit

// 自动找文档四角 + 透视校正。找不到就原样输出（后续 OCR 仍能跑，只是质量差些）。
import ImageIO

/// 从图片文件读 EXIF 方向；读不到就按 .up。
func ExifOrientation(of url: URL) -> UInt32 {
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
          let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
          let raw = properties[kCGImagePropertyOrientation] as? UInt32 else { return 1 }
    return raw
}

let arguments = CommandLine.arguments
guard arguments.count >= 3 else {
    FileHandle.standardError.write(Data("usage: rectify <in.jpg> <out.jpg>\n".utf8))
    exit(1)
}
let inputURL = URL(fileURLWithPath: arguments[1])
let outputPath = arguments[2]
if inputURL.standardizedFileURL == URL(fileURLWithPath: outputPath).standardizedFileURL {
    FileHandle.standardError.write(Data("输入和输出是同一个文件，会写坏原图\n".utf8))
    exit(5)
}
guard let image = CIImage(contentsOf: inputURL) else {
    FileHandle.standardError.write(Data("读不到图片：\(arguments[1])\n".utf8))
    exit(2)
}
// 手机拍的照片带 EXIF 旋转标记，不告诉 Vision 会把页面当横躺的
let orientation = CGImagePropertyOrientation(rawValue: ExifOrientation(of: inputURL)) ?? .up
let handler = VNImageRequestHandler(ciImage: image, orientation: orientation, options: [:])
var detected: VNRectangleObservation?

if #available(macOS 13.0, *) {
    let documentRequest = VNDetectDocumentSegmentationRequest { request, _ in
        detected = (request.results as? [VNRectangleObservation])?.first
    }
    do { try handler.perform([documentRequest]) } catch {
        FileHandle.standardError.write(Data("文档检测失败：\(error.localizedDescription)\n".utf8))
    }
}
if detected == nil {
    let rectangleRequest = VNDetectRectanglesRequest { request, _ in
        detected = (request.results as? [VNRectangleObservation])?
            .sorted { $0.confidence > $1.confidence }.first
    }
    rectangleRequest.maximumObservations = 1
    rectangleRequest.minimumConfidence = 0.2
    do { try handler.perform([rectangleRequest]) } catch {
        FileHandle.standardError.write(Data("矩形检测失败：\(error.localizedDescription)\n".utf8))
    }
}

let imageWidth = image.extent.width
let imageHeight = image.extent.height
func toPixels(_ point: CGPoint) -> CGPoint {
    CGPoint(x: point.x * imageWidth, y: point.y * imageHeight)
}

func write(_ output: CIImage, note: String) {
    let context = CIContext()
    do {
        try context.writeJPEGRepresentation(of: output, to: URL(fileURLWithPath: outputPath),
                                            colorSpace: CGColorSpaceCreateDeviceRGB(), options: [:])
        print(note)
    } catch {
        // 原来这里是 try? + exit(0)：写失败也报成功，调用方拿到的是坏文件。
        // 换成非零退出码，让上游的 `|| cp` 兜底路径能生效。
        FileHandle.standardError.write(Data("写出失败：\(error.localizedDescription)\n".utf8))
        exit(4)
    }
}

/// 四边形的真实面积（鞋带公式，归一化坐标）。
/// 之前用「包围盒面积」，共线或自交的退化四边形也能过 0.25 的门槛，
/// 然后 CIPerspectiveCorrection 会把它拉成一个巨大的畸变图。
func polygonArea(_ box: VNRectangleObservation) -> CGFloat {
    let points = [box.topLeft, box.topRight, box.bottomRight, box.bottomLeft]
    var sum: CGFloat = 0
    for index in 0..<points.count {
        let current = points[index]
        let next = points[(index + 1) % points.count]
        sum += current.x * next.y - next.x * current.y
    }
    return abs(sum) / 2
}

/// 四边形是否凸（叉积同号）。自交的四边形不是凸的，不能拿来校正。
func isConvex(_ box: VNRectangleObservation) -> Bool {
    let points = [box.topLeft, box.topRight, box.bottomRight, box.bottomLeft]
    var signs: [CGFloat] = []
    for index in 0..<points.count {
        let a = points[index]
        let b = points[(index + 1) % points.count]
        let c = points[(index + 2) % points.count]
        signs.append((b.x - a.x) * (c.y - b.y) - (b.y - a.y) * (c.x - b.x))
    }
    return signs.allSatisfy { $0 >= 0 } || signs.allSatisfy { $0 <= 0 }
}

func looksLikePage(_ box: VNRectangleObservation) -> Bool {
    let area = polygonArea(box)
    return area >= 0.25 && area <= 0.999 && isConvex(box)
}

guard let box = detected, looksLikePage(box) else {
    FileHandle.standardError.write(Data("没找到像样的页面区域（面积/凸性不合格），按原图输出\n".utf8))
    write(image, note: "NO_DOCUMENT_DETECTED")
    exit(0)
}

let correction = CIFilter(name: "CIPerspectiveCorrection")!
correction.setValue(image, forKey: kCIInputImageKey)
correction.setValue(CIVector(cgPoint: toPixels(box.topLeft)), forKey: "inputTopLeft")
correction.setValue(CIVector(cgPoint: toPixels(box.topRight)), forKey: "inputTopRight")
correction.setValue(CIVector(cgPoint: toPixels(box.bottomLeft)), forKey: "inputBottomLeft")
correction.setValue(CIVector(cgPoint: toPixels(box.bottomRight)), forKey: "inputBottomRight")
guard let corrected = correction.outputImage else {
    FileHandle.standardError.write("透视校正失败\n".data(using: .utf8)!)
    exit(3)
}
write(corrected, note: String(format: "DETECTED confidence=%.3f out=%.0fx%.0f",
                             box.confidence, corrected.extent.width, corrected.extent.height))
