import Foundation
import AppKit
import CoreImage
import CoreGraphics

// deink —— 处理照片里的手写笔迹。
//
// 两种用途：
//   默认          把彩色笔迹抹白（去手写，给 OCR 用）
//   --marks MODE  只留笔迹、其余抹白（把手写批注单独抠出来看）
//
// MODE 决定认哪种笔迹：
//   red    红墨水      R - max(G,B) > 阈值
//   blue   蓝墨水      B - max(R,G) > 阈值
//   any    任何彩色墨  max(R,G,B) - min(R,G,B) > 阈值
//   （默认 any —— 红笔蓝笔墨绿笔都算；黑白印刷体是灰阶、饱和度低，不会被选中）
//
// ⚠️ 黑笔分不开：黑色手写与印刷体都是灰阶，颜色判据无能为力。
//    黑笔的叉只能靠「读原图」或「OCR 里的低置信度乱码行」来发现，见 kaogong-classify skill。
let args = CommandLine.arguments
var marksMode = ""
var inkMode = "any"
if let index = args.firstIndex(of: "--marks"), index + 1 < args.count {
    marksMode = args[index + 1]
}
if let index = args.firstIndex(of: "--ink"), index + 1 < args.count {
    inkMode = args[index + 1]
}
if args.contains("--red-only") { marksMode = "red" }      // 兼容旧写法
guard args.count >= 3 else {
    FileHandle.standardError.write(Data("usage: deink <in> <out> [--thresh N] [--marks red|blue|any] [--ink red|blue|any]\n".utf8))
    exit(1)
}
var thresh = 38
if let index = args.firstIndex(of: "--thresh"), index + 1 < args.count {
    if let value = Int(args[index + 1]) {
        thresh = value
    } else {
        FileHandle.standardError.write(Data(" --thresh 要跟整数，收到 \(args[index + 1])\n".utf8))
        exit(1)
    }
}

/// 这个像素算不算「彩色笔迹」。黑白印刷体三通道接近，饱和度低。
func isColoredInk(red: Int, green: Int, blue: Int, mode: String) -> Bool {
    switch mode {
    case "red":
        return red - max(green, blue) > thresh
    case "blue":
        return blue - max(red, green) > thresh
    default:
        return max(red, max(green, blue)) - min(red, min(green, blue)) > thresh
    }
}

let source = NSImage(contentsOfFile: args[1])
let cg = source?.cgImage(forProposedRect: nil, context: nil, hints: nil)
if cg == nil {
    FileHandle.standardError.write(Data("读不到图片：\(args[1])\n".utf8))
    exit(2)
}

let width = cg!.width, height = cg!.height
var buf = [UInt8](repeating: 0, count: width * height * 4)
guard let bctx = CGContext(data: &buf, width: width, height: height, bitsPerComponent: 8,
                           bytesPerRow: width * 4, space: CGColorSpaceCreateDeviceRGB(),
                           bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { exit(5) }
// CGContext.draw 按左下原点摆，makeImage 又按第 0 行在上解释，两次抵消 → 不要再翻转
bctx.draw(cg!, in: CGRect(x: 0, y: 0, width: width, height: height))

let extracting = !marksMode.isEmpty
let mode = extracting ? marksMode : inkMode
var hitCount = 0
for index in stride(from: 0, to: buf.count, by: 4) {
    let red = Int(buf[index]), green = Int(buf[index + 1]), blue = Int(buf[index + 2])
    let isInk = isColoredInk(red: red, green: green, blue: blue, mode: mode)
    if extracting {
        if isInk {
            buf[index] = 0; buf[index + 1] = 0; buf[index + 2] = 0      // 笔迹转黑，好辨认
            hitCount += 1
        } else {
            buf[index] = 255; buf[index + 1] = 255; buf[index + 2] = 255
        }
    } else if isInk {
        buf[index] = 255; buf[index + 1] = 255; buf[index + 2] = 255
        hitCount += 1
    }
}
guard let outCG = bctx.makeImage() else { exit(6) }
let rep = NSBitmapImageRep(cgImage: outCG)
guard let data = rep.representation(using: .jpeg, properties: [.compressionFactor: 0.92]) else { exit(7) }
do {
    try data.write(to: URL(fileURLWithPath: args[2]))
} catch {
    FileHandle.standardError.write(Data("写不出去：\(args[2])（\(error.localizedDescription)）\n".utf8))
    exit(8)
}
let ratio = 100.0 * Double(hitCount) / Double(width * height)
if extracting {
    print(String(format: "%@ 笔迹层：占 %.2f%%", marksMode, ratio))
    if ratio < 0.05 {
        FileHandle.standardError.write(Data("几乎没扫到彩色笔迹 —— 这页可能是黑笔批改，或没有批注\n".utf8))
    }
} else {
    print(String(format: "去%@笔迹 %.2f%%", mode, ratio))
}
