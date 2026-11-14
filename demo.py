"""demo.py - the live demo for your presentation (about 20 seconds).

Run from the project root:   python demo.py
Sends the same 256 KB file with each protocol, first on a clean network and then through a
network that drops 10% of UDP datagrams in each direction, and prints whether the received
file is identical (SHA-256 match).
"""
from final import netlab
from tests.harness import run_file_transfer

data = netlab.make_data(262144)
print("File: %d bytes, SHA-256 %s...\n" % (len(data), netlab.sha(data)[:16]))

for loss in (0, 10):
    print("--- Network with %d%% packet loss%s ---" % (loss, " (UDP only; TCP shown on a clean path)" if loss else ""))
    for proto, label in [("tcp", "TCP"), ("udp_plain", "Plain UDP"),
                         ("rudp_w1", "Reliable UDP, window 1"), ("rudp_w16", "Reliable UDP, window 16")]:
        if proto == "tcp" and loss:
            continue
        r = run_file_transfer(proto, loss=loss, data=data)
        verdict = "FILE IDENTICAL" if r["ok_clients"] == 1 else "FILE CORRUPT/INCOMPLETE"
        print("  %-24s %-24s delivered %5.1f%%  time %6.2fs  retransmissions %d"
              % (label, verdict, r["delivered_pct"], r["seconds"], r["retransmits"]))
    print()
