import Foundation
import AppKit
import CoreImage
import CoreGraphics

// 拍照预处理：去红笔。
//   红墨水 R 高、G/B 低；印刷黑字三通道都低 → 判据 R - max(G,B) > 阈值
//
// 匀光（--flatten）分支已删除：CIDivideBlendMode 的接线是反的（blur/ci），
// 而且实测会把浅色字一起洗掉（真词命中 13/16 → 1/16）。留着只会被误用。
let args = CommandLine.arguments
guard args.count >= 3 else {
    FileHandle.standardError.write("usage: deink <in> <out> [--thresh N]\n".data(using: .utf8)!)
    exit(1)
}
var thresh = 38
if let index = args.firstIndex(of: "--thresh"), index + 1 < args.count {
    if let value = Int(args[index + 1]) {
        thresh = value
    } else {
        FileHandle.standardError.write(" --thresh 要跟整数，收到 \(args[index + 1])\n".data(using: .utf8)!)
        exit(1)
    }
}

// 读图：这段一度被误删，导致 cg 未定义
let source = NSImage(contentsOfFile: args[1])
let cg = source?.cgImage(forProposedRect: nil, context: nil, hints: nil)
if cg == nil {
    FileHandle.standardError.write("读不到图片：\(args[1])\n".data(using: .utf8)!)
    exit(2)
}

let w = cg!.width, h = cg!.height
var buf = [UInt8](repeating: 0, count: w * h * 4)
guard let bctx = CGContext(data: &buf, width: w, height: h, bitsPerComponent: 8,
                           bytesPerRow: w * 4, space: CGColorSpaceCreateDeviceRGB(),
                           bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { exit(5) }
// CGContext.draw 按左下原点摆，makeImage 又按第 0 行在上解释，两次抵消 → 不要再翻转
bctx.draw(cg!, in: CGRect(x: 0, y: 0, width: w, height: h))

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
print(String(format: "deinked %.2f%% of pixels", 100.0 * Double(n) / Double(w * h)))
