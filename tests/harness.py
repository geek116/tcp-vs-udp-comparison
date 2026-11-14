"""harness.py - runs ONE experiment (server + optional impaired path + N clients) and returns measurements.
Used by demo.py, test_correctness.py and run_experiments.py.

proto is one of:
  "tcp"        TCP
  "udp_plain"  plain UDP, no reliability
  "rudp_w1"    our reliable UDP, window of 1 (stop-and-wait)
  "rudp_w16"   our reliable UDP, window of 16 (sliding window)

mode:
  "proxy"  loss/delay are injected by tests/impairment.py (UDP protocols only)
  "netem"  loss/delay were already applied to the loopback interface with `tc netem`;
           everything connects directly, and `loss` is just the label to record.
"""
import statistics
import threading
import time

from final import netlab
from tests.impairment import TcpDelayProxy, UdpProxy

PROTOS = ["tcp", "udp_plain", "rudp_w1", "rudp_w16"]


def percentile(values, p):
    if not values:
        return float("nan")
    s = sorted(values)
    k = min(len(s) - 1, max(0, int(round(p / 100 * (len(s) - 1)))))
    return s[k]


def _start(proto, workload):
    """Create the right server for this protocol and workload."""
    if proto == "tcp":
        handler = netlab.tcp_file_handler if workload == "file" else netlab.tcp_echo_handler
        return netlab.TcpServer(handler)
    if workload == "echo":
        return netlab.UdpEchoServer()
    return netlab.PlainUdpServer() if proto == "udp_plain" else netlab.RudpServer()


def _target(proto, server, loss, delay_ms, mode):
    """Return (port clients should connect to, proxy or None).
    TCP can only be DELAYED by the proxy (not made lossy); UDP can be both."""
    if proto == "tcp" and mode == "proxy" and delay_ms > 0:
        proxy = TcpDelayProxy(("127.0.0.1", server.port), delay=delay_ms / 1000)
        return proxy.port, proxy
    if proto != "tcp" and mode == "proxy" and (loss > 0 or delay_ms > 0):
        proxy = UdpProxy(("127.0.0.1", server.port), loss=loss / 100, delay=delay_ms / 1000)
        return proxy.port, proxy
    return server.port, None


def run_file_transfer(proto, loss=0, delay_ms=0, clients=1, data=None, mode="proxy"):
    expected = netlab.sha(data)
    chunks = -(-len(data) // netlab.CHUNK)
    server = _start(proto, "file")
    port, proxy = _target(proto, server, loss, delay_ms, mode)
    rto = 0.05 + 3 * delay_ms / 1000        # RTO must exceed the round-trip time
    outputs = [None] * clients

    def worker(i):
        try:
            if proto == "tcp":
                outputs[i] = netlab.tcp_send_file("127.0.0.1", port, data)
            elif proto == "udp_plain":
                outputs[i] = netlab.plain_udp_send("127.0.0.1", port, data)
            else:
                window = 1 if proto == "rudp_w1" else 16
                outputs[i] = netlab.rudp_send("127.0.0.1", port, data, window=window, rto=rto, max_time=90)
        except Exception as e:  # a failed client is a result, not a crash
            outputs[i] = {"error": repr(e)}

    cpu0, t0 = time.process_time(), time.perf_counter()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(clients)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    wall = time.perf_counter() - t0
    if proto == "udp_plain":                 # wait for the server's idle timeout to close each session
        deadline = time.perf_counter() + 2.0
        while len(server.results) < clients and time.perf_counter() < deadline:
            time.sleep(0.02)
    cpu = time.process_time() - cpu0

    ok = sum(1 for r in server.results if r["hash"] == expected)
    received = sum(r["received"] for r in server.results)
    good = [o for o in outputs if o and "error" not in o]
    errors = [o["error"] for o in outputs if o and "error" in o]
    sent = sum(o["bytes_sent"] for o in good)
    row = {
        "proto": proto, "loss": loss, "delay_ms": delay_ms, "clients": clients, "size": len(data),
        "ok_clients": ok,
        "delivered_pct": 100.0 * received / (clients * chunks),
        "seconds": wall,
        "goodput_mbps": (ok * len(data) * 8 / wall / 1e6) if wall > 0 else 0.0,
        "retransmits": sum(o["retransmits"] for o in good),
        "overhead_pct": 100.0 * (sent - clients * len(data)) / (clients * len(data)) if good else float("nan"),
        "cpu_s": cpu,
        "errors": len(errors),
        "proxy_dropped": proxy.dropped if proxy else 0,
        # Plain UDP only: datagrams lost for any reason OTHER than the loss we configured
        # (i.e. receive-buffer overflow in the proxy or the server). 0 means the measured loss is the configured loss.
        "unintended_loss": (sum(o["packets"] for o in good) - (proxy.c2s_dropped if proxy else 0) - received)
                           if proto == "udp_plain" else None,
    }
    server.close()
    if proxy:
        proxy.close()
    return row


def run_request_response(proto, loss=0, delay_ms=0, n=300, mode="proxy"):
    server = _start(proto, "echo")
    port, proxy = _target(proto, server, loss, delay_ms, mode)
    timeout = 0.05 + 3 * delay_ms / 1000
    t0 = time.perf_counter()
    if proto == "tcp":
        out = netlab.tcp_request_response("127.0.0.1", port, n)
    else:
        out = netlab.udp_request_response("127.0.0.1", port, n, reliable=(proto != "udp_plain"), timeout=timeout)
    wall = time.perf_counter() - t0
    lat = [x * 1000 for x in out["latencies"]]
    row = {
        "proto": proto, "loss": loss, "delay_ms": delay_ms, "requests": n,
        "success_pct": 100.0 * len(lat) / n,
        "lost": out["lost"], "retries": out["retries"],
        "p50_ms": percentile(lat, 50), "p95_ms": percentile(lat, 95), "p99_ms": percentile(lat, 99),
        "mean_ms": statistics.mean(lat) if lat else float("nan"),
        "seconds": wall,
    }
    server.close()
    if proxy:
        proxy.close()
    return row
