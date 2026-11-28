import AppKit
#if canImport(Sparkle)
import Sparkle
#endif

@main
final class Launcher: NSObject, NSApplicationDelegate {
    private var link: String?
    private var runner: Process?
    private var otherWindows: [Process] = []
    private var queuedLink: String?
    private var started = false
    private var relaunching = false
    #if canImport(Sparkle)
    private var updates: SPUStandardUpdaterController?
    private var pendingCommand = Data()
    private var foundVersion: String?
    #endif
    private var toDesktop: FileHandle?
    private var fromDesktop: FileHandle?
    private var check: String?

    static func main() {
        let application = NSApplication.shared
        let delegate = Launcher()
        application.delegate = delegate
        application.setActivationPolicy(.accessory)
        withExtendedLifetime(delegate) { application.run() }
    }

    func application(_ application: NSApplication, open urls: [URL]) {
        guard let url = urls.last, url.scheme == "gitdoc", url.host == "open" else { return }
        if started {
            if check == "check" { queuedLink = url.absoluteString }
            else { launchDesktop(url.absoluteString, primary: false) }
        } else {
            link = url.absoluteString
            launch()
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        DispatchQueue.main.async { self.launch() }
    }

    private func launch() {
        guard !started else { return }
        started = true
        let initial = CommandLine.arguments.dropFirst().first
        launchDesktop(link ?? initial ?? "gitdoc://open/", primary: true)
    }

    private func launchDesktop(_ destination: String, primary: Bool) {
        let packaged = Bundle.main.bundleURL.appendingPathComponent("Contents/Resources/app")
        let root = FileManager.default.fileExists(atPath: packaged.appendingPathComponent("desktop/bridge.py").path)
            ? packaged : Bundle.main.bundleURL.deletingLastPathComponent().deletingLastPathComponent()
        let process = Process()
        process.executableURL = Bundle.main.bundleURL.appendingPathComponent("Contents/MacOS/gitdoc-desktop")
        process.arguments = [destination]
        process.currentDirectoryURL = root
        var environment = ProcessInfo.processInfo.environment
        if environment["DOCVERS_ROOT"] == nil { environment["DOCVERS_ROOT"] = root.path }
        environment.removeValue(forKey: "DOCVERS_SPARKLE_PIPE")
        if primary && enableUpdates(for: process) { environment["DOCVERS_SPARKLE_PIPE"] = "1" }
        process.environment = environment
        process.terminationHandler = { [weak self] process in
            DispatchQueue.main.async {
                guard let self else { return }
                if primary {
                    self.fromDesktop?.readabilityHandler = nil
                    self.toDesktop = nil
                    self.runner = nil
                } else {
                    self.otherWindows.removeAll { $0 === process }
                }
                if self.runner == nil && self.otherWindows.isEmpty && !self.relaunching {
                    NSApp.terminate(nil)
                }
            }
        }
        do {
            try process.run()
            if primary { runner = process }
            else { otherWindows.append(process) }
        } catch {
            let alert = NSAlert()
            alert.messageText = "GitDoc을 시작할 수 없습니다."
            alert.informativeText = error.localizedDescription
            alert.runModal()
            if primary { NSApp.terminate(nil) }
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if relaunching || (runner == nil && otherWindows.isEmpty) { return .terminateNow }
        return .terminateCancel
    }
}

#if canImport(Sparkle)
extension Launcher: SPUUpdaterDelegate {
    private func enableUpdates(for process: Process) -> Bool {
        let host = Bundle.main
        guard let feed = host.object(forInfoDictionaryKey: "SUFeedURL") as? String,
              URL(string: feed)?.scheme == "https",
              let key = host.object(forInfoDictionaryKey: "SUPublicEDKey") as? String,
              Data(base64Encoded: key)?.count == 32,
              !host.bundleURL.path.contains("/AppTranslocation/"),
              (try? host.bundleURL.resourceValues(forKeys: [.volumeIsReadOnlyKey]).volumeIsReadOnly) == false
        else { return false }

        let request = Pipe()
        let response = Pipe()
        process.standardOutput = request
        process.standardInput = response
        toDesktop = response.fileHandleForWriting
        fromDesktop = request.fileHandleForReading
        fromDesktop?.readabilityHandler = { [weak self] handle in
            let bytes = handle.availableData
            guard !bytes.isEmpty else {
                handle.readabilityHandler = nil
                return
            }
            DispatchQueue.main.async { self?.handle(bytes) }
        }
        return true
    }

    private func handle(_ bytes: Data) {
        pendingCommand.append(bytes)
        while let newline = pendingCommand.firstIndex(of: 10) {
            let command = String(data: pendingCommand.prefix(upTo: newline), encoding: .utf8)
            pendingCommand.removeSubrange(...newline)
            if let command, command == "check" || command == "info" {
                startCheck(command)
            }
        }
        if pendingCommand.count > 32 { pendingCommand.removeAll() }
    }

    private func reply(_ result: String) {
        guard runner?.isRunning == true else { return }
        try? toDesktop?.write(contentsOf: Data((result + "\n").utf8))
    }

    private func startCheck(_ command: String) {
        guard check == nil else { return }
        if command == "check" && otherWindows.contains(where: { $0.isRunning }) {
            reply("other-windows")
            return
        }
        if updates == nil {
            updates = SPUStandardUpdaterController(startingUpdater: false, updaterDelegate: self, userDriverDelegate: nil)
            updates?.startUpdater()
        }
        guard let controller = updates, controller.updater.canCheckForUpdates else {
            reply("error")
            return
        }
        foundVersion = nil
        check = command
        if command == "check" {
            NSApp.activate(ignoringOtherApps: true)
            controller.checkForUpdates(nil)
        } else {
            controller.updater.checkForUpdateInformation()
        }
    }

    func updater(_ updater: SPUUpdater, didFindValidUpdate item: SUAppcastItem) {
        let version = item.displayVersionString
        let parts = version.split(separator: ".", omittingEmptySubsequences: false)
        if parts.count == 3 && parts.allSatisfy({ !$0.isEmpty && $0.utf8.allSatisfy { (48...57).contains($0) } }) {
            foundVersion = version
        }
    }

    func updater(_ updater: SPUUpdater, didFinishUpdateCycleFor updateCheck: SPUUpdateCheck, error: Error?) {
        guard let completed = check else { return }
        check = nil
        if let version = foundVersion {
            reply("available " + version)
        } else if completed == "check" || ((error as NSError?)?.domain == SUSparkleErrorDomain
                                            && (error as NSError?)?.code == 1001) {
            reply("idle")
        } else {
            reply("error")
        }
        if let destination = queuedLink, !relaunching {
            queuedLink = nil
            launchDesktop(destination, primary: false)
        }
    }

    func updaterWillRelaunchApplication(_ updater: SPUUpdater) {
        // The desktop freezes editing during this user-initiated session.
        relaunching = true
        if let process = runner, process.isRunning {
            process.terminate()
            process.waitUntilExit()
        }
    }

}
#else
extension Launcher {
    private func enableUpdates(for process: Process) -> Bool { false }
}
#endif
