import Foundation
import AppKit
import CoreImage

// 裁一块 + 放大，方便肉眼看清楚。参数里的 y 从顶部算，符合直觉。
//   crop <in> <out> <x> <y> <w> <h> <scale>
let a = CommandLine.arguments
guard a.count >= 8, let x = Double(a[3]), let y = Double(a[4]),
      let w = Double(a[5]), let h = Double(a[6]), let s = Double(a[7]) else {
    FileHandle.standardError.write("usage: crop <in> <out> <x> <y> <w> <h> <scale>\n".data(using: .utf8)!)
    exit(1)
}
guard let url = URL(string: "file://" + a[1]), let img = CIImage(contentsOf: url) else { exit(2) }
let H = img.extent.height
let rect = CGRect(x: x, y: H - (y + h), width: w, height: h)   // CI 原点在左下
let out = img.cropped(to: rect).transformed(by: CGAffineTransform(scaleX: s, y: s))
let ctx = CIContext()
guard let cg = ctx.createCGImage(out, from: out.extent) else { exit(3) }
let rep = NSBitmapImageRep(cgImage: cg)
guard let d = rep.representation(using: .png, properties: [:]) else { exit(4) }
try d.write(to: URL(fileURLWithPath: a[2]))
print("cropped \(Int(w))x\(Int(h)) x\(s) -> \(Int(w*s))x\(Int(h*s))")
