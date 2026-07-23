import Foundation
import SystemInfoKit

@testable import DataSource

extension Metrics {
    static func dummy(customMetricsTitle: String) -> Metrics {
        Metrics(customMetricsBundles: [
            CustomMetricsBundle(
                id: UUID(1),
                snapshot: CustomMetricsSnapshot(
                    title: customMetricsTitle,
                    lastUpdatedDate: Date(timeIntervalSince1970: 0)
                )
            ),
        ])
    }

    static func dummy(cpuRawValue: Double) -> Metrics {
        var systemInfoBundle = SystemInfoBundle()
        systemInfoBundle.cpuInfo = CPUInfo(
            percentage: Percentage(rawValue: cpuRawValue),
            system: .zero,
            user: .zero,
            idle: .zero
        )
        return Metrics(systemInfoBundle: systemInfoBundle)
    }
}
