# Development validation

Date: 2026-10-23. These results describe the development environment, not either
of the user's machines.

| Check | Result |
| --- | --- |
| Rust model/engine tests | 15 passed |
| Rust formatting and Clippy, all targets | Passed, warnings treated as errors |
| Python comparison-checker tests | 11 passed |
| Committed fixture vs Rust export | Byte-for-byte identical |
| Six proposed gate laws | Passed the Bend source type checker |
| Negative proof check | Making the busy gate request full training was rejected at `already_busy` |
| Native CPU, one thread | All 24 rows matched the Rust reference on every checked run |
| Native CPU, four threads | All 24 rows matched the Rust reference on every checked run |
| Largest numerical difference | Approximately `4.35e-7`, across 19,080 values per run |
| GPU entry point | Type-checked and emitted C with `BANGS = 1`; no GPU execution here |
| BendTT / Lean kernel verdict | Not run locally; `--verdict` is an explicit required gate when selected |
| Actual Fedora / Ryzen 7 4700U | Not run |
| Actual Apple M1 CPU / Metal | Not run |

The local environment was Linux x86_64 with Rust 1.99.1, Clang 18.1.3 and Node
24.19.0. Bun 1.4.4 aborted at startup in this environment. Local checking and C
emission therefore called the **unchanged pinned Bend modules** (`book_load`,
`book_valid`, `compile_book`) through a small temporary Node adapter. It also
checked for unfilled holes and the required laws import. The generated C was
compiled with `-O3 -ffp-contract=off`, linked with pthread and libm, and executed
natively. This is not a successful run of the official Bun CLI or a BendTT kernel
verdict. The adapter is explicitly identified in the local reports.

The checked-in CI workflow uses the ordinary pinned Bun CLI and Clang rather
than the local adapter. It checks both one-thread and four-thread native execution,
compares the reports, and uploads them as the `bend-cpu-comparison` artifact.
Consult the PR's actual workflow status for that independent run. The CI machine
is not a substitute for a 4700U or M1 hardware test.

Reported timings include process startup and JSON output and are not presented
as hardware-performance results. No power measurements or GPU profiling were
performed. The next evidence to collect is the five reports listed in the README
from the actual 4700U and M1, followed by a required BendTT verdict if the proof
kernel is available.
