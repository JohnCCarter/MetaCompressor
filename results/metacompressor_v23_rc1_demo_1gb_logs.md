# MetaCompressor v2.3-rc1 Demo (1GB logs)

## Scenario

- Input: `1.0 GB` log corpus
- Baseline: `TAR+ZSTD = 300 MB`
- MetaCompressor: `MC = 210 MB`

## Visual Result

```
Size (lower is better)

TAR+ZSTD  | ############################## | 300 MB
MC v2.3   | #####################          | 210 MB

Savings: 90 MB (30.0%)
```

## CLI Flow (minimal product)

```bash
mc compress data/ --profile logs
mc decompress archive.mck
```

## Message

`v2.3-rc1` demonstrates a clear size win on large log corpora while preserving deterministic, lossless round-trip behavior.
