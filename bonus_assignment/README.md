# Bonus: agentic voice assistant for a bank

**Design only. Not built.**

The main assignment ships a working weather assistant with a semantic cache and a
model router, measured end to end. This directory takes the same two mechanisms
and asks what they become in the environment Sarj actually sells into: a bank,
with twenty backend systems, real entitlements, regulators, and a false cache hit
that is a reportable incident rather than a wrong temperature.

It exists to show where the architecture goes, not to pad the build. Nothing here
was implemented, and the documents say so throughout.

- [PRD](docs/PRD.md) — product shape, constraints, economics
- [TDD](docs/TDD.md) — architecture, agent loop, caching under entitlements

## What changes from weather to banking

| | Weather (built) | Banking (designed) |
|---|---|---|
| Tools | 1 HTTP call | ~20 services, different owners, legacy middleware |
| Orchestration | Fixed pipeline | Agent loop with planning over tools |
| Cache scope | Global by city | Per-customer, entitlement-bound |
| Personalisation | Resolves to a canonical city | Irreducibly per-customer — cannot be shared |
| Freshness | 10-minute TTL | Event-driven invalidation off core banking |
| Wrong answer | Wrong temperature | Reportable data incident |
| Actions | None | Money movement — must never be model-decided |
