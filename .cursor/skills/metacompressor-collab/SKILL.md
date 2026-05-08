---
name: metacompressor-collab
description: >-
  Coordinates MetaCompressor work between the main Cursor agent and the
  metacompressor-specialist subagent: delegation rules, handoff payloads,
  shared pytest/mc commands, and lossless round-trip guardrails. Use when the
  user asks to align the main agent and subagent, mentions collaboration,
  metacompressor-collab, parallel specialist work, or delegated MetaCompressor
  tasks. Use when working in this repo with both Agent and Task/subagent flows.
disable-model-invocation: true
---

# MetaCompressor: main agent ↔ specialist collaboration

Applies whenever this skill is active—whether you are the **main agent** in chat or running as **metacompressor-specialist** (subagent). The goal is shared rules so work is not duplicated and context is not lost across delegation.

Skill evolution and boundaries: **`.cursor/skills/README.md`**. Product mandates and freeze zones: **`AGENTS.md`**. Session anchor and “after `git pull`” checklist: **`docs/policy/METACOMPRESSOR_WORKING_CONTRACT.md`**.

## Shared facts (both roles)

- **Repo root**: default to the MetaCompressor project directory unless the user specifies otherwise.
- **Core goal**: changes under `metacompressor/` must preserve **lossless round-trip** wherever the format guarantees it (`compress` → `decompress` → identical bytes).
- **Tests**: `python -m pytest` from repo root; respect markers in `pyproject.toml` (`small`, `medium`, `large` / `RUN_LARGE_TESTS`).
- **CLI**: `mc` (defined in `pyproject.toml` under `[project.scripts]`).
- **Heavy validation**: scripts under `benchmarks/`; `results/` holds reports—not the specification of behavior.

## Main agent (orchestration)

1. **Delegate to the specialist** when the task needs deep domain work (container format, CDC/fixed chunking, delta, dedup, corpus, compression pipeline performance) or when exploration would bloat the main thread—not for trivial one-line edits.
2. **Send a clear brief** to the subagent: goal, relevant file paths, repro steps or test names, and what was already tried.
3. **After the reply**: fold the specialist’s conclusion into the main answer; state which files/commands were used for verification. Do not paste full intermediate logs if the subagent already summarized them.
4. **Explicit invocation** the user may use: `/metacompressor-specialist …` or natural language such as “use metacompressor-specialist”.

## Specialist subagent (metacompressor-specialist)

1. You have the **same tools** as the parent (files, terminal, MCP if enabled)—use them; do not guess library behavior without reading the code.
2. **Structured reply** to the parent:
   - Short **outcome** (what holds / recommendation).
   - **Evidence**: which files were read, which commands were run (`pytest …`), results.
   - **Changes**: list paths if you modified code.
3. **Do not spawn unnecessary child subagents**; if you need parallel search, briefly justify why.

## Shared checklist before “done”

- [ ] Round-trip / correctness addressed if `compress` / `decompress` / `container` behavior changed.
- [ ] Relevant tests run, or explain why not.
- [ ] No new dependencies without explicit user approval.

## Related files

- Subagent prompt: `.cursor/agents/metacompressor-specialist.md`
- This skill: `.cursor/skills/metacompressor-collab/SKILL.md` (load with `@metacompressor-collab` or when the model selects the skill from its description)
