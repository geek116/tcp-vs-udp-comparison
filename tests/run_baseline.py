"""run_baseline.py - benchmark the FROZEN AI baseline (baseline/*.py) under the same conditions.

Run from the project root:   python -m tests.run_baseline
Output: results/baseline_results.csv

The baseline scripts are started as separate processes, exactly as an unmodified user would.
A run is marked 'hang' if the server has not exited after TIMEOUT seconds (e.g. the b"END"
datagram was lost, so the server waits forever).
"""
import argparse
import csv
import os
import subprocess
import sys
import tempfile
import time

from final import netlab
from tests.impairment import UdpProxy

TIMEOUT = 8.0
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def run_once(proto, loss, in_path, expected_hash, size):
    out_path = tempfile.mktemp(suffix=".out")
    port = free_port()
    server = subprocess.Popen([PY, os.path.join(ROOT, "baseline", f"{proto}_server.py"), str(port), out_path],
                              stdout=subprocess.PIPE, text=True)
    server.stdout.readline()                      # wait for the "listening" line
    proxy = None
    client_port = port
    if proto == "udp" and loss > 0:
        proxy = UdpProxy(("127.0.0.1", port), loss=loss / 100)
        client_port = proxy.port
    t0 = time.perf_counter()
    subprocess.run([PY, os.path.join(ROOT, "baseline", f"{proto}_client.py"), str(client_port), in_path],
                   stdout=subprocess.DEVNULL, check=False)
    status = "finished"
    try:
        server.wait(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait()
        status = "hang"
    seconds = time.perf_counter() - t0
    got = open(out_path, "rb").read() if os.path.exists(out_path) else b""
    if os.path.exists(out_path):
        try:
            os.remove(out_path)
        except OSError:
            pass
    ok = (netlab.sha(got) == expected_hash)
    row = {"variant": f"baseline_{proto}", "loss": loss, "ok": int(ok), "status": status,
           "file_size": size, "bytes_received": len(got), "seconds": round(seconds, 4), "unintended_loss": None}
    if proto == "udp":
        sent = -(-size // 1024) + 1                          # data datagrams + the END datagram
        arrived = -(-len(got) // 1024) + (1 if status == "finished" else 0)
        configured = proxy.c2s_dropped if proxy else 0
        # datagrams lost for reasons other than the loss we configured (buffer overflow etc.)
        row["unintended_loss"] = sent - configured - arrived
    if proxy:
        proxy.close()
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=262144)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--losses", type=int, nargs="+", default=[0, 5, 10])
    args = ap.parse_args()

    data = netlab.make_data(args.size)
    expected = netlab.sha(data)
    in_path = tempfile.mktemp(suffix=".in")
    open(in_path, "wb").write(data)

    os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
    out_csv = os.path.join(ROOT, "results", "baseline_results.csv")
    rows = []
    for proto in ("tcp", "udp"):
        for loss in ([0] if proto == "tcp" else args.losses):   # the proxy cannot impair TCP
            for rep in range(args.reps):
                r = run_once(proto, loss, in_path, expected, args.size)
                r["rep"] = rep + 1
                rows.append(r)
                print(r, flush=True)
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    os.remove(in_path)
    print("wrote", out_csv)


if __name__ == "__main__":
    main()
