import CoreGraphics

/// macOS ANSI virtual keycodes and the display names for them. These are the
/// same numbers the Python `backend_mac.py` used, so `binds_mac` written by
/// either controller is interchangeable.
enum KeyCodes {
    static let escape = 53

    static let names: [Int: String] = [
        0: "A", 1: "S", 2: "D", 3: "F", 4: "H", 5: "G", 6: "Z", 7: "X", 8: "C",
        9: "V", 11: "B", 12: "Q", 13: "W", 14: "E", 15: "R", 16: "Y", 17: "T",
        18: "1", 19: "2", 20: "3", 21: "4", 22: "6", 23: "5", 24: "=", 25: "9",
        26: "7", 27: "-", 28: "8", 29: "0", 30: "]", 31: "O", 32: "U", 33: "[",
        34: "I", 35: "P", 37: "L", 38: "J", 39: "'", 40: "K", 41: ";", 42: "\\",
        43: ",", 44: "/", 45: "N", 46: "M", 47: ".", 50: "`",
        36: "Return", 48: "Tab", 49: "Space", 51: "Delete",
        56: "Shift", 57: "CapsLock", 58: "Option", 59: "Control",
        123: "Left", 124: "Right", 125: "Down", 126: "Up",
    ]

    /// Right-hand modifier keycodes fold onto their left-hand equivalents so a
    /// bind to "Shift" fires regardless of which Shift the user presses.
    private static let normalizeMap: [Int: Int] = [60: 56, 62: 59, 61: 58]

    /// Which CGEventFlags bit corresponds to a modifier keycode. Modifier keys
    /// arrive as `.flagsChanged`, not key up/down, so we read them off the flags.
    static let modifierFlag: [Int: CGEventFlags] = [
        56: .maskShift, 59: .maskControl, 58: .maskAlternate,
        57: .maskAlphaShift,
    ]

    static func normalize(_ kc: Int) -> Int { normalizeMap[kc] ?? kc }

    static func name(_ kc: Int) -> String { names[kc] ?? "Key \(kc)" }
}
