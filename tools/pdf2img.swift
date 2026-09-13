import Foundation
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

// pdf2img —— 把 PDF 某几页栅格化成 PNG（把讲义当“照片”喂进 OCR 流水线）。
//   pdf2img <in.pdf> <out前缀> <起始页0基> <张数> [缩放]
// 只用 CoreGraphics，没装 poppler 也能用。
let args = CommandLine.arguments
guard args.count >= 5, let startPage = Int(args[3]), let count = Int(args[4]) else {
    FileHandle.standardError.write(Data("usage: pdf2img <in.pdf> <outPrefix> <startPage> <count> [scale]\n".utf8))
    exit(1)
}
let scale = args.count >= 6 ? (Double(args[5]) ?? 2.0) : 2.0
let url = URL(fileURLWithPath: args[1])
guard let doc = CGPDFDocument(url as CFURL) else {
    FileHandle.standardError.write(Data("打不开 PDF：\(args[1])\n".utf8))
    exit(2)
}
let total = doc.numberOfPages
var written = 0
for offset in 0..<count {
    let pageNumber = startPage + offset + 1            // CGPDFDocument 从 1 开始
    guard pageNumber >= 1, pageNumber <= total, let page = doc.page(at: pageNumber) else { continue }
    let box = page.getBoxRect(.mediaBox)
    let width = Int(box.width * scale), height = Int(box.height * scale)
    guard let ctx = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
                              bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                              bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { exit(3) }
    ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
    ctx.fill(CGRect(x: 0, y: 0, width: width, height: height))
    ctx.scaleBy(x: scale, y: scale)
    ctx.drawPDFPage(page)
    guard let image = ctx.makeImage() else { continue }
    let outPath = "\(args[2])-p\(pageNumber).png"
    guard let dest = CGImageDestinationCreateWithURL(URL(fileURLWithPath: outPath) as CFURL,
                                                     UTType.png.identifier as CFString, 1, nil) else { continue }
    CGImageDestinationAddImage(dest, image, nil)
    if CGImageDestinationFinalize(dest) { written += 1; print("  \(outPath)  \(width)x\(height)") }
}
print("  共写出 \(written) 页（PDF 总页数 \(total)）")
