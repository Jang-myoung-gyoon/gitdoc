import CryptoKit
import Foundation

let seed = FileHandle.standardInput.readDataToEndOfFile()
guard CommandLine.arguments.count == 2,
      let encoded = String(data: seed, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines),
      let raw = Data(base64Encoded: encoded), raw.count == 32,
      let key = try? Curve25519.Signing.PrivateKey(rawRepresentation: raw),
      key.publicKey.rawRepresentation.base64EncodedString() == CommandLine.arguments[1] else {
    FileHandle.standardError.write(Data("Sparkle public key and signing seed do not match.\n".utf8))
    exit(1)
}
