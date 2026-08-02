import Foundation

/// Shared constants that must match the Fusion add-in and the Python controller.
enum Const {
    static let udpPort: UInt16 = 42737   // add-in listens here
    static let lockPort: UInt16 = 42739  // single-instance guard
    static let sendHz = 90.0             // motion packet rate
    static let speedMin = 0.15
    static let speedMax = 5.0
    static let appName = "SpaceMouse WASD"
    static let version = "1.1.0"
}

/// The six pannable/zoom actions, in the order the UI lists them.
enum Action: String, CaseIterable {
    case panUp = "pan_up"
    case panDown = "pan_down"
    case panLeft = "pan_left"
    case panRight = "pan_right"
    case zoomIn = "zoom_in"
    case zoomOut = "zoom_out"

    var label: String {
        switch self {
        case .panUp: return "Pan up"
        case .panDown: return "Pan down"
        case .panLeft: return "Pan left"
        case .panRight: return "Pan right"
        case .zoomIn: return "Zoom in"
        case .zoomOut: return "Zoom out"
        }
    }
}

/// Controller configuration, persisted to the same JSON file the Python
/// controller uses so the two are drop-in compatible.
struct Config {
    // W / S, A / D, Shift / Control — matches Python DEFAULT_BINDS.
    static let defaultBinds: [String: Int] = [
        "pan_up": 13, "pan_down": 1,
        "pan_left": 0, "pan_right": 2,
        "zoom_in": 56, "zoom_out": 59,
    ]

    var binds: [String: Int]
    var flyButton: Int    // 1 = back side, 2 = forward side
    var speed: Double

    /// Any `binds_*` blocks for other operating systems, kept verbatim so
    /// saving on a Mac never clobbers a user's Windows bindings.
    private var otherBinds: [String: Any]

    private static let bindsKey = "binds_mac"

    private static var dir: URL {
        FileManager.default
            .homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/SpaceMouseWASD")
    }

    private static var path: URL {
        dir.appendingPathComponent("config.json")
    }

    static func load() -> Config {
        var cfg = Config(binds: defaultBinds, flyButton: 2, speed: 1.0,
                         otherBinds: [:])
        guard let data = try? Data(contentsOf: path),
              let root = (try? JSONSerialization.jsonObject(with: data))
                  as? [String: Any]
        else { return cfg }

        if let saved = root[bindsKey] as? [String: Any] {
            for (k, v) in saved where defaultBinds[k] != nil {
                if let code = (v as? NSNumber)?.intValue { cfg.binds[k] = code }
            }
        }
        if let fb = (root["fly_button"] as? NSNumber)?.intValue,
           fb == 1 || fb == 2 {
            cfg.flyButton = fb
        }
        if let sp = (root["speed"] as? NSNumber)?.doubleValue {
            cfg.speed = min(max(sp, Const.speedMin), Const.speedMax)
        }
        cfg.otherBinds = root.filter {
            $0.key.hasPrefix("binds_") && $0.key != bindsKey
        }
        return cfg
    }

    func save() {
        var out: [String: Any] = otherBinds   // preserve other OSes' binds
        out[Config.bindsKey] = binds
        out["fly_button"] = flyButton
        out["speed"] = speed
        guard let data = try? JSONSerialization.data(
            withJSONObject: out, options: [.prettyPrinted, .sortedKeys])
        else { return }
        try? FileManager.default.createDirectory(
            at: Config.dir, withIntermediateDirectories: true)
        try? data.write(to: Config.path)
    }
}
