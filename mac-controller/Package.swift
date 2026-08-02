// swift-tools-version: 5.9
import PackageDescription

// Tools-version 5.9 keeps the Swift 5 language mode (no Swift 6 strict
// concurrency), which suits this app's deliberate shared-state + background
// thread design — a faithful port of the Python controller.
let package = Package(
    name: "SpaceMouseWASD",
    platforms: [.macOS(.v12)],
    targets: [
        // Private CoreGraphics window-server call, isolated in C where the
        // undocumented symbols are natural to declare.
        .target(
            name: "CGShim",
            linkerSettings: [.linkedFramework("CoreFoundation")]
        ),
        .executableTarget(
            name: "SpaceMouseWASD",
            dependencies: ["CGShim"]
        ),
    ]
)
