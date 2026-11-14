"""AI BASELINE - TCP file server (kept exactly as first generated).
Usage: python tcp_server.py PORT OUTPUT_FILE
Accepts ONE connection, saves everything received to OUTPUT_FILE, then exits.
"""
import socket
import sys

port = int(sys.argv[1])
out_path = sys.argv[2]

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("127.0.0.1", port))
server.listen(1)
print("listening", flush=True)

conn, addr = server.accept()
with open(out_path, "wb") as f:
    while True:
        data = conn.recv(1024)
        if not data:
            break
        f.write(data)
conn.close()
server.close()
print("done", flush=True)
