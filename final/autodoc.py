"""autodoc.py - automatic documentation of every test, from scratch.

THE IDEA
--------
The client, the server and the proxy run in different terminal windows and
cannot see each other's results.  Instead of passing results around while a
test is running, every program simply APPENDS facts to a journal:

    results/journal.jsonl     one JSON object per line, append-only

    client -> send_start / send_done / send_error   (one sid per transfer)
    server -> transfer_done
    proxy  -> proxy_start / proxy_stats / proxy_stop

Nothing in the journal is ever modified or deleted, so it is crash-safe,
works on every OS and can be re-read at any moment.  Turning facts into the
human-readable log is a separate step that only READS the journal:

  * after every appended fact, any test that has just become COMPLETE is
    written immediately as a block to results/test_output.txt, so the
    documentation happens by itself while you test;
  * `python -m final.autodoc`           prints a summary of every test so far;
  * `python -m final.autodoc --rebuild` regenerates ALL blocks from the
    journal into results/test_output_rebuilt.txt (the original log is never
    touched), so the report can always be reproduced from the facts.

A test = one client session + the server result that belongs to it.  The
matching is done when the report is built, not while the test runs: both
sides independently know the protocol and the chunk count, so a server
result belongs to the client session with the same (protocol, chunks) and
the closest timestamp.  If no server result ever arrives, the test is
reported honestly as a failure after a short grace period - it is never
silently paired with some other test's result.

Verdicts:
    PASS   client and server SHA-256 match and every chunk arrived
    FAIL   hashes differ, chunks are missing, or nothing reached the server
    ERROR  the client could not send at all (missing file, refused, timeout)
"""
import json
import os
import sys
import time
import uuid

from final import netlab

JOURNAL = os.path.join("results", "journal.jsonl")        # append-only facts
LOG = os.path.join("results", "test_output.txt")          # automatic blocks
REBUILT = os.path.join("results", "test_output_rebuilt.txt")
STATE = os.path.join("results", ".autodoc_state.json")    # which sids are written
LOCK = os.path.join("results", ".autodoc.lock")           # serializes writers
LINE = "=" * 78

GRACE = 30.0            # s: client finished but no server result matched -> FAIL after this
GRACE_OPEN = 300.0      # s: client sent NOTHING back yet (still transferring, or its window
                        # died). A live transfer can run for minutes, so only report it as
                        # interrupted after this long. Measured from the send_start.
MATCH_WINDOW = 30.0     # s: max distance between a client result and its server result
                        # (in practice the two events are < 2 s apart; 30 is very generous)

_sid = None             # the client session running in THIS process

PROVES = {
    ("PASS", "rudp"): "The reliability layer (sequence numbers, ACKs, retransmission) recovered every dropped datagram - the file arrived byte-for-byte identical.",
    ("PASS", "udp"): "Plain UDP delivered everything this time, but only because nothing was lost on the way - there was no guarantee.",
    ("PASS", "tcp"): "TCP's built-in reliability delivered the file byte-for-byte identical.",
    ("FAIL", "udp"): "Plain UDP silently lost data: the client believed it succeeded, but the server's copy is short and its hash differs.",
    ("FAIL", "rudp"): "The reliability layer did not recover everything in this run - the received hash differs. Worth investigating.",
    ("FAIL", "tcp"): "The received copy differs from the sent file, which is unexpected for TCP.",
}


# --------------------------------------------------------------------------
# 1. Recording facts (called from app.py).  Appending one line is the only
#    write the network programs ever do - no shared state to corrupt.
# --------------------------------------------------------------------------

def _append(event):
    event = dict(event)
    event["t"] = time.time()
    event["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs("results", exist_ok=True)
    with open(JOURNAL, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    _emit_new()


def _cmd():
    return "python " + " ".join(sys.argv)


def client_send_start(a, digest, size):
    """Called just before the client starts sending."""
    global _sid
    _sid = uuid.uuid4().hex[:8]
    event = {"role": "client", "event": "send_start", "sid": _sid, "proto": a.proto,
             "port": a.port, "file": a.file, "size": size, "sha": digest,
             "chunks": -(-size // netlab.CHUNK), "cmd": _cmd()}
    if a.proto == "rudp":
        event["window"] = a.window          # custom settings are recorded,
        event["rto"] = a.rto                # not assumed from the defaults
    _append(event)


def client_send_done(st):
    """Called when the client finished sending without an error."""
    if _sid is None:
        return
    _append({"role": "client", "event": "send_done", "sid": _sid,
             "elapsed": st["elapsed"], "bytes_sent": st["bytes_sent"],
             "retransmits": st["retransmits"]})


def client_send_error(a, error, size=None, digest=None):
    """Called when the client could not send at all (missing file, refused,
    timeout...).  If no send_start was recorded yet, one is created so the
    failure is still documented as its own test."""
    global _sid
    if _sid is None:
        _sid = uuid.uuid4().hex[:8]
        _append({"role": "client", "event": "send_start", "sid": _sid, "proto": a.proto,
                 "port": a.port, "file": a.file, "size": size or 0,
                 "sha": digest or "-", "chunks": 0, "cmd": _cmd()})
    _append({"role": "client", "event": "send_error", "sid": _sid, "error": error})


def server_transfer_done(a, port, r):
    """Called by the server each time it has (the end of) a transfer."""
    _append({"role": "server", "event": "transfer_done", "proto": a.proto, "port": port,
             "sha": r["hash"], "chunks_received": r["received"], "chunks_total": r["total"],
             "cmd": _cmd()})


def proxy_start(a, port):
    _append({"role": "proxy", "event": "proxy_start", "proto": a.proto, "listen": port,
             "to": a.to, "loss": a.loss if a.proto == "udp" else 0,
             "delay_ms": a.delay_ms, "cmd": _cmd()})


def proxy_stats(port, forwarded, dropped):
    """Called about once a second so the totals survive even a crash."""
    _append({"role": "proxy", "event": "proxy_stats", "listen": port,
             "forwarded": forwarded, "dropped": dropped})


def proxy_stop(a, port, p):
    _append({"role": "proxy", "event": "proxy_stop", "listen": port,
             "forwarded": getattr(p, "forwarded", 0), "dropped": getattr(p, "dropped", 0)})


# --------------------------------------------------------------------------
# 2. Reading the journal back and grouping it into tests
# --------------------------------------------------------------------------

def _read_journal():
    events = []
    try:
        with open(JOURNAL, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue          # a torn line never stops the report
    except OSError:
        pass
    return events


def _sessions(events):
    """Group client events into sessions (one sid = one transfer attempt)."""
    starts, outcomes = {}, {}
    for e in events:
        if e.get("role") != "client":
            continue
        if e.get("event") == "send_start":
            starts[e["sid"]] = e
        elif e.get("event") in ("send_done", "send_error") and e.get("sid") in starts:
            outcomes[e["sid"]] = e
    return [{"sid": sid, "start": starts[sid], "outcome": outcomes.get(sid)}
            for sid in sorted(starts, key=lambda s: starts[s]["t"])]


def _network(events, port, t):
    """What the network looked like at time t for clients sending to `port`."""
    desc = None
    for e in events:
        if e.get("role") != "proxy" or e.get("listen") != port:
            continue
        if e["event"] == "proxy_start" and e["t"] <= t:
            desc = ("loss %g%%, delay %d ms each way" % (e["loss"], e["delay_ms"])) \
                if e["proto"] == "udp" \
                else ("delay %d ms each way (TCP relay: delay only, no loss)" % e["delay_ms"])
        elif e["event"] == "proxy_stop" and e["t"] <= t:
            desc = None
    return ("through proxy: %s" % desc) if desc else "direct, no proxy (clean)"


def _match(sessions, events):
    """Attach to each session the server result that belongs to it.

    Both sides know the protocol and the chunk count, so a server result can
    only belong to a session with the same (proto, chunks).  Among the
    possible pairs, the globally CLOSEST (session, result) pair is matched
    first, then the next closest, and so on.  Because the assignment is a
    single global pass over everything in the journal, it comes out the same
    no matter when it is run - an old session can never steal the result of
    a newer test, and re-reading the journal later cannot reshuffle the
    pairs.  Returns the number of server results no session claimed (stray
    datagrams from an earlier test, etc.)."""
    by_sid = {s["sid"]: s for s in sessions}
    server_events = [e for e in events
                     if e.get("role") == "server" and e.get("event") == "transfer_done"]
    pairs = []
    for s in sessions:
        out = s["outcome"]
        if out is None or out["event"] == "send_error":
            continue                                  # nothing to match
        for i, e in enumerate(server_events):
            if e["proto"] != s["start"]["proto"] or e["chunks_total"] != s["start"]["chunks"]:
                continue
            if e["t"] < s["start"]["t"] - 1:
                continue                              # result cannot precede the send
            d = abs(e["t"] - out["t"])
            if d <= MATCH_WINDOW:
                pairs.append((d, s["sid"], i))
    used = set()
    for d, sid, i in sorted(pairs):
        s = by_sid[sid]
        if "server" in s or i in used:
            continue
        s["server"] = server_events[i]
        used.add(i)
    return len(server_events) - len(used)


def _verdict(s):
    """(verdict, note) for one session.  A note overrides the default text."""
    out = s["outcome"]
    if out is None:
        return "ERROR", "The client never finished (window closed or interrupted?)"
    if out["event"] == "send_error":
        return "ERROR", "The client detected and reported the failure instead of crashing or claiming success."
    v = s.get("server")
    if v is None:
        return "FAIL", "No result ever reached the server, so delivery could not be verified - the transfer did not succeed."
    if v["sha"] == s["start"]["sha"] and v["chunks_received"] == v["chunks_total"]:
        return "PASS", None
    return "FAIL", None


def _rows(s, events):
    """The block for one test, in the requested format."""
    verdict, note = _verdict(s)
    c, out, v = s["start"], s["outcome"], s.get("server")
    rows = [
        ("Run ID", s["sid"]),
        ("Verdict", verdict),
        ("Protocol", c["proto"].upper()),
        ("Network", _network(events, c["port"], c["t"])),
        ("File", "%s (%d bytes)" % (c["file"], c["size"])),
        ("Client command", c["cmd"]),
        ("Server command", v["cmd"] if v else "- (no server result)"),
        ("Client SHA-256", c["sha"]),
        ("Server SHA-256", v["sha"] if v else "- (no server result)"),
        ("Chunks at server", "%d of %d" % (v["chunks_received"], v["chunks_total"]) if v else "-"),
    ]
    if out is not None and out["event"] == "send_done":
        rows.append(("Time", "%.3f s" % out["elapsed"]))
        if c["proto"] == "rudp":
            rows.append(("Window", "%d packets in flight" % c.get("window", 16)))
            rows.append(("Timer (RTO)", "%.3f s" % c.get("rto", 0.05)))
        rows.append(("Retransmissions",
                     ("%d (packets the client sent again)" % out["retransmits"]) if c["proto"] != "tcp"
                     else "not measurable (TCP retransmits inside the operating system)"))
    else:
        rows.append(("Time", "-"))
        rows.append(("Retransmissions", "-"))
    if verdict == "ERROR":
        rows.append(("Error", out["error"] if out is not None and out["event"] == "send_error" else note))
    rows.append(("What it proves", note or PROVES[(verdict, c["proto"])]))
    return verdict, c["proto"], rows


# --------------------------------------------------------------------------
# 3. Writing blocks.  A tiny lock serializes the two windows that might
#    finish at the same moment; the journal itself needs no lock.
# --------------------------------------------------------------------------

class _Lock:
    def __enter__(self):
        deadline = time.time() + 5
        while True:
            try:
                self.fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                try:
                    if os.path.getmtime(LOCK) < time.time() - 10:   # stale lock
                        os.remove(LOCK)
                        continue
                except OSError:
                    pass
                if time.time() > deadline:          # never fail a test over logging
                    self.fd = None
                    return self
                time.sleep(0.01)

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
            try:
                os.remove(LOCK)
            except OSError:
                pass
        return False


def _write_block(path, verdict, proto, rows):
    os.makedirs("results", exist_ok=True)
    width = max(len(k) for k, _ in rows) + 1
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n%s\n%s   %s  %s transfer\n%s\n" %
                (LINE, time.strftime("%Y-%m-%d %H:%M:%S"), verdict, proto.upper(), LINE))
        for k, v in rows:
            f.write("%s %s\n" % ((k + ":").ljust(width), v))


def _load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
        st.setdefault("emitted", [])
        return st
    except (OSError, ValueError):
        return {"emitted": []}


def _save_state(st):
    os.makedirs("results", exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(tmp, STATE)


def _ready(sessions, now):
    """Which sessions can be written now:
      * client errors                      -> immediately
      * finished + matched server result   -> immediately
      * finished, no server result ever    -> FAIL after GRACE (the server
        finalizes within ~1 s of a transfer, so if nothing matched in 30 s
        nothing ever will)
      * no outcome at all yet              -> only after GRACE_OPEN.  A slow
        transfer (small window, big timer, big file) is still RUNNING, not
        dead, so it must never be reported while the client is still sending."""
    ready = []
    for s in sessions:
        out = s["outcome"]
        if out is not None and out["event"] == "send_error":
            ready.append(s)
        elif out is not None and s.get("server"):
            ready.append(s)
        elif out is not None and now - out["t"] > GRACE:
            ready.append(s)
        elif out is None and now - s["start"]["t"] > GRACE_OPEN:
            ready.append(s)
    return ready


def _emit_new():
    """Write the blocks for every test that is complete but not yet written."""
    with _Lock():
        events = _read_journal()
        state = _load_state()
        sessions = _sessions(events)
        _match(sessions, events)
        wrote = False
        for s in _ready(sessions, time.time()):
            if s["sid"] in state["emitted"]:
                continue
            _write_block(LOG, *_rows(s, events))
            state["emitted"].append(s["sid"])
            wrote = True
        if wrote:
            _save_state(state)


# --------------------------------------------------------------------------
# 4. Command line:  python -m final.autodoc [--rebuild]
# --------------------------------------------------------------------------

def main(argv):
    if "--rebuild" in argv:
        events = _read_journal()
        sessions = _sessions(events)
        _match(sessions, events)
        if os.path.exists(REBUILT):
            os.remove(REBUILT)
        for s in sessions:
            _write_block(REBUILT, *_rows(s, events))
        print("Rebuilt %d test block(s) from the journal -> %s" % (len(sessions), REBUILT))
        return

    _emit_new()
    events = _read_journal()
    sessions = _sessions(events)
    stray = _match(sessions, events)
    print("%d test(s) in the journal:" % len(sessions))
    counts = {}
    for s in sessions:
        verdict, proto, rows = _rows(s, events)
        counts[verdict] = counts.get(verdict, 0) + 1
        out = s["outcome"]
        secs = ("%.3f" % out["elapsed"]) if out is not None and out["event"] == "send_done" else "-"
        retx = str(out["retransmits"]) if out is not None and out["event"] == "send_done" else "-"
        v = s.get("server")
        chunks = ("%d/%d" % (v["chunks_received"], v["chunks_total"])) if v else "-"
        print("  %s  %-5s %-4s  seconds=%-8s retx=%-5s chunks=%s  (%s)"
              % (s["start"]["ts"], verdict, proto, secs, retx, chunks, s["sid"]))
    if sessions:
        print("Verdicts: " + ", ".join("%s=%d" % kv for kv in sorted(counts.items())))
    if stray:
        print("Note: %d server result(s) matched no client session (stray datagrams?)" % stray)
    if not sessions:
        print("No tests recorded yet. Run a transfer with app.py first.")


if __name__ == "__main__":
    main(sys.argv[1:])
