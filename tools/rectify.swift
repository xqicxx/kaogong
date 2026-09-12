import Foundation
import Vision
import CoreImage
import AppKit

let args = CommandLine.arguments
guard args.count >= 3 else {
    FileHandle.standardError.write("usage: rectify <in.jpg> <out.jpg>\n".data(using: .utf8)!)
    exit(1)
}
let inURL = URL(fileURLWithPath: args[1])
let outPath = args[2]
guard let src = CIImage(contentsOf: inURL) else {
    FileHandle.standardError.write("cannot read input\n".data(using: .utf8)!)
    exit(2)
}
let handler = VNImageRequestHandler(ciImage: src, options: [:])
var obs: VNRectangleObservation?

if #available(macOS 13.0, *) {
    let req = VNDetectDocumentSegmentationRequest { r, _ in
        obs = (r.results as? [VNRectangleObservation])?.first
    }
    try? handler.perform([req])
}
if obs == nil {
    let req = VNDetectRectanglesRequest { r, _ in
        obs = (r.results as? [VNRectangleObservation])?.sorted { $0.confidence > $1.confidence }.first
    }
    req.maximumObservations = 1
    req.minimumConfidence = 0.2
    try? handler.perform([req])
}
let w = src.extent.width, h = src.extent.height
func P(_ p: CGPoint) -> CGPoint { CGPoint(x: p.x * w, y: p.y * h) }

guard let o = obs else {
    print("NO_DOCUMENT_DETECTED")
    let ctx = CIContext()
    try? ctx.writeJPEGRepresentation(of: src, to: URL(fileURLWithPath: outPath),
                                    colorSpace: CGColorSpaceCreateDeviceRGB(), options: [:])
    exit(0)
}
let f = CIFilter(name: "CIPerspectiveCorrection")!
f.setValue(src, forKey: kCIInputImageKey)
f.setValue(CIVector(cgPoint: P(o.topLeft)), forKey: "inputTopLeft")
f.setValue(CIVector(cgPoint: P(o.topRight)), forKey: "inputTopRight")
f.setValue(CIVector(cgPoint: P(o.bottomLeft)), forKey: "inputBottomLeft")
f.setValue(CIVector(cgPoint: P(o.bottomRight)), forKey: "inputBottomRight")
guard let out = f.outputImage else { exit(3) }
let ctx = CIContext()
try ctx.writeJPEGRepresentation(of: out, to: URL(fileURLWithPath: outPath),
                                colorSpace: CGColorSpaceCreateDeviceRGB(), options: [:])
print(String(format: "DETECTED confidence=%.3f out=%.0fx%.0f", o.confidence, out.extent.width, out.extent.height))
