"""make_graphs.py - turn results/*.csv into PNG graphs and a summary table.

Run from the project root:   python -m tests.make_graphs
Outputs (in results/): completion_time.png, delivery.png, clients10.png, rr_latency.png,
                       rr_success.png, summary_file_transfer.csv, summary_table.md
"""
import csv
import os
import statistics
import textwrap
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
LABEL = {"tcp": "TCP", "udp_plain": "Plain UDP", "rudp_w1": "Reliable UDP (window 1)", "rudp_w16": "Reliable UDP (window 16)"}
COLOR = {"tcp": "#1f77b4", "udp_plain": "#d62728", "rudp_w1": "#ff7f0e", "rudp_w16": "#2ca02c"}


def load(name):
    with open(os.path.join(RES, name)) as f:
        return list(csv.DictReader(f))


def agg(rows, keys, value):
    """mean and standard deviation of `value` grouped by `keys`."""
    groups = defaultdict(list)
    for r in rows:
        if r[value] not in ("", None):
            groups[tuple(r[k] for k in keys)].append(float(r[value]))
    return {k: (statistics.mean(v), statistics.pstdev(v) if len(v) > 1 else 0.0, len(v)) for k, v in groups.items()}


def line_plot(rows, value, ylabel, title, fname, protos, filt=lambda r: True, logy=False, note=None):
    rows = [r for r in rows if filt(r)]
    a = agg(rows, ["proto", "loss"], value)
    plt.figure(figsize=(7, 4.5))
    for p in protos:
        pts = sorted((int(k[1]), v) for k, v in a.items() if k[0] == p)
        if not pts:
            continue
        xs = [x for x, _ in pts]
        plt.errorbar(xs, [v[0] for _, v in pts], yerr=[v[1] for _, v in pts], marker="o", capsize=3,
                     label=LABEL[p], color=COLOR[p])
    plt.xlabel("Packet loss on the path (%)")
    plt.ylabel(ylabel)
    plt.title(title)
    if logy:
        plt.yscale("log")
    plt.xticks(sorted({int(r["loss"]) for r in rows}))
    plt.grid(alpha=0.3)
    plt.legend()
    if note:
        plt.figtext(0.01, 0.005, textwrap.fill(note, 105), fontsize=7, color="gray")
    plt.tight_layout(rect=(0, 0.07 if note else 0.0, 1, 1))
    plt.savefig(os.path.join(RES, fname), dpi=150)
    plt.close()


def baseline_vs_final(ft):
    """Compare the frozen AI baseline with the improved system (1 client, same file size, same loss levels)."""
    path = os.path.join(RES, "baseline_results.csv")
    if not os.path.exists(path):
        print("no baseline_results.csv: run  python -m tests.run_baseline  first")
        return
    base = load("baseline_results.csv")
    size = int(base[0].get("file_size") or 262144)
    rows_md = ["| Variant | Loss % | Runs | Correct file (% of runs) | Data delivered (%) | Hung (runs) | Datagrams lost to buffer overflow (mean) |",
               "|---|---|---|---|---|---|---|"]
    chart = {}
    for v in ("baseline_tcp", "baseline_udp"):
        for loss in sorted({int(r["loss"]) for r in base if r["variant"] == v}):
            rs = [r for r in base if r["variant"] == v and int(r["loss"]) == loss]
            correct = 100 * statistics.mean(int(r["ok"]) for r in rs)
            deliv = 100 * statistics.mean(min(1.0, int(r["bytes_received"]) / size) for r in rs)
            hung = sum(r["status"] == "hang" for r in rs)
            ov = [float(r["unintended_loss"]) for r in rs if r["unintended_loss"] not in ("", None)]
            rows_md.append("| AI baseline %s | %d | %d | %.0f | %.1f | %d | %s |" % (
                v.split("_")[1].upper(), loss, len(rs), correct, deliv, hung, ("%.0f" % statistics.mean(ov)) if ov else "n/a"))
            chart[(v, loss)] = (correct, deliv)
    for p in ["tcp", "udp_plain", "rudp_w1", "rudp_w16"]:
        for loss in sorted({int(r["loss"]) for r in ft if r["proto"] == p and r["clients"] == "1"}):
            rs = [r for r in ft if r["proto"] == p and int(r["loss"]) == loss and r["clients"] == "1"]
            correct = 100 * statistics.mean(float(r["ok_clients"]) for r in rs)
            deliv = statistics.mean(float(r["delivered_pct"]) for r in rs)
            ov = [float(r["unintended_loss"]) for r in rs if r["unintended_loss"] not in ("", None)]
            rows_md.append("| Improved %s | %d | %d | %.0f | %.1f | 0 | %s |" % (
                LABEL[p], loss, len(rs), correct, deliv, ("%.0f" % statistics.mean(ov)) if ov else "n/a"))
            chart[(p, loss)] = (correct, deliv)
    open(os.path.join(RES, "baseline_vs_final.md"), "w").write("\n".join(rows_md) + "\n")

    series = [("baseline_udp", "AI baseline UDP", "#7f7f7f"), ("udp_plain", "Improved: plain UDP", COLOR["udp_plain"]),
              ("rudp_w1", "Improved: reliable UDP (w1)", COLOR["rudp_w1"]), ("rudp_w16", "Improved: reliable UDP (w16)", COLOR["rudp_w16"])]
    losses = sorted({l for (_, l) in chart})
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    for ax, idx, yl, ttl in ((axes[0], 0, "Runs with a byte-identical file (%)", "Reliability"),
                             (axes[1], 1, "Data delivered (%)", "Data delivery")):
        for i, (key, lab, col) in enumerate(series):
            xs = [j + (i - 1.5) * 0.2 for j, l in enumerate(losses) if (key, l) in chart]
            ys = [chart[(key, l)][idx] for l in losses if (key, l) in chart]
            ax.bar(xs, ys, 0.2, label=lab, color=col)
            for x, y in zip(xs, ys):
                ax.text(x, y + 1.5, "%.0f" % y, ha="center", fontsize=7)      # label every bar, including the zeros
        ax.set_ylim(0, 112)
        ax.set_xticks(range(len(losses)))
        ax.set_xticklabels([f"{l}%" for l in losses])
        ax.set_xlabel("Packet loss on the path")
        ax.set_ylabel(yl)
        ax.set_title(ttl)
        ax.grid(axis="y", alpha=0.3)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=4, fontsize=8)
    plt.suptitle("AI baseline vs improved system (UDP, 1 client)")
    plt.tight_layout(rect=(0, 0.07, 1, 1))
    plt.savefig(os.path.join(RES, "baseline_vs_final.png"), dpi=150)
    plt.close()


def delay_analysis(ft_all, rr_all):
    """Added one-way delay (proxy mode): completion time and request latency, with vs without delay."""
    dl = sorted({int(r["delay_ms"]) for r in ft_all if int(r["delay_ms"]) > 0})
    if not dl:
        print("no delay runs found: run  python -m tests.run_experiments --losses 0 5 --delay-ms 20 --clients 1 --requests 100")
        return
    d = dl[0]
    protos = ["tcp", "udp_plain", "rudp_w1", "rudp_w16"]
    sel = lambda rows, delay: [r for r in rows if int(r["delay_ms"]) == delay and r["clients"] == "1" and r["loss"] == "0"]
    a0 = agg(sel(ft_all, 0), ["proto"], "seconds")
    a1 = agg(sel(ft_all, d), ["proto"], "seconds")
    r0 = agg([r for r in rr_all if int(r["delay_ms"]) == 0 and r["loss"] == "0"], ["proto"], "p50_ms")
    r1 = agg([r for r in rr_all if int(r["delay_ms"]) == d and r["loss"] == "0"], ["proto"], "p50_ms")
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    for ax, A, B, protos_, yl, ttl in ((axes[0], a0, a1, protos, "Completion time (s)", "File transfer, 0% loss"),
                                        (axes[1], r0, r1, ["tcp", "udp_plain", "rudp_w16"], "Median request latency (ms)", "Request/response, 0% loss")):
        for i, p in enumerate(protos_):
            for j, (src, lab) in enumerate(((A, "no added delay"), (B, f"{d} ms one-way delay"))):
                if (p,) in src:
                    ax.bar(i + (j - 0.5) * 0.38, src[(p,)][0], 0.38, color=COLOR[p], alpha=0.45 if j == 0 else 1.0,
                           label=lab if i == 0 or (i == 1 and j == 0 and p != "tcp") else None)
        ax.set_xticks(range(len(protos_)))
        ax.set_xticklabels([LABEL[p].replace("Reliable UDP ", "RUDP ") for p in protos_], fontsize=8)
        ax.set_ylabel(yl)
        ax.set_title(ttl)
        ax.set_yscale("log")
        ax.grid(axis="y", alpha=0.3)
    plt.suptitle(f"Effect of {d} ms added one-way delay (light = none, dark = delayed)")
    plt.tight_layout()
    plt.savefig(os.path.join(RES, "delay_effect.png"), dpi=150)
    plt.close()
    lines = ["| Protocol | Loss % | File transfer time s (mean) | Correct files | Request p50 ms | Request p95 ms |", "|---|---|---|---|---|---|"]
    for loss in sorted({r["loss"] for r in ft_all if int(r["delay_ms"]) == d}, key=int):
        for p in protos:
            f = [r for r in ft_all if int(r["delay_ms"]) == d and r["loss"] == loss and r["proto"] == p and r["clients"] == "1"]
            q = [r for r in rr_all if int(r["delay_ms"]) == d and r["loss"] == loss and r["proto"] == p]
            if not f:
                continue
            lines.append("| %s | %s | %.3f | %.1f | %s | %s |" % (
                LABEL[p], loss, statistics.mean(float(r["seconds"]) for r in f), statistics.mean(float(r["ok_clients"]) for r in f),
                "%.1f" % statistics.mean(float(r["p50_ms"]) for r in q) if q else "-",
                "%.1f" % statistics.mean(float(r["p95_ms"]) for r in q) if q else "-"))
    open(os.path.join(RES, "summary_delay.md"), "w").write(f"One-way delay = {d} ms (applied in each direction)\n\n" + "\n".join(lines) + "\n")


def main():
    ft_all = load("file_transfer.csv")
    ft = [r for r in ft_all if r["delay_ms"] == "0"]          # the delay runs are analysed separately below
    protos = ["tcp", "udp_plain", "rudp_w1", "rudp_w16"]
    one = lambda r: r["clients"] == "1"
    tcp_note = "TCP is only plotted where it was measured (0% in proxy mode; every level if tc netem was used)."

    line_plot(ft, "seconds", "Completion time (s)", "File transfer: completion time vs loss (1 client)",
              "completion_time.png", protos, one, note="Plain UDP time = sender-side send time only; it has no completion signal. " + tcp_note)
    line_plot(ft, "delivered_pct", "Data delivered (%)", "File transfer: data delivered vs loss (1 client)",
              "delivery.png", protos, one, note=tcp_note)

    # 10-client bar chart: correct files and time
    many = max(int(r["clients"]) for r in ft)
    a_ok = agg([r for r in ft if int(r["clients"]) == many], ["proto", "loss"], "ok_clients")
    losses = sorted({int(r["loss"]) for r in ft})
    plt.figure(figsize=(7.5, 4.5))
    width = 0.2
    for i, p in enumerate(protos):
        xs, ys = [], []
        for j, l in enumerate(losses):
            v = a_ok.get((p, str(l)))
            if v:
                xs.append(j + (i - 1.5) * width)
                ys.append(v[0])
        plt.bar(xs, ys, width, label=LABEL[p], color=COLOR[p])
    plt.xticks(range(len(losses)), [f"{l}%" for l in losses])
    plt.xlabel("Packet loss on the path")
    plt.ylabel(f"Clients that received a correct file (of {many})")
    plt.title(f"{many} concurrent clients: correct deliveries")
    plt.legend(fontsize=8)
    plt.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(RES, "clients10.png"), dpi=150)
    plt.close()

    # summary table for the report
    sa = agg(ft, ["proto", "loss", "clients"], "seconds")
    ok = agg(ft, ["proto", "loss", "clients"], "ok_clients")
    dp = agg(ft, ["proto", "loss", "clients"], "delivered_pct")
    rt = agg(ft, ["proto", "loss", "clients"], "retransmits")
    oh = agg(ft, ["proto", "loss", "clients"], "overhead_pct")
    gp = agg(ft, ["proto", "loss", "clients"], "goodput_mbps")
    lines = ["| Protocol | Loss % | Clients | Correct files (mean) | Delivered % | Time s (mean ± sd) | Goodput Mbit/s | Retransmits | Overhead % |",
             "|---|---|---|---|---|---|---|---|---|"]
    out_rows = []
    for k in sorted(sa, key=lambda k: (int(k[2]), int(k[1]), protos.index(k[0]))):
        p, l, c = k
        lines.append("| %s | %s | %s | %.1f | %.1f | %.3f ± %.3f | %.2f | %.0f | %s |" % (
            LABEL[p], l, c, ok[k][0], dp[k][0], sa[k][0], sa[k][1], gp[k][0], rt[k][0],
            ("%.1f" % oh[k][0]) if k in oh and oh[k][0] == oh[k][0] else "n/a"))
        out_rows.append({"proto": p, "loss": l, "clients": c, "runs": sa[k][2], "ok_clients_mean": ok[k][0],
                         "delivered_pct_mean": dp[k][0], "seconds_mean": sa[k][0], "seconds_sd": sa[k][1],
                         "goodput_mbps_mean": gp[k][0], "retransmits_mean": rt[k][0]})
    with open(os.path.join(RES, "summary_file_transfer.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)
    open(os.path.join(RES, "summary_table.md"), "w").write("\n".join(lines) + "\n")

    # request/response
    rr_all = load("request_response.csv")
    rr = [r for r in rr_all if r["delay_ms"] == "0"]
    rp = ["tcp", "udp_plain", "rudp_w16"]
    line_plot(rr, "p95_ms", "95th-percentile latency (ms)", "Request/response: p95 latency vs loss",
              "rr_latency.png", rp, logy=True, note="Plain UDP latency counts answered requests only; lost requests are not in the latency data. " + tcp_note)
    line_plot(rr, "success_pct", "Requests answered (%)", "Request/response: success rate vs loss",
              "rr_success.png", rp, note=tcp_note)
    baseline_vs_final(ft)
    delay_analysis(ft_all, rr_all)
    print("wrote graphs and summary to", RES)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
