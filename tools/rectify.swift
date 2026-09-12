import Foundation
import Vision
import CoreImage
import AppKit

// 自动找文档四角 + 透视校正。找不到就原样输出（后续 OCR 仍能跑，只是质量差些）。
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
let handler = VNImageRequestHandler(ciImage: image, options: [:])
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

/// 检测到的四边形在画面里占多大（归一化包围盒面积）。
/// 太小的多半是图章、二维码或局部边框 —— 按它做透视校正会把整页裁成一小块。
func coverage(_ box: VNRectangleObservation) -> CGFloat {
    let horizontal = [box.topLeft.x, box.topRight.x, box.bottomLeft.x, box.bottomRight.x]
    let vertical = [box.topLeft.y, box.topRight.y, box.bottomLeft.y, box.bottomRight.y]
    return (horizontal.max()! - horizontal.min()!) * (vertical.max()! - vertical.min()!)
}

guard let box = detected, coverage(box) >= 0.25 else {
    FileHandle.standardError.write(Data("没找到足够大的页面（面积占比 < 25%），按原图输出\n".utf8))
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
