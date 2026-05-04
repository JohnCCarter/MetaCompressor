# Delegate to MetaCompressor specialist

1. Load context: `@metacompressor-collab` if collaboration rules matter for this task.

2. Use the **metacompressor-specialist** subagent (Task) for deep work on: `.mc1` format, fixed/CDC chunking, xxhash dedup, delta encoding, `MC1Container` / serialization, `compress`/`decompress`, corpus tooling, or heavy exploration under `metacompressor/` and `benchmarks/`.

3. In the subagent brief include: goal, relevant paths, repro steps or failing test names, and what was already tried in this chat.

4. When the subagent finishes, merge its outcome into the main answer: outcome, evidence (commands run), and changed files—without duplicating full logs unnecessarily.
