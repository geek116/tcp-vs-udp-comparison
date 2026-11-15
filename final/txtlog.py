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
        ("Retransmissions", c["retrans"]),
        ("What it proves", PROVES[(verdict, c["proto"])]),
    ])


def client_ok(a, digest, size, st):
    s = _load()
    _flush_stale(s)
    px = s["proxy"].get(str(a.port))
    network = ("through proxy: %s" % px) if px else "direct, no proxy (clean)"
    s["client"].append({"t": time.time(), "proto": a.proto, "status": "OK", "cmd": _cmd(), "file": a.file,
                        "size": size, "sha": digest, "seconds": st["elapsed"], "retrans": st["retransmits"],
                        "network": network})
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
