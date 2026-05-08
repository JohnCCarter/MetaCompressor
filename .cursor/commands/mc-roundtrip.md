# Verify lossless round-trip

For the current change or files the user specifies:

1. Confirm whether `compress` / `decompress` / `container` semantics are touched.

2. Run targeted pytest from repo root, e.g.:

   - `python -m pytest metacompressor/tests/core/test_compressor.py -q`
   - Add `metacompressor/tests/core/test_delta.py` or other `test_*.py` files under `metacompressor/tests/` if edits warrant it.

3. Explicitly consider edge cases: empty payload, single-chunk boundary, CDC vs fixed if relevant.

Do not treat the task as done until the relevant tests pass or the user accepts a documented exception.
