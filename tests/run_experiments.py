"""run_experiments.py - the main evaluation of the improved system.

Run from the project root. Examples:
  python -m tests.run_experiments --quick                 # ~1-2 minutes, to check everything works
  python -m tests.run_experiments                         # full proxy-mode run (the default matrix)
  # Linux/WSL2 only, to get real TCP loss as well:
  sudo tc qdisc add dev lo root netem loss 5%
  python -m tests.run_experiments --mode netem --loss 5 --clients 1
  sudo tc qdisc del dev lo root

Output (appended, so you can run it several times):
  results/file_transfer.csv
  results/request_response.csv
"""
import argparse
import csv
import os

from final import netlab
from tests.harness import run_file_transfer, run_request_response

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")


def append(path, row):
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if new:
            w.writeheader()
        w.writerow(row)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["proxy", "netem"], default="proxy")
    ap.add_argument("--losses", type=int, nargs="+", default=[0, 5, 10], help="proxy mode: loss %% levels")
    ap.add_argument("--loss", type=int, default=0, help="netem mode: the loss %% you applied with tc (label only)")
    ap.add_argument("--delay-ms", type=int, default=0)
    ap.add_argument("--size", type=int, default=262144, help="file size in bytes")
    ap.add_argument("--clients", type=int, nargs="+", default=[1, 10])
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--requests", type=int, default=300)
    ap.add_argument("--quick", action="store_true", help="small, fast sanity run")
    ap.add_argument("--skip-rr", action="store_true", help="skip the request/response workload")
    args = ap.parse_args()

    if args.quick:
        args.size, args.reps, args.requests, args.clients = 65536, 1, 100, [1, 3]
    os.makedirs(RES, exist_ok=True)
    data = netlab.make_data(args.size)
    losses = args.losses if args.mode == "proxy" else [args.loss]
    protos = ["tcp", "udp_plain", "rudp_w1", "rudp_w16"]

    print("== Workload A: file transfer ==")
    for loss in losses:
        for clients in args.clients:
            for proto in protos:
                # In proxy mode TCP cannot be impaired, so it is only measured at 0% (as the reference).
                if args.mode == "proxy" and proto == "tcp" and loss != 0:
                    continue
                for rep in range(1, args.reps + 1):
                    r = run_file_transfer(proto, loss, args.delay_ms, clients, data, args.mode)
                    r.update(rep=rep, mode=args.mode)
                    append(os.path.join(RES, "file_transfer.csv"), r)
                    print("  %-9s loss=%2d%% clients=%2d rep=%d  ok=%d/%d delivered=%5.1f%%  %6.2fs  retx=%d"
                          % (proto, loss, clients, rep, r["ok_clients"], clients, r["delivered_pct"],
                             r["seconds"], r["retransmits"]), flush=True)

    if not args.skip_rr:
        print("== Workload B: request/response ==")
        for loss in losses:
            for proto in ["tcp", "udp_plain", "rudp_w16"]:
                if args.mode == "proxy" and proto == "tcp" and loss != 0:
                    continue
                for rep in range(1, args.reps + 1):
                    r = run_request_response(proto, loss, args.delay_ms, args.requests, args.mode)
                    r.update(rep=rep, mode=args.mode)
                    append(os.path.join(RES, "request_response.csv"), r)
                    print("  %-9s loss=%2d%% rep=%d  success=%5.1f%%  p50=%.2fms p95=%.2fms p99=%.2fms"
                          % (proto, loss, rep, r["success_pct"], r["p50_ms"], r["p95_ms"], r["p99_ms"]), flush=True)
    print("done. CSVs are in", RES)


if __name__ == "__main__":
    main()
