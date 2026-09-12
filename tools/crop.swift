import Foundation
import AppKit
import CoreImage

// 裁一块 + 放大，方便肉眼看清楚。参数里的 y 从顶部算，符合直觉。
//   crop <in> <out> <x> <y> <宽> <高> <scale>
let arguments = CommandLine.arguments
guard arguments.count >= 8,
      let originX = Double(arguments[3]), let originY = Double(arguments[4]),
      let regionWidth = Double(arguments[5]), let regionHeight = Double(arguments[6]),
      let scaleFactor = Double(arguments[7]) else {
    FileHandle.standardError.write("usage: crop <in> <out> <x> <y> <w> <h> <scale>\n".data(using: .utf8)!)
    exit(1)
}
// 不能手工拼 "file://" + path：路径里有空格、中文、# ? % 或相对路径时都会坏
guard let image = CIImage(contentsOf: URL(fileURLWithPath: arguments[1])) else {
    FileHandle.standardError.write("读不到图片：\(arguments[1])\n".data(using: .utf8)!)
    exit(2)
}
let imageHeight = image.extent.height
let region = CGRect(x: originX, y: imageHeight - (originY + regionHeight),
                    width: regionWidth, height: regionHeight)   // CI 原点在左下
let scaled = image.cropped(to: region)
    .transformed(by: CGAffineTransform(scaleX: scaleFactor, y: scaleFactor))
let context = CIContext()
guard let cropped = context.createCGImage(scaled, from: scaled.extent) else {
    FileHandle.standardError.write("裁剪失败（坐标超界？x=\(originX) y=\(originY) w=\(regionWidth) h=\(regionHeight)）\n".data(using: .utf8)!)
    exit(3)
}
let bitmap = NSBitmapImageRep(cgImage: cropped)
guard let encoded = bitmap.representation(using: .png, properties: [:]) else { exit(4) }
do {
    try encoded.write(to: URL(fileURLWithPath: arguments[2]))
} catch {
    FileHandle.standardError.write("写不出去：\(arguments[2])（\(error.localizedDescription)）\n".data(using: .utf8)!)
    exit(5)
}
print("cropped \(Int(regionWidth))x\(Int(regionHeight)) x\(scaleFactor) -> \(Int(regionWidth * scaleFactor))x\(Int(regionHeight * scaleFactor))")
