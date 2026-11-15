One-way delay = 20 ms (applied in each direction)

| Protocol | Loss % | File transfer time s (mean) | Correct files | Request p50 ms | Request p95 ms |
|---|---|---|---|---|---|
| TCP | 0 | 0.058 | 1.0 | 41.6 | 42.1 |
| Plain UDP | 0 | 0.004 | 1.0 | 64.2 | 64.8 |
| Reliable UDP (window 1) | 0 | 16.892 | 1.0 | - | - |
| Reliable UDP (window 16) | 0 | 1.078 | 1.0 | 64.2 | 64.7 |
| Plain UDP | 5 | 0.004 | 0.0 | 64.3 | 64.9 |
| Reliable UDP (window 1) | 5 | 20.518 | 1.0 | - | - |
| Reliable UDP (window 16) | 5 | 2.402 | 1.0 | 64.3 | 176.0 |
