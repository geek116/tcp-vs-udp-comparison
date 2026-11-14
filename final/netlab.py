"""netlab.py - the improved system: TCP, plain UDP and reliable UDP (RUDP).

Everything here uses only Python's standard library.

Two workloads are supported for every protocol:
  A) FILE TRANSFER      - a client uploads bytes to a server; the server hashes
                          what it received so we can prove it arrived intact.
  B) REQUEST / RESPONSE - a client sends many small requests; the server echoes
                          each one back; we measure the latency of each exchange.

Sections:
  1. helpers
  2. TCP            (server + clients)
  3. Plain UDP      (server + client, no reliability at all)
  4. Reliable UDP   (our own sequence numbers, ACKs, retransmission, window)
  5. Request/response clients for UDP
"""
import hashlib
import math
import random
import socket
import struct
import threading
import time

# --------------------------------------------------------------------------
# 1. Helpers
# --------------------------------------------------------------------------
CHUNK = 1000                 # payload bytes per UDP datagram (safely below the ~1500 byte Ethernet MTU)
HDR = struct.Struct("!BII")  # our UDP header: type (1 byte), seq (4 bytes), total (4 bytes) = 9 bytes
DATA, ACK = 1, 2             # packet types
REQ_SIZE = 64                # size of a request in the request/response workload


def make_data(size, seed=1):
    """Reproducible pseudo-random bytes, so every run sends identical data."""
    return random.Random(seed).randbytes(size)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def split(data):
    """Cut data into CHUNK-sized pieces."""
    return [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)]


def recv_exact(sock, n):
    """TCP is a byte stream, so one recv() may return fewer bytes than asked.
    Keep reading until exactly n bytes have arrived. (A classic bug in naive code.)"""
    buf = bytearray(n)
    view = memoryview(buf)
    got = 0
    while got < n:
        r = sock.recv_into(view[got:], n - got)
        if r == 0:
            raise ConnectionError("connection closed before all bytes arrived")
        got += r
    return bytes(buf)


# --------------------------------------------------------------------------
# 2. TCP
# --------------------------------------------------------------------------
class TcpServer:
    """Accepts connections and runs `handler(conn, results)` on a new thread for each one,
    so many clients can be served at the same time."""

    def __init__(self, handler, host="127.0.0.1", port=0):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.sock.listen(128)
        self.port = self.sock.getsockname()[1]   # port 0 = let the OS choose a free port
        self.handler = handler
        self.results = []
        self._stop = False
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        self.sock.settimeout(0.2)
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        with conn:
            try:
                self.handler(conn, self.results)
            except OSError:      # a client vanished mid-transfer: don't crash the server
                pass

    def close(self):
        self._stop = True
        self.sock.close()


def tcp_file_handler(conn, results):
    """Protocol: [8-byte length][that many bytes]. Reply b'K' once everything arrived.
    The length prefix is the 'message framing' that a raw byte stream lacks."""
    (length,) = struct.unpack("!Q", recv_exact(conn, 8))
    data = recv_exact(conn, length)
    chunks = math.ceil(length / CHUNK)
    results.append({"hash": sha(data), "received": chunks, "total": chunks})
    conn.sendall(b"K")


def tcp_echo_handler(conn, results):
    """Echo every fixed-size request back until the client disconnects."""
    while True:
        req = recv_exact(conn, REQ_SIZE)
        conn.sendall(req)


def tcp_send_file(host, port, data):
    t0 = time.perf_counter()
    sock = socket.create_connection((host, port), timeout=60)   # TCP handshake happens here
    connect_time = time.perf_counter() - t0
    with sock:
        sock.sendall(struct.pack("!Q", len(data)) + data)
        recv_exact(sock, 1)                                       # wait for the server's 'K'
    return {"elapsed": time.perf_counter() - t0, "connect_time": connect_time,
            "bytes_sent": len(data) + 8, "retransmits": 0}


def tcp_request_response(host, port, n):
    t0 = time.perf_counter()
    sock = socket.create_connection((host, port), timeout=60)
    connect_time = time.perf_counter() - t0
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)   # don't let Nagle's algorithm delay tiny messages
    latencies = []
    with sock:
        for i in range(n):
            req = i.to_bytes(4, "big") + b"x" * (REQ_SIZE - 4)
            t = time.perf_counter()
            sock.sendall(req)
            recv_exact(sock, REQ_SIZE)
            latencies.append(time.perf_counter() - t)
    return {"latencies": latencies, "lost": 0, "retries": 0, "connect_time": connect_time}


# --------------------------------------------------------------------------
# 3. Plain UDP (no ACKs, no retransmission)
# --------------------------------------------------------------------------
class UdpServer:
    """Base class: one thread reads datagrams from one socket and calls on_packet()."""

    def __init__(self, host="127.0.0.1", port=0, rcvbuf=4 * 1024 * 1024):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # A bigger receive buffer lets the OS queue more datagrams while our thread is busy.
        # Without it, a fast sender can overflow the buffer and datagrams vanish silently.
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
        self.sock.bind((host, port))
        self.port = self.sock.getsockname()[1]
        self.sock.settimeout(0.05)
        self.results = []
        self._stop = False
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self._stop:
            try:
                data, addr = self.sock.recvfrom(65535)
            except socket.timeout:
                self.on_idle()
                continue
            except ConnectionResetError:   # Windows reports ICMP "port unreachable" this way; ignore it
                continue
            except OSError:
                break
            self.on_packet(data, addr)

    def on_packet(self, data, addr):
        pass

    def on_idle(self):
        pass

    def close(self):
        self._stop = True
        self.sock.close()


class PlainUdpServer(UdpServer):
    """Collects chunks per client. UDP has no 'end of connection', so a client's transfer
    is declared finished after `idle_timeout` seconds of silence from that client."""

    def __init__(self, *args, idle_timeout=0.4, **kwargs):
        self.sessions = {}
        self.idle_timeout = idle_timeout
        super().__init__(*args, **kwargs)

    def on_packet(self, data, addr):
        if len(data) < HDR.size:
            return
        ptype, seq, total = HDR.unpack(data[:HDR.size])
        if ptype != DATA:
            return
        s = self.sessions.setdefault(addr, {"chunks": {}, "total": total, "last": 0.0, "done": False})
        if not s["done"]:
            s["chunks"][seq] = data[HDR.size:]   # a dict makes duplicate datagrams harmless
            s["last"] = time.perf_counter()
        self._finalize_idle()

    def on_idle(self):
        self._finalize_idle()

    def _finalize_idle(self):
        now = time.perf_counter()
        for s in list(self.sessions.values()):
            if not s["done"] and now - s["last"] > self.idle_timeout:
                s["done"] = True
                # Reassemble in order; missing chunks are simply absent, so the hash will differ.
                data = b"".join(s["chunks"].get(i, b"") for i in range(s["total"]))
                self.results.append({"hash": sha(data), "received": len(s["chunks"]), "total": s["total"]})


def plain_udp_send(host, port, data):
    """Fire all datagrams and hope. There is no way to know if they arrived."""
    chunks = split(data)
    total = len(chunks)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sent_bytes = 0
    t0 = time.perf_counter()
    for i, c in enumerate(chunks):
        pkt = HDR.pack(DATA, i, total) + c
        sock.sendto(pkt, (host, port))
        sent_bytes += len(pkt)
    elapsed = time.perf_counter() - t0
    sock.close()
    return {"elapsed": elapsed, "bytes_sent": sent_bytes, "retransmits": 0, "packets": total}


# --------------------------------------------------------------------------
# 4. Reliable UDP (RUDP): our own mini-TCP on top of UDP
# --------------------------------------------------------------------------
class RudpServer(UdpServer):
    """Acknowledges EVERY data packet (even duplicates, because the earlier ACK may have been lost),
    stores each chunk once, and reports the file when every chunk is present.
    The session is kept after completion so late retransmissions still get an ACK."""

    def __init__(self, *args, **kwargs):
        self.sessions = {}
        super().__init__(*args, **kwargs)

    def on_packet(self, data, addr):
        if len(data) < HDR.size:
            return
        ptype, seq, total = HDR.unpack(data[:HDR.size])
        if ptype != DATA or seq >= total:
            return
        s = self.sessions.setdefault(addr, {"chunks": {}, "done": False})
        s["chunks"].setdefault(seq, data[HDR.size:])
        if not s["done"] and len(s["chunks"]) == total:
            s["done"] = True
            blob = b"".join(s["chunks"][i] for i in range(total))
            self.results.append({"hash": sha(blob), "received": total, "total": total})
        # Record the result BEFORE acknowledging, so the sender only finishes after the file is complete.
        self.sock.sendto(HDR.pack(ACK, seq, total), addr)


def rudp_send(host, port, data, window=16, rto=0.05, max_time=120):
    """Sliding-window sender (selective repeat).
       window = how many unacknowledged packets may be in flight (window=1 is stop-and-wait)
       rto    = retransmission timeout in seconds: resend a packet if no ACK arrives within rto"""
    chunks = split(data)
    total = len(chunks)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.connect((host, port))        # connect() makes the OS ignore datagrams from other senders
    acked = set()
    sent_at = {}                      # seq -> time it was last transmitted
    base = 0                          # lowest sequence number not yet acknowledged
    next_seq = 0                      # next never-sent sequence number
    packets = retransmits = bytes_sent = 0
    t0 = time.perf_counter()

    def transmit(seq):
        nonlocal packets, bytes_sent
        pkt = HDR.pack(DATA, seq, total) + chunks[seq]
        try:
            sock.send(pkt)
        except ConnectionError:       # e.g. server not ready yet; the timer will retry
            pass
        packets += 1
        bytes_sent += len(pkt)
        sent_at[seq] = time.perf_counter()

    while base < total:
        # 1) send new packets while the window has room
        while next_seq < total and next_seq < base + window:
            transmit(next_seq)
            next_seq += 1
        # 2) wait for an ACK, but no longer than the oldest packet's timer
        pending = [sent_at[s] + rto for s in range(base, next_seq) if s not in acked]
        wait = max(0.001, min(pending) - time.perf_counter())
        sock.settimeout(wait)
        try:
            pkt = sock.recv(64)
            if len(pkt) >= HDR.size:
                ptype, seq, _ = HDR.unpack(pkt[:HDR.size])
                if ptype == ACK and seq < total:
                    acked.add(seq)
        except socket.timeout:
            pass
        except ConnectionError:
            time.sleep(0.005)
        # 3) slide the window past everything acknowledged in order
        while base in acked:
            base += 1
        # 4) retransmit every unacknowledged packet whose timer expired
        now = time.perf_counter()
        for seq in range(base, next_seq):
            if seq not in acked and now - sent_at[seq] >= rto:
                transmit(seq)
                retransmits += 1
        if now - t0 > max_time:
            sock.close()
            raise TimeoutError("RUDP transfer did not finish in %d s" % max_time)
    elapsed = time.perf_counter() - t0
    sock.close()
    return {"elapsed": elapsed, "bytes_sent": bytes_sent, "retransmits": retransmits}


# --------------------------------------------------------------------------
# 5. UDP request/response (plain = one try, reliable = retry until answered)
# --------------------------------------------------------------------------
class UdpEchoServer(UdpServer):
    def on_packet(self, data, addr):
        self.sock.sendto(data, addr)


def udp_request_response(host, port, n, reliable, timeout=0.05, max_tries=20):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.connect((host, port))
    latencies, lost, retries = [], 0, 0
    for i in range(n):
        pkt = HDR.pack(DATA, i, n) + b"x" * (REQ_SIZE - HDR.size)
        t = time.perf_counter()
        got = False
        tries = 0
        while not got and tries < (max_tries if reliable else 1):
            tries += 1
            try:
                sock.send(pkt)
            except ConnectionError:
                pass
            deadline = time.perf_counter() + timeout
            while True:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    break
                sock.settimeout(remaining)
                try:
                    resp = sock.recv(2048)
                except socket.timeout:
                    break
                except ConnectionError:
                    time.sleep(0.005)
                    continue
                if len(resp) >= HDR.size and HDR.unpack(resp[:HDR.size])[1] == i:
                    got = True    # (replies to older requests are ignored)
                    break
        if got:
            latencies.append(time.perf_counter() - t)
            retries += tries - 1
        else:
            lost += 1
    sock.close()
    return {"latencies": latencies, "lost": lost, "retries": retries, "connect_time": 0.0}
