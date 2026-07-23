import Foundation

extension URL {
    static func fixture(name: String, fileExtension: String = "png") -> URL {
        Bundle.module.url(forResource: name, withExtension: fileExtension)!
    }

    func hasPathSuffix(_ suffix: String) -> Bool {
        path(percentEncoded: false).hasSuffix(suffix)
    }
}
