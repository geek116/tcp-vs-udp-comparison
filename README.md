# TCP vs UDP Comparison System

Compares TCP, plain UDP and a reliable UDP (RUDP) that we built ourselves (sequence numbers, ACKs,
retransmission timer, sliding window) under packet loss, delay and multiple clients.
Standard library only, except `matplotlib` for graphs. Python 3.10+.

## Layout

| Path | What it is |
|---|---|
| `baseline/` | The FROZEN AI-generated first version (4 small scripts). Never edit these. |
| `final/netlab.py` | The improved system: TCP, plain UDP, reliable UDP, request/response |
| `tests/impairment.py` | UDP proxy that drops/delays datagrams (works on any OS) |
| `tests/harness.py` | Runs one experiment and returns measurements |
| `tests/test_correctness.py` | Pass/fail tests that the system works |
| `tests/run_baseline.py` | Benchmarks the frozen baseline |
| `tests/run_experiments.py` | The main evaluation (writes CSVs to `results/`) |
| `tests/make_graphs.py` | CSVs -> graphs + summary table |
| `app.py` | **The interactive client-server application: the user selects TCP, UDP or reliable UDP** (also starts the loss/delay proxy) |
| `demo.py` | 20-second automatic demo for the presentation |
| `docs/ai_log.md` | Your AI interaction record (fill this in as you go) |

## The application (user selects the protocol)

Open two or three terminals in this folder. Make a test file first, e.g. `python -c "import os;open('myfile.bin','wb').write(os.urandom(300000))"`.

```
# terminal 1 - server (choose: tcp | udp | rudp)
python app.py server --proto rudp --port 9000
# terminal 2 - client; compare the SHA-256 it prints with the one the server prints
python app.py send --proto rudp --port 9000 --file myfile.bin
```
With a bad network in between (terminal 3; works on every OS), then send to the PROXY's port 9001:
```
python app.py proxy --proto udp --listen 9001 --to 9000 --loss 10 --delay-ms 20
python app.py send  --proto rudp --port 9001 --file myfile.bin
```
Try `--proto udp` against the same proxy to see plain UDP lose data (server hash differs).
For `--proto tcp` the proxy can add delay only (`--proto tcp` on the proxy); real TCP loss needs `tc netem`.
Errors (server down, refused connection, timeout) print `TRANSFER FAILED` instead of crashing.

## Run order for the evaluation (all commands from this folder)

```
python -m unittest tests.test_correctness -v     # 1. does it work?           (~15 s)
python demo.py                                   # 2. visual proof            (~10 s)
python -m tests.run_baseline                     # 3. measure the AI baseline (~30 s)
python -m tests.run_experiments --quick          # 4. sanity-check the harness (~15 s)
python -m tests.run_experiments                  # 5. loss + concurrent clients (~2 min)
python -m tests.run_experiments --losses 0 5 --delay-ms 20 --clients 1 --requests 100   # 6. added delay (~4 min)
python -m tests.make_graphs                      # 7. graphs + tables in results/
```

## Network impairment

* **Default (proxy mode)**: `tests/impairment.py` sits between UDP client and server and drops/delays
  datagrams (in both directions). It cannot impair TCP, so TCP is measured on the clean path as the reference.
* **Added delay**: `--delay-ms N` adds N ms in EACH direction for every protocol (a TCP relay in `tests/impairment.py`
  handles TCP). The relay does not delay the TCP handshake itself; `tc netem` does.
* **Real TCP loss (Linux / WSL2 only)**:
  ```
  sudo tc qdisc add dev lo root netem loss 5%
  python -m tests.run_experiments --mode netem --loss 5 --clients 1
  sudo tc qdisc del dev lo root          # ALWAYS remove it afterwards
  ```
  Repeat for other loss levels. On loopback netem acts on both directions of every packet.

## What the results files contain

| File | Answers |
|---|---|
| `summary_table.md`, `completion_time.png`, `delivery.png`, `clients10.png` | speed, throughput, delivery rate, reliability, concurrent clients |
| `rr_latency.png`, `rr_success.png` | latency (p50/p95/p99) of small request/response exchanges |
| `summary_delay.md`, `delay_effect.png` | effect of added delay |
| `baseline_vs_final.md`, `baseline_vs_final.png` | AI baseline vs improved system |

## How the reliable UDP works (`rudp_send` / `RudpServer`)

1. The file is cut into 1000-byte chunks; each datagram has a 9-byte header: type, sequence number, total chunks.
2. The sender keeps up to `window` unacknowledged datagrams in flight (window 1 = stop-and-wait).
3. The server ACKs every datagram it receives, including duplicates (the earlier ACK may have been lost).
4. If no ACK arrives within the timeout (RTO = 50 ms), the sender retransmits that datagram.
5. The server stores each chunk once (duplicates are harmless), puts the file back in order, and records its SHA-256.
6. The transfer is finished when every chunk has been acknowledged.

## Known limitations (state these honestly in the report)

* Fixed RTO (no adaptive RTT estimation), no congestion control, no flow control beyond the fixed window.
* The harness runs server, proxy and clients as threads in ONE Python process, so absolute timings include
  interpreter overhead and thread contention. Compare protocols with each other, not with real networks.
* Loopback has near-zero latency; use `--delay-ms` or `tc netem` to add delay.
* CPU time (`cpu_s` in the CSV) is for the whole test process, not per protocol.
* If a machine's UDP receive buffer is small (Linux default `net.core.rmem_max` = 212992), plain UDP can lose
  datagrams to buffer overflow. The `unintended_loss` column shows this: it must be 0 for a clean experiment.
