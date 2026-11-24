# AI Interaction Record

Project: TCP vs UDP Comparison System (solo project). AI tool: Claude (Anthropic).

---

## Entry 1: AI baseline (TCP and UDP client/server)

- **Date / AI tool used:** 11/14/2026, Claude
- **Goal:** Get a working first version of a TCP and a UDP client/server to compare.
- **What I asked for:** A working TCP and UDP client/server pair to compare.
- **What the AI produced:** `baseline/tcp_server.py`, `tcp_client.py`, `udp_server.py`, `udp_client.py`. The scripts take the port on the command line. I froze them in git as the tag [TAG NAME].
- **What worked:** TCP and UDP both transferred the 256 KB file correctly at 0% loss (3 of 3 runs, hashes matched).
- **What failed or was incomplete (evidence):** From `baseline_results.csv`, UDP at 5% loss delivered about 250,500 of 262,144 bytes (95.6%) and at 10% about 231,100 bytes (88.2%), with 0 of 3 runs correct. The sender reported success every time. It also has no default port and no error handling.
- **Networking knowledge needed to notice / fix it:** UDP gives no delivery guarantee and no signal to the sender when a datagram is lost, so a transfer can look successful and still be corrupt. I needed to compare SHA-256 hashes instead of trusting "done".
- **How I changed it (file, commit):** I did not change the baseline. I froze it and measured it as the "before" ([commit hash]).
- **Result after the change (measurement):** Baseline measured with `python -m tests.run_baseline`; results in `baseline_results.csv`.

---

## Entry 2: Reliable UDP (RUDP) and the test harness

- **Date / AI tool used:** 11/14/2026, Claude
- **Goal:** Add a reliability layer over UDP (sequence numbers, acknowledgements, retransmission) and a repeatable way to measure it.
- **What I asked for:** A reliability layer over UDP (sequence numbers, acknowledgements, retransmission) and a repeatable way to measure it.
- **What the AI produced:** `final/netlab.py` (TCP, plain UDP, RUDP), `app.py`, `tests/impairment.py` (loss and delay proxies), `tests/harness.py`, `run_experiments.py`, `make_graphs.py` and 13 tests. RUDP uses a 9-byte header (type, seq, total), a per-packet ACK, a sliding window (default 16) and a fixed 0.05 s timer.
- **What worked:** RUDP delivered a byte-identical file in every test at 0, 5 and 10% loss, including with 10 clients.
- **What failed or was incomplete (evidence):** I avoided using port below 1024. Its generally advisable for UDP and TCP testing, both to avoid privileged system ports and reduce conflicts with standard services.
- **Networking knowledge needed:** I checked the header size, the ACKs (the server must ACK duplicates, because an earlier ACK may have been lost) and the window with small practice scripts. Window 1 is stop-and-wait: one round trip per packet.
- **How I changed it (file, commit):** I used several port lower than 1024 just for test and it failed. So i tried the ones above 1024 in range and that worked.
- **Result after the change (measurement):** At 20 ms delay and 0% loss, window 1 took 16.4 s against 1.07 s for window 16 (about 15 times). At 5% and 10% loss window 16 was 2.3 and 3.3 times faster (1 client, 256 KB).

---

## Entry 3: Missing-file crash

- **Date / AI tool used:** 11/15/2026, Claude
- **Goal:** Make errors visible and clean instead of crashing.
- **What I asked for:** A fix for the client crash I found when the input file did not exist.
- **What the AI produced:** A suggestion to handle the file read error and the send error in `cmd_send`.
- **What worked:** After the change the client prints `TRANSFER FAILED: cannot read file ...` and exits with code 1.
- **What failed or was incomplete (evidence):** I found by testing that sending a file that does not exist crashed the client with `FileNotFoundError` and a traceback. On Windows I also hit `WinError 32` (a file lock) and `WinError 10048` (port in use) while running the baseline script.
- **Networking knowledge needed:** TCP reports a missing server at once (connection refused, error 10061). Plain UDP cannot detect that nobody is listening and reports success. RUDP retries and then times out.
- **How I changed it (file, commit):** `app.py` I found this error by testing: sending a file that did not exist crashed the client with a FileNotFoundError traceback. I updated app.py so that the client catches the file-read error in cmd_send and prints a single clear message, TRANSFER FAILED: cannot read file ..., then exits with code 1. I then re-tested it, and the case is now recorded as an ERROR entry in my results file.
- **Result after the change (measurement):** The missing-file case and the refused connection are recorded as ERROR entries in my results file (Stage 4).

---

## Entry 4: The TCP proxy cannot drop packets

- **Date / AI tool used:** 11/16/2026, Claude
- **Goal:** Test TCP under packet loss like UDP.
- **What I asked for:** A way to test TCP under packet loss like UDP.
- **What the AI produced:** A TCP relay that adds delay only. Its startup line says 0% loss.
- **What worked:** TCP through the delay relay delivered 300 of 300 chunks (PASS, 0.058 s).
- **What failed or was incomplete (evidence):** TCP under loss is not measured, so the loss graphs have no TCP line.
- **Networking knowledge needed:** A user-space program cannot make TCP lose packets because the operating system retransmits them invisibly. Real TCP loss needs `tc netem` on Linux.
- **How I changed it (file, commit):** I did not change the code. I documented it as a limit and kept the TCP claims to delay only.
- **Result after the change (measurement):** Report states TCP was tested at 0% loss only.

---

## Entry 5: Fixed retransmission timer

- **Date / AI tool used:** 11/17/2026, Claude
- **Goal:** Understand why a live RUDP run had far more retransmissions than the loss explains.
- **What I asked for:** An explanation of why a live run had far more retransmissions than the loss explained.
- **What the AI produced:** First an explanation that the fixed 50 ms timer was the cause, which the AI had not verified. Its own test did not reproduce the number and the AI retracted the claim.
- **What worked:** My own timer runs: 0.05 s gave about 365 retransmissions (2.4 s), 0.15 s gave 75 (5.1 s), 0.3 s gave 59 (6.0 s).
- **What failed or was incomplete (evidence):** `app.py` keeps a fixed 0.05 s timer, while the AI's own harness uses `0.05 + 3 x delay`. Through the UDP proxy with 20 ms delay the round trip was about 62 ms (TCP: 41 ms), longer than 50 ms. The cause is not confirmed until my control run (`--loss 0 --delay-ms 20`, default timer): [result].
- **Networking knowledge needed:** A retransmission timeout must exceed the round-trip time, or packets are resent while their ACK is still on its way. TCP measures the round trip and adapts its timer.
- **How I changed it (file, commit):** I did not change anything. Its just important to understand that RUDP will have more retransmissions in other to evetually complete it task.
- **Result after the change (measurement):** No change made. But I recommend --rto 0.15: it removes most of the wasted retransmissions while costing less time than 0.3 s. This is a recommendation from three measurements on one computer, not a proven optimum, and the best value depends on the network’s round-trip time.

---

## Entry 6: Automatic results documentation (my own work)

- **Date / AI tool used:** 11, Claude
- **Goal:** Record every live run in a readable file straight away, not only at Stage 5.
- **What I asked for:** Help with automatic output documentation. I reverted the AI's design and wrote my own.
- **What the AI produced (disclosure):** Earlier the AI showed me a logging design and an ideas list. I did not like how the log looked, reverted it, and wrote `final/autodoc.py` myself. 
- **What worked:** Each run is saved with a unique run ID, a PASS, FAIL or ERROR verdict with the reason, the commands, both SHA-256 hashes, chunk counts, time and retransmissions.
- **What failed or was incomplete (evidence):** No Failure
- **Networking knowledge needed:** To decide what proves PASS or FAIL: matching hashes and full chunk count for delivery, and why plain UDP can fail without an error.
- **How I changed it (file, commit):** `final/autodoc.py`, `app.py` . We wanted some documentation that shows more detail with reason and purpose of command with the result of the specific command
- **Result after the change (measurement):** Entries recorded for TCP (PASS), plain UDP (FAIL, 260 of 300), reliable UDP (PASS, about 366 retransmissions), window and timer runs, and the Stage 4 errors.
