import Foundation
import Vision
import CoreImage
import AppKit

// 自动找文档四角 + 透视校正。找不到就原样输出（后续 OCR 仍能跑，只是质量差些）。
let arguments = CommandLine.arguments
guard arguments.count >= 3 else {
    FileHandle.standardError.write("usage: rectify <in.jpg> <out.jpg>\n".data(using: .utf8)!)
    exit(1)
}
let inputURL = URL(fileURLWithPath: arguments[1])
let outputPath = arguments[2]
guard let image = CIImage(contentsOf: inputURL) else {
    FileHandle.standardError.write("读不到图片：\(arguments[1])\n".data(using: .utf8)!)
    exit(2)
}
let handler = VNImageRequestHandler(ciImage: image, options: [:])
var detected: VNRectangleObservation?

if #available(macOS 13.0, *) {
    let documentRequest = VNDetectDocumentSegmentationRequest { request, _ in
        detected = (request.results as? [VNRectangleObservation])?.first
    }
    do { try handler.perform([documentRequest]) } catch {
        FileHandle.standardError.write("文档检测失败：\(error.localizedDescription)\n".data(using: .utf8)!)
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
        FileHandle.standardError.write("矩形检测失败：\(error.localizedDescription)\n".data(using: .utf8)!)
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
        FileHandle.standardError.write("写出失败：\(error.localizedDescription)\n".data(using: .utf8)!)
        exit(4)
    }
}

guard let box = detected else {
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
