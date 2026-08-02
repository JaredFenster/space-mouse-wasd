import SwiftUI

struct ContentView: View {
    @ObservedObject var controller: AppController

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            statusCard
            bindingsCard
            footer
            if let err = controller.errorText {
                Text(err)
                    .font(.custom(Theme.ui, size: 11))
                    .foregroundColor(Theme.danger)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 18)
                    .padding(.bottom, 18)
            }
        }
        .frame(width: 400)
        .background(Theme.bg)
    }

    // ------------------------------------------------------------- header
    private var header: some View {
        HStack(spacing: 14) {
            LogoView(size: 56)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 0) {
                    Text("SpaceMouse ")
                        .foregroundColor(Theme.text)
                    Text("WASD")
                        .foregroundColor(Theme.accent)
                }
                .font(.custom(Theme.ui, size: 19).bold())
                Text("Fly-through navigation for Fusion")
                    .font(.custom(Theme.ui, size: 11))
                    .foregroundColor(Theme.dim)
            }
            Spacer()
        }
        .padding(.horizontal, 18)
        .padding(.top, 18)
        .padding(.bottom, 10)
    }

    // ------------------------------------------------------------- status
    private var statusCard: some View {
        Card {
            statusRow(label: "Fly mode",
                      dot: controller.flying ? Theme.accent : Theme.versionGray,
                      value: controller.flying ? "Flying" : "Idle",
                      valueColor: controller.flying ? Theme.accent : Theme.dim)
            statusRow(label: "Fusion add-in",
                      dot: controller.connected ? Theme.okGreen : Theme.waitAmber,
                      value: controller.connected ? "Connected" : "Waiting…",
                      valueColor: controller.connected ? Theme.okGreen
                          : Theme.waitAmber)
        }
    }

    private func statusRow(label: String, dot: Color, value: String,
                           valueColor: Color) -> some View {
        HStack {
            Circle().fill(dot).frame(width: 10, height: 10)
            Text(label)
                .font(.custom(Theme.ui, size: 12))
                .foregroundColor(Theme.text)
            Spacer()
            Text(value)
                .font(.custom(Theme.ui, size: 12))
                .foregroundColor(valueColor)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 6)
    }

    // ------------------------------------------------------------- bindings
    private var bindingsCard: some View {
        Card {
            Text("KEY BINDINGS")
                .font(.custom(Theme.ui, size: 10).bold())
                .foregroundColor(Theme.dim)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 14)
                .padding(.top, 12)
                .padding(.bottom, 6)

            ForEach(Action.allCases, id: \.self) { action in
                HStack {
                    Text(action.label)
                        .font(.custom(Theme.ui, size: 12))
                        .foregroundColor(Theme.text)
                    Spacer()
                    BindingButton(
                        title: controller.capturingAction == action
                            ? "press a key…" : controller.keyName(action),
                        capturing: controller.capturingAction == action,
                        action: { controller.beginCapture(action) })
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 3)
            }

            HStack {
                Text("Fly button")
                    .font(.custom(Theme.ui, size: 12))
                    .foregroundColor(Theme.text)
                Spacer()
                FlyButtonPicker(controller: controller)
            }
            .padding(.horizontal, 14)
            .padding(.top, 10)
            .padding(.bottom, 3)

            HStack(spacing: 12) {
                Text("Speed")
                    .font(.custom(Theme.ui, size: 12))
                    .foregroundColor(Theme.text)
                Slider(value: Binding(
                    get: { controller.speed },
                    set: { controller.setSpeed($0) }),
                       in: Const.speedMin...Const.speedMax)
                    .tint(Theme.accent)
                Text(String(format: "×%.2f", controller.speed))
                    .font(.custom(Theme.mono, size: 12).bold())
                    .foregroundColor(Theme.accent)
                    .frame(width: 52, alignment: .trailing)
            }
            .padding(.horizontal, 14)
            .padding(.top, 6)
            .padding(.bottom, 12)
        }
    }

    // ------------------------------------------------------------- footer
    private var footer: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(controller.hint)
                .font(.custom(Theme.ui, size: 11))
                .foregroundColor(Theme.dim)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Spacer()
                Text("v\(Const.version)")
                    .font(.custom(Theme.ui, size: 11))
                    .foregroundColor(Theme.versionGray)
                Text("Reset defaults")
                    .font(.custom(Theme.ui, size: 11))
                    .foregroundColor(Theme.dim)
                    .onTapGesture { controller.reset() }
            }
        }
        .padding(.horizontal, 18)
        .padding(.top, 4)
        .padding(.bottom, 14)
    }
}

// ----------------------------------------------------------------- pieces
private struct Card<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 0) { content }
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.card)
            .cornerRadius(8)
            .padding(.horizontal, 18)
            .padding(.vertical, 7)
    }
}

private struct BindingButton: View {
    var title: String
    var capturing: Bool
    var action: () -> Void
    @State private var hover = false

    var body: some View {
        Text(title)
            .font(.custom(Theme.mono, size: 12).bold())
            .foregroundColor(capturing ? Theme.accent : Theme.text)
            .frame(width: 116)
            .padding(.vertical, 5)
            .background(hover ? Theme.fieldHi : Theme.field)
            .cornerRadius(6)
            .contentShape(Rectangle())
            .onTapGesture(perform: action)
            .onHover { hover = $0 }
    }
}

private struct FlyButtonPicker: View {
    @ObservedObject var controller: AppController
    @State private var hover = false

    var body: some View {
        Menu {
            Button("Forward side") { controller.setFlyButton(2) }
            Button("Back side") { controller.setFlyButton(1) }
        } label: {
            HStack {
                Text(Engine.flyButtonLabels[controller.flyButton] ?? "")
                    .font(.custom(Theme.ui, size: 12))
                    .foregroundColor(Theme.text)
                Spacer()
                Text("▾").foregroundColor(Theme.dim)
            }
            .frame(width: 116)
            .padding(.vertical, 4)
            .padding(.horizontal, 9)
            .background(hover ? Theme.fieldHi : Theme.field)
            .cornerRadius(6)
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .onHover { hover = $0 }
    }
}
