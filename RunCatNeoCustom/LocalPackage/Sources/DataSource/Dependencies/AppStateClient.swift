/*
 AppStateClient.swift
 DataSource

 Created by Takuto Nakamura on 2026/05/05.
 Copyright 2026 Kyome22 (Takuto Nakamura)

 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at

 http://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
 */

import AllocatedUnfairLock

public struct AppStateClient: DependencyClient {
    private let lock: AllocatedUnfairLock<AppState>

    init(lock: AllocatedUnfairLock<AppState>) {
        self.lock = lock
    }

    public func withLock<R: Sendable>(_ body: @Sendable (inout AppState) throws -> R) rethrows -> R {
        try lock.withLock(body)
    }

    public func send<T: Sendable>(
        _ keyPath: any WritableKeyPath<AppState, AsyncStreamBundle<T>> & Sendable,
        _ value: T
    ) {
        lock.withLock { $0[keyPath: keyPath].send(value) }
    }

    public func send<T: Sendable>(
        _ keyPath: any WritableKeyPath<AppState, AsyncStreamBundle<T>> & Sendable,
        default defaultValue: @autoclosure @Sendable () -> T,
        _ transform: @Sendable (inout T) -> Void
    ) {
        lock.withLock { appState in
            var value = appState[keyPath: keyPath].latestValue ?? defaultValue()
            transform(&value)
            appState[keyPath: keyPath].send(value)
        }
    }

    public static let liveValue = Self(lock: .init(initialState: .init()))

    public static let testValue = Self(lock: .init(initialState: .init()))

    public static func testDependency(_ appState: AllocatedUnfairLock<AppState>) -> Self {
        Self(lock: appState)
    }
}
