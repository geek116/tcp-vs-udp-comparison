"""Correctness tests. Run from the project root:  python -m unittest tests.test_correctness -v

These check that the system WORKS. They are separate from the performance experiments.
"""
import socket
import struct
import unittest

from final import netlab
from tests.harness import run_file_transfer, run_request_response

DATA = netlab.make_data(100_000)       # 100 KB = 100 chunks


class TestCorrectness(unittest.TestCase):

    def test_tcp_clean(self):
        r = run_file_transfer("tcp", data=DATA)
        self.assertEqual(r["ok_clients"], 1)

    def test_rudp_clean(self):
        for proto in ("rudp_w1", "rudp_w16"):
            r = run_file_transfer(proto, data=DATA)
            self.assertEqual(r["ok_clients"], 1, proto)
            self.assertEqual(r["retransmits"], 0, proto)

    def test_rudp_survives_heavy_loss(self):
        """The core claim: reliable UDP delivers an identical file even with 20% loss in each direction."""
        for proto in ("rudp_w1", "rudp_w16"):
            r = run_file_transfer(proto, loss=20, data=DATA)
            self.assertEqual(r["ok_clients"], 1, proto)
            self.assertGreater(r["retransmits"], 0, proto)

    def test_plain_udp_loses_data_under_loss(self):
        """Plain UDP at 20% loss must NOT deliver the whole file (that is the point of the comparison)."""
        r = run_file_transfer("udp_plain", loss=20, data=DATA)
        self.assertEqual(r["ok_clients"], 0)
        self.assertLess(r["delivered_pct"], 95)

    def test_rudp_with_delay(self):
        r = run_file_transfer("rudp_w16", loss=5, delay_ms=10, data=DATA)
        self.assertEqual(r["ok_clients"], 1)

    def test_many_clients_all_correct(self):
        for proto in ("tcp", "rudp_w16"):
            r = run_file_transfer(proto, loss=0, clients=10, data=DATA)
            self.assertEqual(r["ok_clients"], 10, proto)

    def test_many_clients_rudp_under_loss(self):
        r = run_file_transfer("rudp_w16", loss=10, clients=5, data=DATA)
        self.assertEqual(r["ok_clients"], 5)

    def test_request_response(self):
        self.assertEqual(run_request_response("tcp", n=100)["success_pct"], 100)
        self.assertEqual(run_request_response("rudp_w16", loss=10, n=100)["success_pct"], 100)
        self.assertLess(run_request_response("udp_plain", loss=20, n=200)["success_pct"], 100)


class TestErrorHandling(unittest.TestCase):
    """Communication errors must be reported or survived, never crash or hang forever."""

    def test_rudp_gives_up_when_nobody_is_listening(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)    # grab a free port, then close it
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        with self.assertRaises(TimeoutError):
            netlab.rudp_send("127.0.0.1", port, DATA, max_time=1)

    def test_tcp_connection_refused_is_an_error(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        with self.assertRaises(OSError):
            netlab.tcp_send_file("127.0.0.1", port, DATA)

    def test_tcp_server_survives_client_that_disconnects_midway(self):
        server = netlab.TcpServer(netlab.tcp_file_handler)
        bad = socket.create_connection(("127.0.0.1", server.port))
        bad.sendall(struct.pack("!Q", 1_000_000) + b"only a few bytes")   # promises 1 MB, sends 16 bytes
        bad.close()
        netlab.tcp_send_file("127.0.0.1", server.port, DATA)             # the next client must still work
        self.assertEqual([r["hash"] for r in server.results], [netlab.sha(DATA)])
        server.close()

    def test_rudp_server_handles_garbage_duplicates_and_reordering(self):
        server = netlab.RudpServer()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1.0)
        dest = ("127.0.0.1", server.port)
        sock.sendto(b"junk", dest)                                       # too short to be a packet
        sock.sendto(netlab.HDR.pack(netlab.DATA, 999, 3) + b"x", dest)   # sequence number out of range
        chunks = netlab.split(DATA)
        total = len(chunks)
        acks = 0
        for seq in reversed(range(total)):                               # out of order...
            pkt = netlab.HDR.pack(netlab.DATA, seq, total) + chunks[seq]
            sock.sendto(pkt, dest)
            sock.sendto(pkt, dest)                                       # ...and every packet duplicated
        try:
            while acks < 2 * total:
                sock.recv(64)
                acks += 1
        except socket.timeout:
            pass
        self.assertEqual(acks, 2 * total)                                # every datagram was acknowledged
        self.assertEqual([r["hash"] for r in server.results], [netlab.sha(DATA)])   # file rebuilt once, correctly
        sock.close()
        server.close()

    def test_tcp_delay_proxy_adds_latency_but_keeps_data_intact(self):
        r0 = run_request_response("tcp", delay_ms=0, n=20)
        r1 = run_request_response("tcp", delay_ms=20, n=20)
        self.assertGreater(r1["p50_ms"], r0["p50_ms"] + 30)              # about 2 x 20 ms round trip
        self.assertEqual(run_file_transfer("tcp", delay_ms=10, data=DATA)["ok_clients"], 1)


if __name__ == "__main__":
    unittest.main()
