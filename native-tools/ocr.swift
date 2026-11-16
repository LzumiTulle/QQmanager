import Foundation
import Vision
import ImageIO

do {
    guard CommandLine.arguments.count == 2 else { throw NSError(domain: "input", code: 1) }
    let url = URL(fileURLWithPath: CommandLine.arguments[1])
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
          let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString:Any],
          let width = properties[kCGImagePropertyPixelWidth] as? Int,
          let height = properties[kCGImagePropertyPixelHeight] as? Int,
          width > 0, height > 0, width * height <= 40000000 else { throw NSError(domain: "image", code: 2) }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    request.usesLanguageCorrection = true
    try VNImageRequestHandler(url: url, options: [:]).perform([request])
    // The default reading order can concatenate whole columns. Keep horizontal
    // neighbours together without guessing the scope of merged table cells.
    let observations = (request.results ?? []).sorted { $0.boundingBox.midY > $1.boundingBox.midY }
    var rows: [[VNRecognizedTextObservation]] = []
    for observation in observations {
        if let last = rows.last, let anchor = last.first,
           abs(anchor.boundingBox.midY - observation.boundingBox.midY) < min(anchor.boundingBox.height, observation.boundingBox.height) * 0.55 {
            rows[rows.count - 1].append(observation)
        } else { rows.append([observation]) }
    }
    let text = rows.map { row in
        row.sorted { $0.boundingBox.minX < $1.boundingBox.minX }
            .compactMap { $0.topCandidates(1).first?.string }.joined(separator: " | ")
    }.joined(separator: "\n")
    let data = try JSONSerialization.data(withJSONObject: ["text": text])
    FileHandle.standardOutput.write(data)
} catch { let e=error as NSError; FileHandle.standardError.write(Data("\(e.domain):\(e.code)".utf8)); exit(1) }
