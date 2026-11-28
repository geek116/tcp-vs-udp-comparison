# TCP vs UDP Comparison System

A client-server application that sends a file over three transports and compares them under controlled packet loss and delay:

- **tcp**: length-prefixed framing, one thread per connection
- **udp**: plain UDP, 1000-byte chunks, no reliability
- **rudp**: reliable UDP with sequence numbers, per-packet ACKs, a sliding window (default 16) and retransmission (default timer 0.05 s)

Impairment proxies sit between client and server: a UDP proxy (loss and delay) and a TCP proxy (delay only; it cannot drop TCP packets).

## Requirements

- Python 3.10 or newer (standard library only for the system itself)
- `matplotlib`, only for the graphs (`pip install matplotlib`)
- Tested on Windows (PowerShell)

## Layout

| Path | Purpose |
|---|---|
| `app.py` | Command-line entry point: `server`, `send` (client) and `proxy` |
| `final/netlab.py` | TCP, plain UDP and reliable UDP implementations |
| `final/autodoc.py` | Records every live run to `results/test_output.txt` (run ID, PASS / FAIL / ERROR and reason) |
| `demo.py` | 20-second visual demo of all three protocols |
| `tests/` | 13 correctness tests, impairment proxies, experiment harness, graph maker |
| `baseline/` | Frozen AI-generated baseline (git tag: [TAG NAME]) |
| `results/` | All output: `test_output.txt`, CSV files, summary tables and graphs |
| `docs/ai_log.md` | AI interaction record |

## Run it (step by step)

Run every command from the project folder.

### 1. Check that it works (about 15 s)

```
python -m unittest tests.test_correctness -v
```

Expect 13 tests, all OK.

### 2. Quick visual demo (about 10 s)

```
python demo.py
```

Sends the same file with each protocol, clean and then with 10% loss, and prints whether the SHA-256 hashes match. Plain UDP should fail under loss; TCP and reliable UDP should pass.

### 3. Live run (three terminals)

Defaults: host 127.0.0.1 (this computer), server port 9000, proxy listens on 9001 and forwards to 9000.

Make a test file once:

```
python -c "import os;open('myfile.bin','wb').write(os.urandom(300000))"
```

```
# Terminal 1: server
python app.py server --proto rudp --port 9000

# Terminal 2: proxy (10% loss, 20 ms delay)
python app.py proxy --proto udp --listen 9001 --to 9000 --loss 10 --delay-ms 20

# Terminal 3: client (sends to the PROXY port)
python app.py send --proto rudp --port 9001 --file myfile.bin
```

Repeat with `--proto udp` (hashes differ, FAIL) or `--proto tcp` (the TCP proxy adds delay only). The proxy protocol must match the traffic: `tcp` with tcp, `udp` for udp and rudp. Stop each program with `Ctrl+C` before changing protocol. If you see `WinError 10048` (port in use), close old terminals or run `taskkill /F /IM python.exe`.

Useful client options: `--window 1` (stop-and-wait; default 16), `--rto 0.15` (retransmission timer in seconds; default 0.05), `--timeout 60`.

**Ports.** Any free port from 1024 to 65535 works; avoid ports below 1024 (reserved for standard services). The numbers must line up: the server `--port` is the proxy `--to`, and the client `--port` is the proxy `--listen` (or the server port when there is no proxy). For example: server 6000, proxy `--listen 6500 --to 6000`, client `--port 6500`.

**More scenarios.** Each test uses the same three terminals (server, proxy, client). Terminal 1 (the server) is the same line every time except for the protocol, so it is shown once in the first column. Change only the proxy line (Terminal 2) and, for reliable UDP, the client options. Replace `<proto>` with `tcp`, `udp` or `rudp`, and use the same protocol in the client line.

| Scenario | Terminal 1 (server) | Terminal 2 (proxy) | Terminal 3 (client) | Expected |
|---|---|---|---|---|
| Direct, no proxy | `server --proto <proto> --port 9000` | not used | `send --proto <proto> --port 9000 --file myfile.bin` | tcp, udp, rudp: PASS |
| Delay only | same | `proxy --proto tcp` (or `udp`) `--listen 9001 --to 9000 --delay-ms 20` | `--port 9001` | PASS, slower |
| Loss 5% or 10% (udp, rudp) | same | `proxy --proto udp --listen 9001 --to 9000 --loss 5` (or `10`) | `--port 9001` | udp FAIL; rudp PASS |
| Loss 10% + 20 ms delay | same | `proxy --proto udp --listen 9001 --to 9000 --loss 10 --delay-ms 20` | `--port 9001` | udp FAIL; rudp PASS, slower |
| Reliable UDP window | `--proto rudp` | same proxy | add `--window 1` or `--window 16` | window 1 about ten times slower |
| Reliable UDP timer | `--proto rudp` | same proxy | add `--rto 0.05`, `0.15` or `0.3` | lower timer: more retransmissions |

All commands start with `python app.py`. The proxy protocol must match the traffic: `tcp` for tcp, `udp` for udp and rudp. The server `--port` must equal the proxy `--to`.

The TCP proxy adds delay only, so loss tests are for udp and rudp. The full guide, with the exact Terminal 1, 2 and 3 commands for every test, is in `docs/How_To_Run_TCP_vs_UDP_Project.docx`.

### 4. Error cases (no server running)

```
python app.py send --proto tcp --port 9000 --file nothere.bin
python app.py send --proto tcp --port 9000 --file myfile.bin
```

The first is a missing file, the second a refused connection. Both print `TRANSFER FAILED` and do not crash.

### 5. Full evaluation (no server needed)

The harness starts its own servers and proxies. Delete the old CSVs first, because the experiments append to them:

```
Remove-Item results\file_transfer.csv, results\request_response.csv -ErrorAction SilentlyContinue
python -m tests.run_baseline
python -m tests.run_experiments
python -m tests.run_experiments --losses 0 5 --delay-ms 20 --clients 1 --requests 100
python -m tests.make_graphs
```

Run `make_graphs` last; it fails if the experiments have not run. Do not use `--quick`: it writes short 64 KB runs into the same CSV files and they get averaged with the real 256 KB runs.

## Test scenarios at a glance

Every test uses three terminals: **Terminal 1** the server, **Terminal 2** the proxy (the network condition) and **Terminal 3** the client. The exact commands for each test are in `docs/How_To_Run_TCP_vs_UDP_Project.docx`. "udp" means plain UDP (no reliability); "rudp" means reliable UDP.

| # | Scenario | tcp | udp (plain) | rudp (reliable) |
|---|---|---|---|---|
| 1 | Direct, no proxy | PASS | PASS | PASS |
| 2 | Delay only (20 ms) | PASS (slower) | PASS | PASS (slower) |
| 3 | Loss 5% | not possible* | FAIL | PASS |
| 4 | Loss 10% | not possible* | FAIL | PASS |
| 5 | Loss 10% + delay 20 ms | not possible* | FAIL | PASS (slower) |
| 6 | RUDP window 1 against 16 | n/a | n/a | both PASS; window 1 about ten times slower |
| 7 | RUDP timer 0.05 / 0.15 / 0.3 s | n/a | n/a | lower timer: more retransmissions, faster |
| 8 | Control: delay, no loss, default timer | n/a | n/a | shows whether the timer alone causes retransmissions |
| E | Errors: missing file, no server | ERROR | ERROR (no server: no error shown) | ERROR |

\* The TCP proxy adds delay only; it cannot drop TCP packets.

## Results at a glance

From the experiment runs (256 KB file, three repetitions, one computer over loopback). The full tables and graphs are in `results/` and the report.

- **Reliability under loss:** plain UDP never delivered a correct file at 5% or 10% loss (about 93% and 90% of the data arrived), and the AI baseline UDP behaved the same way (0 of 3 runs correct). Reliable UDP delivered a byte-identical file in every run, including 10 concurrent clients.
- **Cost of reliability:** 0.78 to 3.9 s for one client instead of milliseconds, with about 11% overhead at 5% loss and 23 to 25% at 10% loss.
- **Window:** window 16 was 2.3 to 3.3 times faster than stop-and-wait under loss, and about 15 times faster with 20 ms delay (1.07 s against 16.4 s).
- **Timer:** the fixed 0.05 s timer gave about 365 retransmissions in a live run, against 75 at 0.15 s and 59 at 0.3 s.
- **TCP:** correct in every test, but only tested without packet loss.

## Where the output is saved

| What | Where |
|---|---|
| Every live run (steps 3 and 4) | `results/test_output.txt`: a new entry per run |
| AI baseline measurements | `results/baseline_results.csv` (overwritten each run) |
| Experiment data | `results/file_transfer.csv`, `results/request_response.csv` (appended each run) |
| Tables | `results/summary_table.md`, `summary_file_transfer.csv`, `summary_delay.md`, `baseline_vs_final.md` |
| Graphs | `results/*.png` (baseline_vs_final, delivery, completion_time, clients10, delay_effect, rr_latency, rr_success) |

### Reading `results/test_output.txt`

Each live run adds one entry with a unique run ID, a verdict and the evidence: the protocol, the network (through the proxy or not), the file, the client and server commands, both SHA-256 hashes, chunks at the server, time, retransmissions, and a line on what the run proves.

| Verdict | Meaning |
|---|---|
| PASS | The received file is identical (hashes match) and all chunks arrived |
| FAIL | The transfer ran but the file is not identical (hashes differ or chunks are missing), for example plain UDP under loss |
| ERROR | The transfer could not run (missing file, connection refused) |

Check the newest entry after each live run. Plain UDP under loss should FAIL; TCP and reliable UDP should PASS; the step 4 cases should be ERROR.

## Running on two machines (untested)

Start the server with `--host 0.0.0.0` and give the client the server's IPv4 address. The proxy in `app.py` always listens on 127.0.0.1.

## Known limits

- All experiments run on loopback, not a real network.
- TCP under packet loss is not tested (the TCP proxy adds delay only; real loss would need an operating-system-level loss tool).
- RUDP uses a fixed retransmission timer; real TCP adapts its timer. Through the UDP proxy with 20 ms delay on Windows, the measured round trip was about 62 ms, longer than the 0.05 s default timer.
- Results use three repetitions per setting, on one computer.
