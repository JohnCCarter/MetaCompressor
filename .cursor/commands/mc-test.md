# Run MetaCompressor tests

From the repository root:

1. Run the default fast path:

   `python -m pytest metacompressor/tests -q -m small`

2. If failures appear, re-run the narrowest file that matches the changed code and paste the failure summary with file and test name.

3. Do **not** run `large` / expensive markers unless the user explicitly asks or `RUN_LARGE_TESTS` is already part of the task.

Report: command used, pass/fail counts, and any follow-up needed.
