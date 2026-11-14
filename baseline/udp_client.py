"""AI BASELINE - UDP file client (kept exactly as first generated).
Usage: python udp_client.py PORT INPUT_FILE
Sends the file in 1024-byte datagrams, then sends b"END".
"""
import socket
import sys

port = int(sys.argv[1])
in_path = sys.argv[2]

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
with open(in_path, "rb") as f:
    while True:
        chunk = f.read(1024)
        if not chunk:
            break
        sock.sendto(chunk, ("127.0.0.1", port))
sock.sendto(b"END", ("127.0.0.1", port))
sock.close()
print("sent", flush=True)
