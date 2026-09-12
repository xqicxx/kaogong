import Foundation
import AppKit
import CoreImage

// 裁一块 + 放大，方便肉眼看清楚。参数里的 y 从顶部算，符合直觉。
//   crop <in> <out> <x> <y> <宽> <高> <scale>
//
// 参数一律先校验再动图：NaN / inf / 0 / 负数会让 cropped() 静默补透明边，
// 或者申请一块巨大内存直接 OOM —— 两种都不是“报错”，而是“看起来成功的错”。
let arguments = CommandLine.arguments
guard arguments.count >= 8,
      let rawX = Double(arguments[3]), let rawY = Double(arguments[4]),
      let rawWidth = Double(arguments[5]), let rawHeight = Double(arguments[6]),
      let rawScale = Double(arguments[7]) else {
    FileHandle.standardError.write(Data("usage: crop <in> <out> <x> <y> <w> <h> <scale>\n".utf8))
    exit(1)
}
let params = [rawX, rawY, rawWidth, rawHeight, rawScale]
if params.contains(where: { !$0.isFinite }) || rawWidth <= 0 || rawHeight <= 0 || rawScale <= 0 {
    FileHandle.standardError.write(Data("参数必须是有限正数\n".utf8))
    exit(1)
}
let maxPixels = 80_000_000.0                     // 80MP 上限，防 OOM
let outputPixels = rawWidth * rawHeight * rawScale * rawScale
if !outputPixels.isFinite || outputPixels > maxPixels {
    FileHandle.standardError.write(Data("裁剪后像素数超过上限：\(Int(outputPixels))\n".utf8))
    exit(1)
}

// 不能手工拼 "file://" + path：路径里有空格、中文、# ? % 或相对路径时都会坏
guard let image = CIImage(contentsOf: URL(fileURLWithPath: arguments[1])) else {
    FileHandle.standardError.write(Data("读不到图片：\(arguments[1])\n".utf8))
    exit(2)
}

// 夹到图片范围内：越界时 cropped() 不报错，只补透明边
let clampedWidth = min(rawWidth, image.extent.width)
let clampedHeight = min(rawHeight, image.extent.height)
let originX = max(0, min(rawX, image.extent.width - clampedWidth))
let originY = max(0, min(rawY, image.extent.height - clampedHeight))
let region = CGRect(x: originX,
                    y: image.extent.height - (originY + clampedHeight),
                    width: clampedWidth, height: clampedHeight)   // CI 原点在左下
let scaled = image.cropped(to: region)
    .transformed(by: CGAffineTransform(scaleX: rawScale, y: rawScale))
let context = CIContext()
guard let cropped = context.createCGImage(scaled, from: scaled.extent) else {
    FileHandle.standardError.write(Data("裁剪失败（x=\(originX) y=\(originY) w=\(clampedWidth) h=\(clampedHeight)）\n".utf8))
    exit(3)
}
let bitmap = NSBitmapImageRep(cgImage: cropped)
guard let encoded = bitmap.representation(using: .png, properties: [:]) else { exit(4) }
do {
    try encoded.write(to: URL(fileURLWithPath: arguments[2]))
} catch {
    FileHandle.standardError.write(Data("写不出去：\(arguments[2])（\(error.localizedDescription)）\n".utf8))
    exit(5)
}
print("cropped \(Int(clampedWidth))x\(Int(clampedHeight)) x\(rawScale) -> \(Int(clampedWidth * rawScale))x\(Int(clampedHeight * rawScale))")
