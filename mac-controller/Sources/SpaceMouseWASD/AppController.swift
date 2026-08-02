import AppKit
import SwiftUI

/// View-model bridging the background `Engine` to SwiftUI. A 10 Hz timer polls
/// the engine's thread-safe snapshot (mirroring the Python UI's 200 ms tick),
/// and user actions here update the engine and persist config.
final class AppController: ObservableObject {
    @Published var flying = false
    @Published var connected = false
    @Published var speed: Double
    @Published var binds: [String: Int]
    @Published var flyButton: Int
    @Published var capturingAction: Action?
    @Published private(set) var errorText: String?

    private let engine: Engine
    private var config: Config
    private var pollTimer: Timer?
    private var saveTimer: Timer?
    private var keyMonitor: Any?

    init(engine: Engine, config: Config) {
        self.engine = engine
        self.config = config
        self.speed = config.speed
        self.binds = config.binds
        self.flyButton = config.flyButton
        self.errorText = engine.error
    }

    func startPolling() {
        let timer = Timer(timeInterval: 0.1, repeats: true) { [weak self] _ in
            guard let self else { return }
            self.flying = self.engine.isFlying
            self.connected = self.engine.connected
            if let sp = self.engine.takeSpeedDirty() {   // wheel changed speed
                self.speed = sp
                self.config.speed = sp
                self.config.save()
            }
        }
        RunLoop.main.add(timer, forMode: .common)   // keep ticking during drags
        pollTimer = timer
    }

    var hint: String {
        let name = (Engine.flyButtonLabels[flyButton] ?? "").uppercased()
        return "Hold the \(name) mouse button in Fusion to fly.  "
            + "Scroll = speed, Esc = bail out."
    }

    func keyName(_ action: Action) -> String {
        KeyCodes.name(binds[action.rawValue] ?? 0)
    }

    // ------------------------------------------------------------- rebinding
    func beginCapture(_ action: Action) {
        endCapture()
        capturingAction = action
        keyMonitor = NSEvent.addLocalMonitorForEvents(
            matching: [.keyDown, .flagsChanged]
        ) { [weak self] event in
            self?.handleCapture(event)
            return nil                              // swallow keys while capturing
        }
    }

    private func handleCapture(_ event: NSEvent) {
        let kc = KeyCodes.normalize(Int(event.keyCode))
        if event.type == .keyDown {
            if kc == KeyCodes.escape { endCapture(); return }   // Esc cancels
            applyBind(kc)
        } else if let flag = Self.nsModifierFlag[kc],
                  event.modifierFlags.contains(flag) {
            applyBind(kc)                            // modifier pressed (not released)
        }
    }

    private func applyBind(_ kc: Int) {
        guard KeyCodes.names[kc] != nil, let action = capturingAction else {
            endCapture(); return
        }
        var updated = config.binds
        for (other, code) in updated
        where other != action.rawValue && code == kc {
            updated[other] = updated[action.rawValue]   // swap to avoid dupes
        }
        updated[action.rawValue] = kc
        config.binds = updated
        binds = updated
        engine.setBinds(updated)
        config.save()
        endCapture()
    }

    func endCapture() {
        if let m = keyMonitor { NSEvent.removeMonitor(m); keyMonitor = nil }
        capturingAction = nil
    }

    private static let nsModifierFlag: [Int: NSEvent.ModifierFlags] = [
        56: .shift, 59: .control, 58: .option, 57: .capsLock,
    ]

    // ------------------------------------------------------------- settings
    func setFlyButton(_ n: Int) {
        flyButton = n
        engine.flyButton = n
        config.flyButton = n
        config.save()
    }

    func setSpeed(_ v: Double) {
        speed = v
        engine.setSpeed(v)
        config.speed = v
        saveTimer?.invalidate()
        saveTimer = Timer.scheduledTimer(withTimeInterval: 0.8, repeats: false) {
            [weak self] _ in self?.config.save()
        }
    }

    func reset() {
        config.binds = Config.defaultBinds
        config.flyButton = 2
        config.speed = 1.0
        engine.setBinds(Config.defaultBinds)
        engine.flyButton = 2
        engine.setSpeed(1.0)
        binds = Config.defaultBinds
        flyButton = 2
        speed = 1.0
        config.save()
        endCapture()
    }

    func shutDown() {
        config.save()
        engine.stop()
    }
}
