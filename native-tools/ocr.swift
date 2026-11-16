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
    let lines: [[String:Any]] = observations.compactMap { o in
        guard let s = o.topCandidates(1).first?.string else { return nil }
        return ["text":s,"x":o.boundingBox.minX,"y":1-o.boundingBox.maxY,
                "w":o.boundingBox.width,"h":o.boundingBox.height]
    }
    var output: [String:Any] = ["text":text,"lines":lines]
    let dayNames = ["星期一","星期二","星期三","星期四","星期五","星期六","星期日"]
    let headers = dayNames.compactMap { name in observations.first { $0.topCandidates(1).first?.string == name } }
    if headers.count == 7 && zip(headers,headers.dropFirst()).allSatisfy({ $0.boundingBox.midX < $1.boundingBox.midX }) {
        let top = headers.map { $0.boundingBox.minY }.min()!
        let footers = observations.filter { ($0.topCandidates(1).first?.string ?? "").contains("其他课程") && $0.boundingBox.maxY < top }
        let bottom = footers.map { $0.boundingBox.maxY }.max() ?? 0
        var columns: [[String:Any]] = []
        for i in 0..<7 {
            let center = headers[i].boundingBox.midX
            let left = i == 0 ? center-(headers[1].boundingBox.midX-center)/2 : (headers[i-1].boundingBox.midX+center)/2
            let right = i == 6 ? center+(center-headers[5].boundingBox.midX)/2 : (headers[i+1].boundingBox.midX+center)/2
            let column = VNRecognizeTextRequest()
            column.recognitionLevel = .accurate
            column.recognitionLanguages = ["zh-Hans","en-US"]
            column.usesLanguageCorrection = true
            column.regionOfInterest = CGRect(x:max(0,left),y:bottom,width:min(1,right)-max(0,left),height:top-bottom)
            try VNImageRequestHandler(url:url,options:[:]).perform([column])
            let entries = (column.results ?? []).sorted { $0.boundingBox.midY > $1.boundingBox.midY }
            columns.append(["weekday":i+1,"text":entries.compactMap { $0.topCandidates(1).first?.string }.joined(separator:"\n")])
        }
        output["columns"] = columns
        output["footer"] = observations.filter { $0.boundingBox.midY <= bottom }.compactMap { $0.topCandidates(1).first?.string }.joined(separator:"\n")
    }
    let data = try JSONSerialization.data(withJSONObject: output)
    FileHandle.standardOutput.write(data)
} catch { let e=error as NSError; FileHandle.standardError.write(Data("\(e.domain):\(e.code)".utf8)); exit(1) }
