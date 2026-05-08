# MetaCompressor Roadmap - Speed + Adoption

## Phase 0 - Stabilize Current State

**Goal:** Lock what already works.

**Do:**
- verify latest commits are clean
- keep runtime substitution marked NO-GO
- preserve docs/evidence
- keep tokenization micro-opts stopped unless new profiling proves need

**Exit:**
- git status understood
- tests green
- current speedup evidence documented

## Phase 1 - Higher-Level Build Profiling

**Goal:** Find the next real bottleneck after tokenization.

**Measure:**
- input walk
- template extraction
- row/column model build
- msgpack object build
- serialization
- zstd
- determinism benchmark overhead
- memory/materialization

**Output:**
- ranked cost report
- one recommended safe target

**Exit:**
- top bottleneck identified with 3x median evidence

## Phase 2 - ZSTD-Affine Shaping Research

**Goal:** Make MC feed ZSTD byte layouts it compresses faster/better.

**Research:**
- stable field ordering
- repeated structural markers
- prefix/suffix clustering
- template grouping
- column locality
- dictionary-token substitution
- delta-friendly numeric lanes

**Rules:**
- no wire-format change initially
- report-only first
- compare ratio + encode time + decode time

**Exit:**
- one shaping candidate shows measurable benefit

## Phase 3 - Single Safe Shaping Experiment

**Goal:** Implement one bounded ZSTD-affine transform.

**Rules:**
- feature flag only
- output lossless
- deterministic
- fallback if ratio/speed worsens
- no broad architecture change

**Measure:**
- ratio vs current MC
- encode/decode time
- memory
- output correctness

**Exit:**
- keep only if net-positive

## Phase 4 - Warm-Path / Decision Reuse Expansion

**Goal:** Use receipts/manifests where they are already safe.

**Improve:**
- analysis-skip eligibility
- decision reuse
- stale metadata diagnostics
- warm-path reporting
- no artifact substitution

**Exit:**
- warm unchanged datasets skip more work safely

## Phase 5 - Product Benchmark Suite

**Goal:** Prove MC is useful for real adoption.

**Workloads:**
- logs
- JSON/structured corpora
- CSV/tabular
- mixed project folders
- noisy/high entropy
- small files
- large corpora

**Compare:**
- TAR+ZSTD
- ZSTD-per-file
- current MC
- MC fast/warm path
- MC shaping candidate

**Exit:**
- clear report: where MC wins, loses, and should fallback

## Phase 6 - Adoption Mode

**Goal:** Make MC usable, not just impressive.

**Add:**
- fast default mode
- explainable report
- safe fallback
- predictable memory use
- clear CLI flags
- docs for best workloads

**Exit:**
- user can run MC and understand:
  - selected path
  - ratio
  - speed
  - fallback reason
  - whether MC was worth using

## Phase 7 - Boundary Redesign Research

**Goal:** Revisit runtime reuse only if needed.

**Do NOT resume chunk-local substitution.**

**Research alternatives:**
- msgpack-object-level artifact boundary
- assembly-state cache
- dependency-graph rebuild
- higher-level reusable model objects

**Exit:**
- only continue if byte-identical parity can be proven without wire-format damage
