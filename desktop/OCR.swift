import Foundation
import Vision

struct Glyph: Encodable {
    let text: String
    let box: [Double]

    func encode(to encoder: Encoder) throws {
        var values = encoder.unkeyedContainer()
        try values.encode(text)
        try values.encode(box)
    }
}

@main
struct OCR {
    static func main() throws {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        let supported = try request.supportedRecognitionLanguages()
        request.recognitionLanguages = ["ko-KR", "en-US"].filter(supported.contains)
        let image = URL(fileURLWithPath: CommandLine.arguments[1])
        try VNImageRequestHandler(url: image).perform([request])
        let lines = (request.results ?? []).compactMap { observation -> [Glyph]? in
            guard let candidate = observation.topCandidates(1).first else { return nil }
            let text = candidate.string
            let line = observation.boundingBox
            let advance = line.width / Double(max(1, text.count))
            return text.indices.enumerated().map { index, start in
                let end = text.index(after: start)
                let fallback = CGRect(x: line.minX + Double(index) * advance, y: line.minY,
                                      width: advance, height: line.height)
                let measured = (try? candidate.boundingBox(for: start..<end))?.boundingBox
                let box = measured.flatMap { $0.isEmpty ? nil : $0 } ?? fallback
                return Glyph(text: String(text[start..<end]), box: [
                    box.minX, 1 - box.maxY, box.maxX, 1 - box.minY
                ])
            }
        }
        FileHandle.standardOutput.write(try JSONEncoder().encode(lines))
    }
}
