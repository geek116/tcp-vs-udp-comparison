"""AI BASELINE - TCP file client (kept exactly as first generated).
Usage: python tcp_client.py PORT INPUT_FILE
"""
import socket
import sys

port = int(sys.argv[1])
in_path = sys.argv[2]

client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client.connect(("127.0.0.1", port))
with open(in_path, "rb") as f:
    while True:
        chunk = f.read(1024)
        if not chunk:
            break
        client.sendall(chunk)
client.close()
print("sent", flush=True)
