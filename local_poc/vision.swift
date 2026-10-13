import Foundation
import Vision

struct Label: Encodable {
    let name: String
    let confidence: Float
}

struct Result: Encodable {
    let text: [String]
    let labels: [Label]
}

do {
    guard CommandLine.arguments.count == 2 else { throw NSError(domain: "input", code: 1) }
    let image = URL(fileURLWithPath: CommandLine.arguments[1])
    let ocr = VNRecognizeTextRequest()
    ocr.recognitionLevel = .accurate
    ocr.recognitionLanguages = ["zh-Hant", "zh-Hans", "en-US"]
    ocr.usesLanguageCorrection = true
    let classify = VNClassifyImageRequest()
    try VNImageRequestHandler(url: image).perform([ocr, classify])
    let result = Result(
        text: (ocr.results ?? []).compactMap { $0.topCandidates(1).first?.string }.prefix(30).map { String($0.prefix(200)) },
        labels: (classify.results ?? []).prefix(5).map { Label(name: $0.identifier, confidence: $0.confidence) }
    )
    let bytes = try JSONEncoder().encode(result)
    print(String(decoding: bytes, as: UTF8.self))
} catch {
    fputs("Vision analysis failed\n", stderr)
    exit(1)
}
