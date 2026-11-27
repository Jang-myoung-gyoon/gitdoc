import AppKit
import CryptoKit
import WebKit

@main
final class GitDoc: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    private let root = Bundle.main.bundleURL.deletingLastPathComponent().deletingLastPathComponent()
    private let environment = ProcessInfo.processInfo.environment
    private var window: NSWindow!
    private var webView: WKWebView!
    private var process: Process?
    private var readiness: Task<Void, Never>?
    private var origin: URL!
    private var location = URLComponents()
    private var ready = false

    static func main() {
        let application = NSApplication.shared
        let delegate = GitDoc()
        application.delegate = delegate
        application.setActivationPolicy(.regular)
        application.run()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let configuration = WKWebViewConfiguration()
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1380, height: 950),
                          styleMask: [.titled, .closable, .resizable, .miniaturizable],
                          backing: .buffered, defer: false)
        window.minSize = NSSize(width: 620, height: 480)
        window.title = "GitDoc"
        window.contentView = webView
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        let menu = NSMenu()
        let item = NSMenuItem()
        menu.addItem(item)
        item.submenu = NSMenu(title: "GitDoc")
        item.submenu?.addItem(withTitle: "GitDoc 종료", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let edit = NSMenuItem()
        menu.addItem(edit)
        edit.submenu = NSMenu(title: "편집")
        edit.submenu?.addItem(withTitle: "복사", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.submenu?.addItem(withTitle: "잘라내기", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.submenu?.addItem(withTitle: "붙여넣기", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.submenu?.addItem(withTitle: "전체 선택", action: #selector(NSResponder.selectAll(_:)), keyEquivalent: "a")
        NSApp.mainMenu = menu

        guard let port = Int(environment["DOCVERS_PORT"] ?? "8796"), (1...65535).contains(port) else {
            showError("DOCVERS_PORT 설정을 확인하세요.")
            return
        }
        origin = URL(string: "http://127.0.0.1:\(port)")!
        readiness = Task { await connect() }
    }

    func application(_ application: NSApplication, open urls: [URL]) {
        guard let url = urls.last, let link = URLComponents(url: url, resolvingAgainstBaseURL: false),
              link.scheme == "gitdoc", link.host == "open", link.user == nil, link.password == nil,
              link.port == nil,
              link.path.range(of: #"^(/|/documents/[1-9][0-9]*(/compare)?)$"#, options: .regularExpression) != nil
        else { return }
        location.path = link.path
        location.percentEncodedQueryItems = link.percentEncodedQueryItems?.filter {
            ["base", "target", "branch", "page", "change", "sheet", "cell", "view"].contains($0.name)
        }
        if ready { loadLocation() }
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        readiness?.cancel()
        if let process, process.isRunning { process.terminate() }
    }

    private func canonicalPath(_ path: String) -> String {
        if let resolved = realpath(path, nil) {
            defer { free(resolved) }
            return String(cString: resolved)
        }
        let parent = (path as NSString).deletingLastPathComponent
        guard !parent.isEmpty, parent != path else { return path }
        return (canonicalPath(parent) as NSString).appendingPathComponent((path as NSString).lastPathComponent)
    }

    private func connect() async {
        let databaseURL = URL(fileURLWithPath: environment["DOCVERS_DB"] ?? "data/documents.sqlite3",
                              relativeTo: root.appendingPathComponent("", isDirectory: true))
        let database = canonicalPath(databaseURL.standardizedFileURL.path)
        let expected = SHA256.hash(data: Data(database.utf8)).map { String(format: "%02x", $0) }.joined()
        for attempt in 0..<90 {
            if Task.isCancelled { return }
            var request = URLRequest(url: origin.appendingPathComponent("healthz"))
            request.timeoutInterval = 1.5
            request.cachePolicy = .reloadIgnoringLocalCacheData
            if let (data, _) = try? await URLSession.shared.data(for: request) {
                let value = (try? JSONSerialization.jsonObject(with: data)) as? [String: String]
                guard value?["store"] == expected else {
                    showError("포트가 다른 저장소나 프로그램에서 사용 중입니다.")
                    return
                }
                ready = true
                loadLocation()
                return
            }
            if attempt == 0 {
                let worker = Process()
                worker.executableURL = root.appendingPathComponent(".venv/bin/python")
                worker.arguments = [root.appendingPathComponent("app.py").path]
                worker.currentDirectoryURL = root
                worker.environment = environment.merging(["DOCVERS_DB": database]) { _, new in new }
                do {
                    try worker.run()
                    process = worker
                } catch {
                    showError("Python 실행 환경을 찾을 수 없습니다. README의 설치 절차를 실행하세요.")
                    return
                }
            }
            try? await Task.sleep(nanoseconds: 150_000_000)
        }
        showError("로컬 저장소에 연결할 수 없습니다. 터미널에서 app.py를 실행해 오류를 확인하세요.")
    }

    private func loadLocation() {
        var address = URLComponents(url: origin, resolvingAgainstBaseURL: false)!
        address.path = location.path.isEmpty ? "/" : location.path
        address.percentEncodedQueryItems = location.percentEncodedQueryItems
        if let url = address.url { webView.load(URLRequest(url: url)) }
    }

    private func showError(_ message: String) {
        let alert = NSAlert()
        alert.messageText = "GitDoc을 열 수 없습니다"
        alert.informativeText = message
        alert.addButton(withTitle: "닫기")
        alert.beginSheetModal(for: window) { _ in NSApp.terminate(nil) }
    }

    private func isLocal(_ url: URL?) -> Bool {
        guard let url else { return false }
        return url.scheme == origin.scheme && url.host == origin.host && url.port == origin.port
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        decisionHandler(isLocal(action.request.url) ? (action.shouldPerformDownload ? .download : .allow) : .cancel)
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        guard isLocal(response.response.url) else { decisionHandler(.cancel); return }
        let header = (response.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition")
        decisionHandler(header?.lowercased().hasPrefix("attachment") == true || !response.canShowMIMEType ? .download : .allow)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url, isLocal(url) { NSWorkspace.shared.open(url) }
        return nil
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.beginSheetModal(for: window) { response in
            completionHandler(response == .OK ? panel.urls : nil)
        }
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                  suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = suggestedFilename
        panel.beginSheetModal(for: window) { result in completionHandler(result == .OK ? panel.url : nil) }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        window.title = webView.title.map { "GitDoc · " + $0 } ?? "GitDoc"
    }
}
