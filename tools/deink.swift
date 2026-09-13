import Foundation
import AppKit
import CoreImage
import CoreGraphics

// 拍照预处理：去红笔。
//   红墨水 R 高、G/B 低；印刷黑字三通道都低 → 判据 R - max(G,B) > 阈值
//
// 匀光（--flatten）分支已删除：CIDivideBlendMode 的接线是反的（blur/ci），
// 而且实测会把浅色字一起洗掉（真词命中 13/16 → 1/16）。留着只会被误用。
// deink —— 把照片里的红色像素挑出来处理。
//
// 两种模式：
//   正常     把红色抹白（去红笔，给 OCR 用）
//   --red-only  只留红色、其余抹白（把用户的手写批注单独抠出来看）
//               —— 判对错就靠它：红笔的 ✓ ✗ 圈划和订正答案都在这一层
let args = CommandLine.arguments
let redOnly = args.contains("--red-only")
guard args.count >= 3 else {
    FileHandle.standardError.write("usage: deink <in> <out> [--thresh N] [--red-only]\n".data(using: .utf8)!)
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

let width = cg!.width, height = cg!.height
var buf = [UInt8](repeating: 0, count: width * height * 4)
guard let bctx = CGContext(data: &buf, width: width, height: height, bitsPerComponent: 8,
                           bytesPerRow: width * 4, space: CGColorSpaceCreateDeviceRGB(),
                           bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { exit(5) }
// CGContext.draw 按左下原点摆，makeImage 又按第 0 行在上解释，两次抵消 → 不要再翻转
bctx.draw(cg!, in: CGRect(x: 0, y: 0, width: width, height: height))

var redCount = 0
for index in stride(from: 0, to: buf.count, by: 4) {
    let red = Int(buf[index]), green = Int(buf[index + 1]), blue = Int(buf[index + 2])
    let isRed = red - max(green, blue) > thresh
    if redOnly {
        if isRed {
            buf[index] = 0; buf[index + 1] = 0; buf[index + 2] = 0     // 红 → 黑，好辨认
            redCount += 1
        } else {
            buf[index] = 255; buf[index + 1] = 255; buf[index + 2] = 255
        }
    } else if isRed {
        buf[index] = 255; buf[index + 1] = 255; buf[index + 2] = 255
        redCount += 1
    }
}
guard let outCG = bctx.makeImage() else { exit(6) }
let rep = NSBitmapImageRep(cgImage: outCG)
guard let data = rep.representation(using: .jpeg, properties: [.compressionFactor: 0.92]) else { exit(7) }
try data.write(to: URL(fileURLWithPath: args[2]))
let ratio = 100.0 * Double(redCount) / Double(width * height)
if redOnly {
    print(String(format: "red-only: 红色像素占 %.2f%%", ratio))
    if ratio < 0.05 {
        FileHandle.standardError.write(Data("几乎没扫到红色 —— 这页大概没有红笔批注\n".utf8))
    }
} else {
    print(String(format: "deinked %.2f%% of pixels", ratio))
}
