"""Writes one readable block per test to results/test_output.txt.

The client and server run in different windows, so each one leaves a note in
results/.pending.json. Whichever finishes second completes the pair, and the
program writes ONE block with the verdict (PASS / FAIL) by comparing the two
SHA-256 values. Errors (missing file, connection refused, timeout) are ERROR.
"""
import json, os, sys, time

LOG = os.path.join("results", "test_output.txt")
STATE = os.path.join("results", ".pending.json")
LINE = "=" * 78
STALE = 90          # seconds to wait for the other half of a pair

PROVES = {
    ("PASS", "rudp"): "Reliable UDP delivered an identical file, even if packets were dropped on the way.",
    ("PASS", "udp"): "Plain UDP happened to deliver everything this time (no loss on this path).",
    ("PASS", "tcp"): "TCP delivered an identical file.",
    ("FAIL", "udp"): "Plain UDP lost data and the client could not tell: it said done, the server is short.",
    ("FAIL", "rudp"): "The hashes differ: reliable UDP did NOT deliver the file intact. Investigate.",
    ("FAIL", "tcp"): "The hashes differ: TCP did NOT deliver the file intact. Investigate.",
}


def _load():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"client": [], "server": [], "proxy": {}}


def _save(s):
    os.makedirs("results", exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f)


def _write(title, rows):
    os.makedirs("results", exist_ok=True)
    width = max(len(k) for k, _ in rows) + 1
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("\n%s\n%s   %s\n%s\n" % (LINE, time.strftime("%Y-%m-%d %H:%M:%S"), title, LINE))
        for k, v in rows:
            f.write("%s %s\n" % ((k + ":").ljust(width), v))


def _cmd():
    return "python " + " ".join(sys.argv)


def _flush_stale(s):
    now = time.time()
    for role in ("client", "server"):
        keep = []
        for e in s[role]:
            if now - e["t"] > STALE:
                _write("%s UNPAIRED (%s, %s)" % (role.upper(), e["proto"].upper(), e["status"]),
                       [("Command", e["cmd"]), ("Result", e["status"]), ("SHA-256", e.get("sha", "-")),
                        ("Note", "no matching %s entry within %d s" % ("server" if role == "client" else "client", STALE))])
            else:
                keep.append(e)
        s[role] = keep


def _pair(s, proto):
    """Return (client_entry, server_entry) if both halves are waiting."""
    cs = [e for e in s["client"] if e["proto"] == proto and e["status"] == "OK"]
    ss = [e for e in s["server"] if e["proto"] == proto]
    if cs and ss:
        c, v = cs[0], ss[0]
        s["client"].remove(c)
        s["server"].remove(v)
        return c, v
    return None


def _block(c, v):
    ok = c["sha"] == v["sha"] and v["received"] == v["total"]
    verdict = "PASS" if ok else "FAIL"
    _write("%s  %s transfer" % (verdict, c["proto"].upper()), [
        ("Verdict", verdict),
        ("Protocol", c["proto"].upper()),
        ("Network", c["network"]),
        ("File", "%s (%d bytes)" % (c["file"], c["size"])),
        ("Client command", c["cmd"]),
        ("Server command", v["cmd"]),
        ("Client SHA-256", c["sha"]),
        ("Server SHA-256", v["sha"]),
        ("Chunks at server", "%d of %d" % (v["received"], v["total"])),
        ("Time", "%.3f s" % c["seconds"]),
        ("Retransmissions", ("%d (packets the client sent again)" % c["retrans"]) if c["proto"] != "tcp"
         else "not measurable (TCP resends inside the operating system)"),
        ("Bytes sent", "%d (file is %d, extra %d)" % (c["bytes_sent"], c["size"], c["bytes_sent"] - c["size"])),
        ("Dropped by proxy", ("%s (this run, approx.)" % c["dropped"]) if c["dropped"] != "-" else "n/a"),
        ("What it proves", PROVES[(verdict, c["proto"])]),
    ])


def _proxy_file(port):
    return os.path.join("results", ".proxy_%s.json" % port)


def proxy_stats(port, forwarded, dropped):
    """Called by the proxy about once a second."""
    os.makedirs("results", exist_ok=True)
    with open(_proxy_file(port), "w", encoding="utf-8") as f:
        json.dump({"forwarded": forwarded, "dropped": dropped}, f)


def _read_proxy(port):
    try:
        with open(_proxy_file(port), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


_baseline = None


def client_begin(a):
    """Called by the client before sending: remember the proxy's drop count."""
    global _baseline
    _baseline = _read_proxy(a.port)


def client_ok(a, digest, size, st):
    # Do the slow part BEFORE reading the shared notes, so we never overwrite
    # an entry the server wrote while we were waiting.
    px_now = None
    if a.proto in ("udp", "rudp") and _read_proxy(a.port) is not None:
        time.sleep(1.2)                      # proxy refreshes its counters once a second
        px_now = _read_proxy(a.port)
    dropped = "-"
    if px_now is not None:
        dropped = px_now["dropped"] - (_baseline["dropped"] if _baseline else 0)
    s = _load()
    _flush_stale(s)
    px = s["proxy"].get(str(a.port))
    network = ("through proxy: %s" % px) if px else "direct, no proxy (clean)"
    s["client"].append({"t": time.time(), "proto": a.proto, "status": "OK", "cmd": _cmd(), "file": a.file,
                        "size": size, "sha": digest, "seconds": st["elapsed"], "retrans": st["retransmits"],
                        "network": network, "bytes_sent": st["bytes_sent"], "dropped": dropped})
    p = _pair(s, a.proto)
    if p:
        _block(*p)
    _save(s)


def client_error(a, error, size=None, digest=None):
    s = _load()
    _flush_stale(s)
    rows = [("Verdict", "ERROR"), ("Protocol", a.proto.upper()), ("File", a.file),
            ("Client command", _cmd()), ("Error", error)]
    if digest:
        rows.append(("Client SHA-256", digest))
    rows.append(("What it proves", "The client reported the failure instead of crashing or claiming success."))
    _write("ERROR  %s transfer" % a.proto.upper(), rows)
    _save(s)


def server_done(a, port, r):
    s = _load()
    _flush_stale(s)
    s["server"].append({"t": time.time(), "proto": a.proto, "status": "DONE", "cmd": _cmd(),
                        "sha": r["hash"], "received": r["received"], "total": r["total"]})
    p = _pair(s, a.proto)
    if p:
        _block(*p)
    _save(s)


def proxy_start(a, port):
    s = _load()
    desc = "loss %d%%, delay %d ms each way" % (a.loss if a.proto == "udp" else 0, a.delay_ms)
    s["proxy"][str(port)] = desc
    _save(s)
    _write("PROXY STARTED  (%s)" % a.proto.upper(), [
        ("Command", _cmd()), ("Settings", desc),
        ("Ports", "clients send to %d, proxy forwards to %d" % (port, a.to))])


def proxy_stop(a, port, p):
    s = _load()
    s["proxy"].pop(str(port), None)
    _save(s)
    note = "forwarded %d, dropped %d" % (p.forwarded, p.dropped) if a.proto == "udp" else "TCP proxy adds delay only"
    _write("PROXY STOPPED  (%s)" % a.proto.upper(), [("Traffic", note)])
