import Foundation
import AppKit
import CoreImage
import CoreGraphics

// 拍照预处理：匀光（去阴影） + 去红笔。
//   --flatten  用「原图 ÷ 大半径模糊」压平照明不均（页边阴影、手影）
//   去红笔     红墨水 R 高、G/B 低；印刷黑字三通道都低 → 判据 R - max(G,B) > 阈值
let args = CommandLine.arguments
guard args.count >= 3 else {
    FileHandle.standardError.write("usage: deink <in> <out> [--thresh N] [--flatten] [--radius N]\n".data(using: .utf8)!)
    exit(1)
}
var thresh = 38
if let i = args.firstIndex(of: "--thresh"), i + 1 < args.count, let v = Int(args[i + 1]) { thresh = v }
var radius = 48.0
if let i = args.firstIndex(of: "--radius"), i + 1 < args.count, let v = Double(args[i + 1]) { radius = v }
let flatten = args.contains("--flatten")

guard let src = NSImage(contentsOfFile: args[1]),
      let cg0 = src.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("cannot load\n".data(using: .utf8)!); exit(2)
}
var cg = cg0
if flatten {
    let ci = CIImage(cgImage: cg0)
    guard let blur = CIFilter(name: "CIGaussianBlur",
                              parameters: [kCIInputImageKey: ci, kCIInputRadiusKey: radius])?.outputImage,
          let div = CIFilter(name: "CIDivideBlendMode",
                             parameters: [kCIInputImageKey: ci, kCIInputBackgroundImageKey: blur])?.outputImage else { exit(3) }
    let ctx = CIContext()
    // CIContext 负责方向，不会上下颠倒
    guard let out = ctx.createCGImage(div, from: ci.extent) else { exit(4) }
    cg = out
}

let w = cg.width, h = cg.height
var buf = [UInt8](repeating: 0, count: w * h * 4)
guard let bctx = CGContext(data: &buf, width: w, height: h, bitsPerComponent: 8,
                           bytesPerRow: w * 4, space: CGColorSpaceCreateDeviceRGB(),
                           bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { exit(5) }
// CGContext.draw 按左下原点摆，makeImage 又按第 0 行在上解释，两次抵消 → 不要再翻转
bctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))

var n = 0
for i in stride(from: 0, to: buf.count, by: 4) {
    let r = Int(buf[i]), g = Int(buf[i + 1]), b = Int(buf[i + 2])
    if r - max(g, b) > thresh {
        buf[i] = 255; buf[i + 1] = 255; buf[i + 2] = 255; n += 1
    }
}
guard let outCG = bctx.makeImage() else { exit(6) }
let rep = NSBitmapImageRep(cgImage: outCG)
guard let data = rep.representation(using: .jpeg, properties: [.compressionFactor: 0.92]) else { exit(7) }
try data.write(to: URL(fileURLWithPath: args[2]))
print(String(format: "flatten=%@ deinked %.2f%%", flatten ? "on" : "off", 100.0 * Double(n) / Double(w * h)))
