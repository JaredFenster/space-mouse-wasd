import Darwin
import Foundation

/// Minimal non-blocking UDP client to localhost, mirroring the Python sender's
/// use of a plain datagram socket: fire packets at the add-in and drain any
/// heartbeat acks that come back on the same ephemeral port.
final class UDPClient {
    private var fd: Int32 = -1
    private var addr = sockaddr_in()

    init?(host: String, port: UInt16) {
        fd = socket(AF_INET, SOCK_DGRAM, 0)
        guard fd >= 0 else { return nil }

        // Non-blocking so recv never stalls the send loop.
        let flags = fcntl(fd, F_GETFL, 0)
        _ = fcntl(fd, F_SETFL, flags | O_NONBLOCK)

        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = port.bigEndian
        guard inet_pton(AF_INET, host, &addr.sin_addr) == 1 else {
            close(fd)
            return nil
        }
    }

    func send(_ data: Data) {
        _ = data.withUnsafeBytes { raw in
            withUnsafePointer(to: &addr) { aptr in
                aptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
                    sendto(fd, raw.baseAddress, raw.count, 0, sa,
                           socklen_t(MemoryLayout<sockaddr_in>.size))
                }
            }
        }
    }

    /// Drain every pending datagram; returns true if at least one arrived.
    func drain() -> Bool {
        var got = false
        var buf = [UInt8](repeating: 0, count: 64)
        while true {
            let n = recv(fd, &buf, buf.count, 0)
            if n <= 0 { break }
            got = true
        }
        return got
    }

    deinit {
        if fd >= 0 { close(fd) }
    }
}
