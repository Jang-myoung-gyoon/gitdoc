import AppKit

@main
final class Launcher: NSObject, NSApplicationDelegate {
    private var link: String?
    private var started = false

    static func main() {
        let application = NSApplication.shared
        let delegate = Launcher()
        application.delegate = delegate
        application.setActivationPolicy(.accessory)
        application.run()
    }

    func application(_ application: NSApplication, open urls: [URL]) {
        if let url = urls.last, url.scheme == "gitdoc", url.host == "open" {
            link = url.absoluteString
        }
        launch()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        DispatchQueue.main.async { self.launch() }
    }

    private func launch() {
        guard !started else { return }
        started = true
        let root = Bundle.main.bundleURL.deletingLastPathComponent().deletingLastPathComponent()
        let runner = Process()
        runner.executableURL = Bundle.main.bundleURL.appendingPathComponent("Contents/MacOS/gitdoc-desktop")
        runner.arguments = [link ?? "gitdoc://open/"]
        runner.currentDirectoryURL = root
        runner.environment = ProcessInfo.processInfo.environment.merging(
            ["DOCVERS_ROOT": root.path]) { existing, _ in existing }
        do { try runner.run() }
        catch {
            let alert = NSAlert()
            alert.messageText = "GitDoc을 시작할 수 없습니다."
            alert.informativeText = error.localizedDescription
            alert.runModal()
        }
        NSApp.terminate(nil)
    }
}
