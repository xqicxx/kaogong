import Foundation
import Vision
import AppKit
import ImageIO

let args = CommandLine.arguments
let knownFlags: Set<String> = ["--json", "--plain"]
// 不能写死 args[1] 当图片路径：vision-ocr --json 图.png 会把 "--json" 当文件名
guard let imagePath = args.dropFirst().first(where: { !knownFlags.contains($0) }) else {
    print("usage: vision-ocr <image> [--json] [--plain]")
    exit(1)
}
let jsonMode = args.contains("--json")
guard let image = NSImage(contentsOfFile: imagePath),
      let source = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("cannot load image\n".data(using: .utf8)!); exit(2)
}
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hans", "en-US"]
request.usesLanguageCorrection = !args.contains("--plain")

// 手机拍的照片常带 EXIF 旋转标记：不告诉 Vision 就会读到横躺的图，OCR 直接烂掉。
// CGImage 本身不带这个信息，得从文件里取。
func exifOrientation(of path: String) -> CGImagePropertyOrientation {
    guard let imageSource = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
          let properties = CGImageSourceCopyPropertiesAtIndex(imageSource, 0, nil) as? [CFString: Any],
          let raw = properties[kCGImagePropertyOrientation] as? UInt32,
          let orientation = CGImagePropertyOrientation(rawValue: raw) else { return .up }
    return orientation
}

let handler = VNImageRequestHandler(cgImage: source,
                                     orientation: exifOrientation(of: imagePath), options: [:])
let started = Date()
do {
    try handler.perform([request])
} catch {
    FileHandle.standardError.write("Vision 识别失败：\(error.localizedDescription)\n".data(using: .utf8)!)
    exit(3)
}
let elapsed = Date().timeIntervalSince(started)
guard let observations = request.results else {
    FileHandle.standardError.write("Vision 没返回任何结果\n".data(using: .utf8)!)
    exit(3)
}

struct TextLine {
    let text: String
    let left: CGFloat
    let top: CGFloat
    let height: CGFloat
    let width: CGFloat
    let confidence: Float
    let alternates: [String]
}

var lines: [TextLine] = []
for observation in observations {
    guard let candidate = observation.topCandidates(1).first else { continue }
    // 前 3 个候选：top1 与 top2 不同 → 识别器自己也不确定，这是比置信度阈值更好的校对信号
    let alternates = observation.topCandidates(3).dropFirst().map { $0.string }
    let box = observation.boundingBox   // 归一化，原点左下
    lines.append(TextLine(text: candidate.string, left: box.minX, top: 1 - box.maxY,
                          height: box.height, width: box.width,
                          confidence: candidate.confidence, alternates: Array(alternates)))
}

// 阅读顺序：先按纵向分行（重叠视为同一行），行内按横向
lines.sort { $0.top < $1.top }
var rows: [[TextLine]] = []
var currentRow: [TextLine] = []
for line in lines {
    if let first = currentRow.first, line.top - first.top > line.height * 0.6 {
        rows.append(currentRow); currentRow = [line]
    } else { currentRow.append(line) }
}
if !currentRow.isEmpty { rows.append(currentRow) }

let heights = lines.map { $0.height }.sorted()
let medianHeight = heights.isEmpty ? 0.01 : heights[heights.count / 2]
let plainText: [String] = rows.map { row in
    let sorted = row.sorted { $0.left < $1.left }
    let text = sorted.map { $0.text }.joined(separator: "  ")
    let tallest = sorted.map { $0.height }.max() ?? 0
    // 行高明显大于中位数的当标题
    return (tallest > medianHeight * 1.25 && text.count < 40) ? ("## " + text) : text
}

if jsonMode {
    // 这些键名是给 vision2md.py 用的契约，不能改名
    let records = lines.map {
        ["text": $0.text, "x": Double($0.left), "y": Double($0.top), "h": Double($0.height),
         "w": Double($0.width), "conf": Double($0.confidence), "alts": $0.alternates] as [String: Any]
    }
    let payload: [String: Any] = ["seconds": elapsed, "lines": records]
    do {
        let encoded = try JSONSerialization.data(withJSONObject: payload,
                                                 options: [.prettyPrinted, .withoutEscapingSlashes, .sortedKeys])
        guard let json = String(data: encoded, encoding: .utf8) else { exit(4) }
        print(json)
    } catch {
        // 原来这里是 try! —— 序列化失败就是 SIGILL，调用方只看到崩溃
        FileHandle.standardError.write("JSON 序列化失败：\(error)\n".data(using: .utf8)!)
        exit(4)
    }
} else {
    print("### meta: " + String(format: "%.2fs  %d 行  %dx%d", elapsed, lines.count, source.width, source.height))
    print(plainText.joined(separator: "\n"))
}
