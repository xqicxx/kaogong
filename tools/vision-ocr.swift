import Foundation
import Vision
import AppKit

let args = CommandLine.arguments
guard args.count >= 2 else { print("usage: vision-ocr <image> [--json]"); exit(1) }
let jsonMode = args.contains("--json")
guard let img = NSImage(contentsOfFile: args[1]),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("cannot load image\n".data(using: .utf8)!); exit(2)
}
let req = VNRecognizeTextRequest()
req.recognitionLevel = .accurate
req.recognitionLanguages = ["zh-Hans", "en-US"]
req.usesLanguageCorrection = !args.contains("--plain")
let handler = VNImageRequestHandler(cgImage: cg, options: [:])
let t0 = Date()
try? handler.perform([req])
let dt = Date().timeIntervalSince(t0)
guard let obs = req.results else { exit(3) }

struct Line { let text: String; let x: CGFloat; let y: CGFloat; let h: CGFloat; let w: CGFloat; let conf: Float; let alts: [String] }
var lines: [Line] = []
for o in obs {
    guard let c = o.topCandidates(1).first else { continue }
    // 前 3 个候选：top1 与 top2 不同 → 识别器自己也不确定，这是比置信度阈值更好的校对信号
    let alts = o.topCandidates(3).dropFirst().map { $0.string }
    let bb = o.boundingBox   // 归一化，原点左下
    lines.append(Line(text: c.string, x: bb.minX, y: 1 - bb.maxY, h: bb.height, w: bb.width, conf: c.confidence, alts: Array(alts)))
}
// 阅读顺序：先按 y 分行（重叠视为同一行），行内按 x
lines.sort { $0.y < $1.y }
var rows: [[Line]] = []
var cur: [Line] = []
for l in lines {
    if let first = cur.first, l.y - first.y > l.h * 0.6 {
        rows.append(cur); cur = [l]
    } else { cur.append(l) }
}
if !cur.isEmpty { rows.append(cur) }

let heights = lines.map { $0.h }.sorted()
let medH = heights.isEmpty ? 0.01 : heights[heights.count / 2]
let out: [String] = rows.map { r in
    let sorted = r.sorted { $0.x < $1.x }
    let text = sorted.map { $0.text }.joined(separator: "  ")
    let h = sorted.map { $0.h }.max() ?? 0
    // 行高明显大于中位数的当标题
    return (h > medH * 1.25 && text.count < 40) ? ("## " + text) : text
}
if jsonMode {
    let lns = lines.map { ["text": $0.text, "x": Double($0.x), "y": Double($0.y), "h": Double($0.h), "conf": Double($0.conf), "alts": $0.alts] as [String: Any] }
    let payload: [String: Any] = ["seconds": dt, "lines": lns]
    let d = try! JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .withoutEscapingSlashes, .sortedKeys])
    print(String(data: d, encoding: .utf8)!)
} else {
    print("### meta: " + String(format: "%.2fs  %d 行  %dx%d", dt, lines.count, cg.width, cg.height))
    print(out.joined(separator: "\n"))
}
