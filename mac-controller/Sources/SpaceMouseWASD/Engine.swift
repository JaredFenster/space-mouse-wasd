import AppKit
import CGShim
import CoreGraphics
import Foundation

/// Global, non-capturing trampoline for the CGEventTap. The tap hands us the
/// Engine back through `refcon`, so the actual logic lives on the instance.
private func engineTapCallback(
    proxy: CGEventTapProxy,
    type: CGEventType,
    event: CGEvent,
    refcon: UnsafeMutableRawPointer?
) -> Unmanaged<CGEvent>? {
    guard let refcon else { return Unmanaged.passUnretained(event) }
    let engine = Unmanaged<Engine>.fromOpaque(refcon).takeUnretainedValue()
    return engine.handle(type: type, event: event)
}

/// The whole controller runtime: a Quartz event tap capturing input, cursor
/// capture while flying, foreground detection, and the 90 Hz UDP motion stream.
/// A faithful port of `engine_base.py` + `backend_mac.py`.
final class Engine {
    // Movement arrives as *MouseDragged (not MouseMoved) whenever a button is
    // held — and the fly button is held for the whole of fly mode.
    private static let moveTypes: Set<CGEventType> =
        [.mouseMoved, .leftMouseDragged, .rightMouseDragged, .otherMouseDragged]

    private static let tapTypes: [CGEventType] =
        [.keyDown, .keyUp, .flagsChanged, .otherMouseDown, .otherMouseUp,
         .scrollWheel, .mouseMoved, .leftMouseDragged, .rightMouseDragged,
         .otherMouseDragged]

    // Setting 1 = back side button, 2 = forward side → CGEvent button numbers.
    private static let buttonNumber: [Int: Int] = [2: 4, 1: 3]

    static let flyButtonLabels: [Int: String] = [2: "Forward side", 1: "Back side"]

    // -- shared state, all touched under `lock` --
    private let lock = NSLock()
    private var binds: [String: Int]
    private var bound: Set<Int>
    private var _flyButton: Int
    private var speed: Double
    private var speedDirty = false
    private var fly = false
    private var down: [Int: Bool] = [:]
    private var accumX = 0.0
    private var accumY = 0.0
    private var lastFlyEnd = 0.0
    private var lastAck = 0.0
    private var errorText: String?
    private var cursorHidden = false
    private var fgCached = false
    private var fgCheckedAt = 0.0

    // -- tap/thread handles --
    private var tap: CFMachPort?
    private var tapRunLoop: CFRunLoop?
    private var running = false

    init(config: Config) {
        binds = config.binds
        bound = Set(config.binds.values)
        _flyButton = config.flyButton
        speed = config.speed
    }

    private func now() -> Double { ProcessInfo.processInfo.systemUptime }

    // ------------------------------------------------------------- lifecycle
    func start() {
        _ = smw_allow_background_cursor_hiding()

        let ready = DispatchSemaphore(value: 0)
        Thread.detachNewThread { [weak self] in self?.tapThread(ready) }
        _ = ready.wait(timeout: .now() + 3.0)

        lock.lock()
        let haveTap = tap != nil
        let hadError = errorText != nil
        if !haveTap && !hadError {
            errorText = "Could not create the event tap. Grant this app "
                + "Accessibility and Input Monitoring permission in System "
                + "Settings > Privacy & Security, then relaunch."
        }
        let blocked = errorText != nil
        lock.unlock()

        guard !blocked else { return }
        running = true
        Thread.detachNewThread { [weak self] in self?.senderLoop() }
    }

    private func tapThread(_ ready: DispatchSemaphore) {
        var mask: CGEventMask = 0
        for t in Self.tapTypes {
            mask |= CGEventMask(1) << CGEventMask(t.rawValue)
        }
        let mp = CGEvent.tapCreate(
            tap: .cgSessionEventTap,
            place: .headInsertEventTap,
            options: .defaultTap,
            eventsOfInterest: mask,
            callback: engineTapCallback,
            userInfo: Unmanaged.passUnretained(self).toOpaque())

        lock.lock(); tap = mp; lock.unlock()
        ready.signal()

        guard let mp else { return }
        let source = CFMachPortCreateRunLoopSource(nil, mp, 0)
        let rl = CFRunLoopGetCurrent()
        lock.lock(); tapRunLoop = rl; lock.unlock()
        CFRunLoopAddSource(rl, source, .commonModes)
        CGEvent.tapEnable(tap: mp, enable: true)
        CFRunLoopRun()
    }

    func stop() {
        running = false
        if isFlying { stopFly() }
        lock.lock()
        let mp = tap
        let rl = tapRunLoop
        lock.unlock()
        if let mp { CGEvent.tapEnable(tap: mp, enable: false) }
        if let rl { CFRunLoopStop(rl) }
    }

    // ------------------------------------------------------------- UI access
    var isFlying: Bool { lock.lock(); defer { lock.unlock() }; return fly }

    var connected: Bool {
        lock.lock(); defer { lock.unlock() }
        return now() - lastAck < 3.0
    }

    var error: String? { lock.lock(); defer { lock.unlock() }; return errorText }

    var flyButton: Int {
        get { lock.lock(); defer { lock.unlock() }; return _flyButton }
        set { lock.lock(); _flyButton = newValue; lock.unlock() }
    }

    var currentSpeed: Double {
        lock.lock(); defer { lock.unlock() }; return speed
    }

    /// If the scroll wheel changed the speed while flying, hand the new value
    /// back to the UI once so it can update the slider and persist it.
    func takeSpeedDirty() -> Double? {
        lock.lock(); defer { lock.unlock() }
        guard speedDirty else { return nil }
        speedDirty = false
        return speed
    }

    func setSpeed(_ v: Double) {
        lock.lock(); speed = min(max(v, Const.speedMin), Const.speedMax)
        lock.unlock()
    }

    func setBinds(_ newBinds: [String: Int]) {
        lock.lock()
        binds = newBinds
        bound = Set(newBinds.values)
        down.removeAll()
        lock.unlock()
    }

    private func adjustSpeed(_ factor: Double) {
        lock.lock()
        speed = min(max(speed * factor, Const.speedMin), Const.speedMax)
        speedDirty = true
        lock.unlock()
    }

    // ------------------------------------------------------------- fly mode
    private func startFly() {
        lock.lock()
        fly = true
        down.removeAll()
        accumX = 0; accumY = 0
        lock.unlock()
        // Decoupling freezes the cursor in place while raw deltas keep flowing,
        // so the pointer can never drift onto another screen or app.
        CGAssociateMouseAndMouseCursorPosition(0)
        lock.lock()
        if !cursorHidden {
            CGDisplayHideCursor(CGMainDisplayID())
            cursorHidden = true
        }
        lock.unlock()
    }

    private func stopFly() {
        lock.lock()
        fly = false
        down.removeAll()
        accumX = 0; accumY = 0
        lastFlyEnd = now()
        lock.unlock()
        CGAssociateMouseAndMouseCursorPosition(1)
        lock.lock()
        if cursorHidden {   // hide/show are refcounted — keep balanced
            CGDisplayShowCursor(CGMainDisplayID())
            cursorHidden = false
        }
        lock.unlock()
    }

    private func fusionForeground(force: Bool = false) -> Bool {
        // The sender loop asks 90×/sec; the AppKit round-trip is far too costly
        // for that, so answer from a short cache unless it must be exact.
        let t = now()
        lock.lock()
        let cached = fgCached
        let checkedAt = fgCheckedAt
        lock.unlock()
        if !force && t - checkedAt < 0.25 { return cached }

        var result = false
        if let name = NSWorkspace.shared.frontmostApplication?.localizedName {
            result = name.contains("Fusion")
        }
        lock.lock(); fgCached = result; fgCheckedAt = t; lock.unlock()
        return result
    }

    // ------------------------------------------------------------- event tap
    func handle(type: CGEventType, event: CGEvent) -> Unmanaged<CGEvent>? {
        let pass = Unmanaged.passUnretained(event)

        if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
            lock.lock(); let mp = tap; lock.unlock()
            if let mp { CGEvent.tapEnable(tap: mp, enable: true) }
            return pass
        }

        let flyBtn = Self.buttonNumber[flyButton] ?? 4

        if type == .otherMouseDown {
            let btn = Int(event.getIntegerValueField(.mouseEventButtonNumber))
            if btn == flyBtn {
                if isFlying { return nil }
                if fusionForeground(force: true) { startFly(); return nil }
            }
            return pass
        }

        if type == .otherMouseUp {
            let btn = Int(event.getIntegerValueField(.mouseEventButtonNumber))
            if btn == flyBtn && isFlying { stopFly(); return nil }
            return pass
        }

        if !isFlying { return pass }

        if Self.moveTypes.contains(type) {
            let dx = Double(event.getIntegerValueField(.mouseEventDeltaX))
            let dy = Double(event.getIntegerValueField(.mouseEventDeltaY))
            lock.lock(); accumX += dx; accumY += dy; lock.unlock()
            return nil               // cursor is frozen anyway
        }

        if type == .keyDown {
            let kc = KeyCodes.normalize(
                Int(event.getIntegerValueField(.keyboardEventKeycode)))
            lock.lock(); let isBound = bound.contains(kc); lock.unlock()
            if isBound {
                lock.lock(); down[kc] = true; lock.unlock()
                return nil
            }
            if kc == KeyCodes.escape { stopFly(); return nil }
            return pass
        }

        if type == .keyUp {
            let kc = KeyCodes.normalize(
                Int(event.getIntegerValueField(.keyboardEventKeycode)))
            lock.lock(); if bound.contains(kc) { down[kc] = false }; lock.unlock()
            return pass              // pass key-ups through so nothing sticks
        }

        if type == .flagsChanged {
            // Modifier keys arrive here, not as key down/up.
            let kc = KeyCodes.normalize(
                Int(event.getIntegerValueField(.keyboardEventKeycode)))
            if let flag = KeyCodes.modifierFlag[kc] {
                lock.lock()
                if bound.contains(kc) { down[kc] = event.flags.contains(flag) }
                lock.unlock()
            }
            return pass              // never block modifiers
        }

        if type == .scrollWheel {
            let delta = event.getIntegerValueField(.scrollWheelEventDeltaAxis1)
            if delta != 0 { adjustSpeed(pow(1.15, delta > 0 ? 1.0 : -1.0)) }
            return nil
        }

        return pass
    }

    // ----------------------------------------------------------- UDP sender
    private func senderLoop() {
        guard let sock = UDPClient(host: "127.0.0.1", port: Const.udpPort)
        else { return }
        let interval = 1.0 / Const.sendHz
        var lastPing = 0.0

        while running {
            Thread.sleep(forTimeInterval: interval)
            let t = now()

            if t - lastPing > 1.0 {
                lastPing = t
                sock.send(Data(#"{"ping":1}"#.utf8))
            }
            if sock.drain() { lock.lock(); lastAck = now(); lock.unlock() }

            if isFlying && !fusionForeground() { stopFly() }

            lock.lock()
            let active = fly || (t - lastFlyEnd) < 1.5
            if !active { lock.unlock(); continue }
            let dx = accumX, dy = accumY
            accumX = 0; accumY = 0
            let b = binds
            let d = down
            let sp = speed
            lock.unlock()

            func held(_ action: String) -> Double {
                guard let kc = b[action] else { return 0.0 }
                return d[kc] == true ? 1.0 : 0.0
            }
            let tx = held("pan_right") - held("pan_left")
            let ty = held("pan_up") - held("pan_down")
            let tz = held("zoom_in") - held("zoom_out")

            let pkt: [String: Any] = [
                "tx": tx, "ty": ty, "tz": tz,
                "rx": dx * Const.sendHz, "ry": dy * Const.sendHz,  // px/sec
                "sp": sp, "boost": false,
            ]
            if let data = try? JSONSerialization.data(withJSONObject: pkt) {
                sock.send(data)
            }
        }
    }
}
