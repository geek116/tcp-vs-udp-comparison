# TCP vs UDP Comparison System

A client-server application that sends a file over three transports and compares them under controlled loss and delay:

- **tcp**: length-prefixed framing, one thread per connection
- **udp**: plain UDP, 1000-byte chunks, no reliability
- **rudp**: reliable UDP with sequence numbers, per-packet ACKs, a sliding window (default 16) and retransmission (default timer 0.05 s)

Impairment proxies sit between client and server: a UDP proxy (loss and delay) and a TCP proxy (delay only; it cannot drop TCP packets).

## Requirements

- Python 3 (standard library only for the system itself)
- Windows, Linux or macOS. Tested on Windows.

## Layout

| Path | Purpose |
|---|---|
| `app.py` | Command-line entry point: server, client, proxy |
| `final/netlab.py` | TCP, UDP and RUDP implementations |
| `final/autodoc.py` | Writes each run to a results .txt file (run ID, PASS/FAIL/ERROR, reason) |
| `tests/` | Unit tests, impairment proxies, experiment harness, graph maker |
| `baseline/` | Frozen AI-generated baseline (tagged in git: [TAG NAME]) |
| `docs/ai_log.md` | AI interaction record |

## Quick run (three terminals)

Defaults: server port 9000, proxy listens on 9001 and forwards to 9000, host 127.0.0.1.

```
# Terminal 1: server
python app.py server --proto rudp --port 9000

# Terminal 2: proxy (10% loss, 20 ms delay)
python app.py proxy --proto rudp --listen 9001 --to 9000 --loss 10 --delay-ms 20

# Terminal 3: client (send through the proxy)
python app.py send --proto rudp --port 9001 --file [FILE]
```

Use `--proto tcp` or `--proto udp` in all three terminals for the other transports. The proxy protocol must match the traffic. For TCP the proxy only adds delay (`--delay-ms`).

> Check the exact subcommand and option names against `python app.py --help` before submitting, and edit this section to match.

## Tests

```
python -m unittest discover tests
```

[Edit if the test command differs.]

## Experiments (no server needed)

The harness starts its own servers and proxies on free ports.

```
python -m tests.run_baseline        # frozen baseline, overwrites baseline_results.csv
python -m tests.run_experiments     # improved system, APPENDS to the CSVs
python -m tests.make_graphs         # graphs and summaries (run experiments first)
```

Delete the old `file_transfer.csv` and `request_response.csv` before a fresh run, because experiments append.

Useful options: `--losses 0 5 10`, `--delay-ms 20`, `--clients 1`, `--requests 100`, `--quick`.

## Running on two machines (untested)

Start the server with `--host 0.0.0.0` and give the client the server's IPv4 address. The proxy in `app.py` always listens on 127.0.0.1.

## Known limits

- All experiments run on loopback, not a real network.
- TCP under packet loss is not tested (the TCP proxy adds delay only; real loss would need `tc netem` on Linux).
- RUDP uses a fixed retransmission timer; real TCP adapts its timer.
- Results use three repeats per setting.
