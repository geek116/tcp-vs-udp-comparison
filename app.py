"""app.py - the interactive client-server application. The USER SELECTS the protocol.

  --proto tcp    TCP
  --proto udp    plain UDP (no reliability)
  --proto rudp   our reliable UDP (sequence numbers, ACKs, retransmission, sliding window)

Terminal 1 (server):   python app.py server --proto rudp --port 9000
Terminal 2 (client):   python app.py send   --proto rudp --port 9000 --file myfile.bin
Compare the SHA-256 printed by the client with the one printed by the server.

To put a bad network between them (any OS), start a proxy in a third terminal and send to ITS port:
  python app.py proxy --proto udp --listen 9001 --to 9000 --loss 10 --delay-ms 20
  python app.py send  --proto rudp --port 9001 --file myfile.bin
(For --proto tcp the proxy can add delay only; use `tc netem` for real TCP loss.)
"""
import argparse
import sys
import time

from final import netlab
from tests.impairment import TcpDelayProxy, UdpProxy


def cmd_server(a):
    if a.proto == "tcp":
        server = netlab.TcpServer(netlab.tcp_file_handler, a.host, a.port)
    elif a.proto == "udp":
        server = netlab.PlainUdpServer(a.host, a.port)
    else:
        server = netlab.RudpServer(a.host, a.port)
    print("[server] %s listening on %s:%d  (Ctrl+C to stop)" % (a.proto.upper(), a.host, server.port), flush=True)
    seen = 0
    try:
        while True:
            time.sleep(0.2)
            while seen < len(server.results):
                r = server.results[seen]
                seen += 1
                print("[server] transfer finished: %d/%d chunks, SHA-256 %s" % (r["received"], r["total"], r["hash"]), flush=True)
    except KeyboardInterrupt:
        server.close()


def cmd_send(a):
    try:
        data = open(a.file, "rb").read()
    except OSError as e:
        print("[client] TRANSFER FAILED: cannot read file %s (%s)" % (a.file, e.strerror))
        sys.exit(1)
    digest = netlab.sha(data)
    print("[client] file %s: %d bytes, SHA-256 %s" % (a.file, len(data), digest))
    try:
        if a.proto == "tcp":
            st = netlab.tcp_send_file(a.host, a.port, data)
        elif a.proto == "udp":
            st = netlab.plain_udp_send(a.host, a.port, data)
        else:
            st = netlab.rudp_send(a.host, a.port, data, window=a.window, rto=a.rto, max_time=a.timeout)
    except (OSError, TimeoutError) as e:      # communication errors are reported, not crashes
        print("[client] TRANSFER FAILED: %r" % (e,))
        sys.exit(1)
    print("[client] done in %.3f s, %d bytes sent, %d retransmissions" % (st["elapsed"], st["bytes_sent"], st["retransmits"]))
    if a.proto == "udp":
        print("[client] plain UDP gives no delivery confirmation: check the server's SHA-256 yourself.")


def cmd_proxy(a):
    if a.proto == "udp":
        p = UdpProxy((a.host, a.to), loss=a.loss / 100, delay=a.delay_ms / 1000, listen_port=a.listen)
    else:
        p = TcpDelayProxy((a.host, a.to), delay=a.delay_ms / 1000, listen_port=a.listen)
    print("[proxy] %s %d%% loss, %d ms delay: clients should connect to port %d, forwarding to %d" %
          (a.proto.upper(), a.loss if a.proto == "udp" else 0, a.delay_ms, p.port, a.to), flush=True)
    try:
        while True:
            time.sleep(1)
            if a.proto == "udp":
                print("[proxy] forwarded %d, dropped %d" % (p.forwarded, p.dropped), flush=True)
    except KeyboardInterrupt:
        p.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("server")
    s.add_argument("--proto", choices=["tcp", "udp", "rudp"], required=True)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=9000)
    s.set_defaults(fn=cmd_server)
    c = sub.add_parser("send")
    c.add_argument("--proto", choices=["tcp", "udp", "rudp"], required=True)
    c.add_argument("--host", default="127.0.0.1")
    c.add_argument("--port", type=int, default=9000)
    c.add_argument("--file", required=True)
    c.add_argument("--window", type=int, default=16, help="rudp: packets in flight (1 = stop-and-wait)")
    c.add_argument("--rto", type=float, default=0.05, help="rudp: retransmission timeout in seconds")
    c.add_argument("--timeout", type=int, default=60, help="rudp: give up after this many seconds")
    c.set_defaults(fn=cmd_send)
    p = sub.add_parser("proxy")
    p.add_argument("--proto", choices=["tcp", "udp"], required=True)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--listen", type=int, default=9001)
    p.add_argument("--to", type=int, default=9000)
    p.add_argument("--loss", type=float, default=0, help="percent of datagrams dropped (udp only)")
    p.add_argument("--delay-ms", type=int, default=0)
    p.set_defaults(fn=cmd_proxy)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
