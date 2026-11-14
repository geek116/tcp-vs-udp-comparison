"""AI BASELINE - UDP file server (kept exactly as first generated).
Usage: python udp_server.py PORT OUTPUT_FILE
Writes every datagram to OUTPUT_FILE until it receives the datagram b"END".
"""
import socket
import sys

port = int(sys.argv[1])
out_path = sys.argv[2]

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("127.0.0.1", port))
print("listening", flush=True)

with open(out_path, "wb") as f:
    while True:
        data, addr = sock.recvfrom(2048)
        if data == b"END":
            break
        f.write(data)
sock.close()
print("done", flush=True)
