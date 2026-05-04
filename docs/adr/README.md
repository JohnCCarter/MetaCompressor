# Architecture Decision Records (ADRs)

Short-lived decisions for MetaCompressor: **one markdown file per decision**, numbered for order.

For **where files belong** (zones, naming, splits), see the living **[repository layout policy](../repository-layout-policy.md)**—ADRs record *decisions*; that document records *placement conventions*. For **agent mandates and freeze zones**, see **[`AGENTS.md`](../AGENTS.md)**. For **working contract / stay current**, see **[`METACOMPRESSOR_WORKING_CONTRACT.md`](../METACOMPRESSOR_WORKING_CONTRACT.md)**. For **Cursor skill charter** (A′ additive-only, precedence), see **[`.cursor/skills/README.md`](../../.cursor/skills/README.md)**.

## Index

| ADR | Title |
|-----|--------|
| [0001](0001-layout-policy-and-ci-guardrails.md) | Layout policy and CI guardrails |

## When to add an ADR

- A choice affects **multiple modules**, **tooling**, or **contributor workflow** and is hard to reverse.
- You need a durable pointer (“why is it this way?”) beyond a commit message.

## Naming

- `NNNN-short-slug.md` (four digits, incrementing).
- Prefer **status** line at top: `Accepted`, `Proposed`, `Superseded by 0007`.

## Template

Copy from [0001](0001-layout-policy-and-ci-guardrails.md): Context / Decision / Consequences.
