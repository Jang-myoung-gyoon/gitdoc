import CryptoKit
import Foundation

let args = CommandLine.arguments
guard args.count == 4,
      let raw = Data(base64Encoded: args[1].trimmingCharacters(in: .whitespacesAndNewlines)),
      let key = try? Curve25519.Signing.PublicKey(rawRepresentation: raw) else {
    FileHandle.standardError.write(Data("Invalid Sparkle public key. Use the exported base64 public key.\n".utf8))
    exit(1)
}
guard let challenge = try? Data(contentsOf: URL(fileURLWithPath: args[2])),
      let signature = Data(base64Encoded: args[3]),
      key.isValidSignature(signature, for: challenge) else {
    FileHandle.standardError.write(Data("The configured Sparkle keys are not a matching pair.\n".utf8))
    exit(1)
}
