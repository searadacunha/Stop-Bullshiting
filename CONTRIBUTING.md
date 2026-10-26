# Contributing

Please keep the project offline-first, reversible, testable and dependency-light.

1. Create a focused branch for one feature or bug fix.
2. Add unit tests, especially for truncated diagnostics, tool output schemas and preservation of original exit status.
3. Run `python3 -m unittest discover -s tests -v`.
4. Never claim actual token savings based on character-length reductions. Benchmark against agent usage counters separately.
5. Avoid changing users' authentication or adding background services.
6. Do not submit vendor code without checking and preserving applicable license attribution.

The core implementation is intentionally self-contained.
