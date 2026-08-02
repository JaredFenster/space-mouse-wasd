import AppKit
import Darwin
import SwiftUI

/// Binds a localhost TCP port for the app's lifetime as a cross-platform
/// single-instance lock — two copies would mean two sets of input hooks
/// fighting over the same events.
final class SingleInstanceLock {
    private var fd: Int32 = -1

    init?(port: UInt16) {
        fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { return nil }
        var yes: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes,
                   socklen_t(MemoryLayout<Int32>.size))
        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = port.bigEndian
        addr.sin_addr.s_addr = INADDR_ANY.bigEndian
        let bound = withUnsafePointer(to: &addr) { p in
            p.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard bound == 0, listen(fd, 1) == 0 else {
            close(fd); return nil
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var lock: SingleInstanceLock?
    private var engine: Engine?
    private var controller: AppController?
    private var window: NSWindow?

    func applicationDidFinishLaunching(_ notification: Notification) {
        lock = SingleInstanceLock(port: Const.lockPort)
        if lock == nil {
            let alert = NSAlert()
            alert.messageText = Const.appName
            alert.informativeText = "\(Const.appName) is already running."
            alert.runModal()
            NSApp.terminate(nil)
            return
        }

        let config = Config.load()
        let engine = Engine(config: config)
        engine.start()
        self.engine = engine

        let controller = AppController(engine: engine, config: config)
        controller.startPolling()
        self.controller = controller

        buildMenu()
        showWindow(controller: controller)

        if CommandLine.arguments.contains("--selftest") {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                print("SELFTEST OK")
                NSApp.terminate(nil)
            }
        }
    }

    private func showWindow(controller: AppController) {
        let hosting = NSHostingView(rootView: ContentView(controller: controller))
        hosting.setFrameSize(hosting.fittingSize)

        let win = NSWindow(
            contentRect: NSRect(origin: .zero, size: hosting.fittingSize),
            styleMask: [.titled, .closable, .miniaturizable],
            backing: .buffered, defer: false)
        win.title = Const.appName
        win.appearance = NSAppearance(named: .darkAqua)
        win.backgroundColor = NSColor(Theme.bg)
        win.titlebarAppearsTransparent = true
        win.isMovableByWindowBackground = true
        win.contentView = hosting
        win.center()
        win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        self.window = win
    }

    private func buildMenu() {
        let mainMenu = NSMenu()
        let appItem = NSMenuItem()
        mainMenu.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Hide \(Const.appName)",
                        action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit \(Const.appName)",
                        action: #selector(NSApplication.terminate(_:)),
                        keyEquivalent: "q")
        appItem.submenu = appMenu
        NSApp.mainMenu = mainMenu
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication)
        -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        controller?.shutDown()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
