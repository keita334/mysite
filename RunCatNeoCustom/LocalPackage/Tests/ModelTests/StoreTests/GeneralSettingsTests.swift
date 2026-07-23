import AllocatedUnfairLock
import Foundation
import Testing

@testable import DataSource
@testable import Model

struct GeneralSettingsTests {
    @MainActor @Test
    func send_updateIntervalChanged_persists_and_restarts_monitoring() async {
        let setCallStack = AllocatedUnfairLock<[String]>(initialState: [])
        let monitoringEvents = AllocatedUnfairLock<[String]>(initialState: [])
        let sut = GeneralSettings(.testDependencies(
            systemInfoObserverClient: testDependency(of: SystemInfoObserverClient.self) {
                $0.stopMonitoring = {
                    monitoringEvents.withLock { $0.append("stop") }
                }
                $0.startMonitoring = { interval in
                    monitoringEvents.withLock { $0.append("start: \(interval)") }
                }
            },
            userDefaultsClient: testDependency(of: UserDefaultsClient.self) {
                $0.integer = { _ in 3 }
                $0.set = { value, key in
                    let entry = "set: \(key) = \(value ?? "nil")"
                    setCallStack.withLock { $0.append(entry) }
                }
            }
        ))
        await sut.send(.updateIntervalChanged(.threeSeconds))
        #expect(sut.updateInterval == .threeSeconds)
        #expect(setCallStack.withLock(\.self) == ["set: UPDATE_INTERVAL = 3"])
        #expect(monitoringEvents.withLock(\.self) == ["stop", "start: 3.0"])
    }

    @MainActor @Test
    func send_launchAtLoginToggleSwitched_enables_when_register_succeeds() async {
        let registered = AllocatedUnfairLock(initialState: false)
        let sut = GeneralSettings(.testDependencies(
            smAppServiceClient: testDependency(of: SMAppServiceClient.self) {
                $0.status = { registered.withLock(\.self) ? .enabled : .notRegistered }
                $0.register = { registered.withLock { $0 = true } }
            }
        ))
        await sut.send(.launchAtLoginToggleSwitched(true))
        #expect(sut.launchesAtLogin == true)
    }

    @MainActor @Test
    func send_launchAtLoginToggleSwitched_keeps_actual_status_when_register_fails() async {
        let sut = GeneralSettings(.testDependencies(
            smAppServiceClient: testDependency(of: SMAppServiceClient.self) {
                $0.status = { .notRegistered }
                $0.register = { throw URLError(.unknown) }
            }
        ))
        await sut.send(.launchAtLoginToggleSwitched(true))
        #expect(sut.launchesAtLogin == false)
    }
}
