"""impairment.py - a UDP proxy that simulates a bad network.

Sits between client and server. Every datagram passing through, in EITHER direction,
is dropped with probability `loss` and otherwise delayed by `delay` seconds.

Use this when you cannot run `tc netem` (Windows, macOS, no admin rights).
Limitation: it can only impair UDP. TCP loss needs tc netem (see README).
"""
import heapq
import queue
import random
import socket
import threading
import time


class UdpProxy:
    def __init__(self, server_addr, loss=0.0, delay=0.0, listen_host="127.0.0.1", listen_port=0, seed=None):
        self.server_addr = server_addr
        self.loss = loss
        self.delay = delay
        self.rng = random.Random(seed)
        self.front = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)   # clients talk to this socket
        self.front.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        self.front.bind((listen_host, listen_port))
        self.front.settimeout(0.1)
        self.port = self.front.getsockname()[1]
        self.backs = {}              # client address -> socket used to talk to the real server
        self.dropped = 0             # both directions
        self.forwarded = 0           # both directions
        self.c2s_received = 0        # client -> server direction only (used to detect buffer-overflow loss)
        self.c2s_dropped = 0
        self.c2s_forwarded = 0
        self._stop = False
        self._heap = []              # delayed packets: (send_time, counter, socket, data, destination)
        self._counter = 0
        self._cv = threading.Condition()
        threading.Thread(target=self._client_to_server, daemon=True).start()
        threading.Thread(target=self._delay_sender, daemon=True).start()

    def _emit(self, sock, data, dest, c2s=False):
        """Apply loss and delay, then send. c2s=True marks the client->server direction."""
        if self.rng.random() < self.loss:
            self.dropped += 1
            self.c2s_dropped += c2s
            return
        self.forwarded += 1
        self.c2s_forwarded += c2s
        if self.delay <= 0:
            try:
                sock.sendto(data, dest)
            except OSError:
                pass
            return
        with self._cv:
            self._counter += 1
            heapq.heappush(self._heap, (time.perf_counter() + self.delay, self._counter, sock, data, dest))
            self._cv.notify()

    def _delay_sender(self):
        while not self._stop:
            with self._cv:
                while not self._heap and not self._stop:
                    self._cv.wait(0.1)
                if self._stop:
                    return
                due = self._heap[0][0]
                now = time.perf_counter()
                if due > now:
                    self._cv.wait(due - now)
                    continue
                _, _, sock, data, dest = heapq.heappop(self._heap)
            try:
                sock.sendto(data, dest)
            except OSError:
                pass

    def _client_to_server(self):
        while not self._stop:
            try:
                data, client = self.front.recvfrom(65535)
            except socket.timeout:
                continue
            except ConnectionResetError:
                continue
            except OSError:
                return
            back = self.backs.get(client)
            if back is None:
                back = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                back.settimeout(0.1)
                self.backs[client] = back
                threading.Thread(target=self._server_to_client, args=(back, client), daemon=True).start()
            self.c2s_received += 1
            self._emit(back, data, self.server_addr, c2s=True)

    def _server_to_client(self, back, client):
        while not self._stop:
            try:
                data, _ = back.recvfrom(65535)
            except socket.timeout:
                continue
            except ConnectionResetError:
                continue
            except OSError:
                return
            self._emit(self.front, data, client)

    def close(self):
        self._stop = True
        with self._cv:
            self._cv.notify_all()
        time.sleep(0.15)
        for s in [self.front, *self.backs.values()]:
            try:
                s.close()
            except OSError:
                pass


class TcpDelayProxy:
    """TCP relay that adds `delay` seconds of one-way latency to every chunk of data in both directions.

    It cannot DROP TCP segments (the OS's TCP would just retransmit them invisibly), so for real TCP loss use
    `tc netem`. It also does not delay the TCP handshake itself (the client connects to the proxy locally).
    """

    def __init__(self, server_addr, delay=0.0, listen_host="127.0.0.1", listen_port=0):
        self.server_addr = server_addr
        self.delay = delay
        self.dropped = 0           # always 0: kept so it can be used interchangeably with UdpProxy
        self.c2s_dropped = 0
        self._stop = False
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((listen_host, listen_port))
        self.sock.listen(128)
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        while not self._stop:
            try:
                client, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                upstream = socket.create_connection(self.server_addr, timeout=10)
            except OSError:
                client.close()
                continue
            threading.Thread(target=self._relay, args=(client, upstream), daemon=True).start()

    def _relay(self, client, upstream):
        a = threading.Thread(target=self._pump, args=(client, upstream), daemon=True)
        b = threading.Thread(target=self._pump, args=(upstream, client), daemon=True)
        a.start()
        b.start()
        a.join()
        b.join()
        client.close()
        upstream.close()

    def _pump(self, src, dst):
        """Read from src; hand each chunk to a sender thread that releases it `delay` seconds later (order preserved)."""
        q = queue.Queue()

        def sender():
            while True:
                item = q.get()
                if item is None:
                    break
                due, data = item
                wait = due - time.perf_counter()
                if wait > 0:
                    time.sleep(wait)
                try:
                    dst.sendall(data)
                except OSError:
                    break
            try:
                dst.shutdown(socket.SHUT_WR)       # pass the end-of-stream on to the other side
            except OSError:
                pass

        t = threading.Thread(target=sender, daemon=True)
        t.start()
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                q.put((time.perf_counter() + self.delay, data))
        except OSError:
            pass
        finally:
            q.put(None)
            t.join()

    def close(self):
        self._stop = True
        self.sock.close()
