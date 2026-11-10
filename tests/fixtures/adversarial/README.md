# Adversarial fixtures (hand-written, not model output)

Each app exposes the right `data-testid` elements but does NOT implement the behaviour:

| fixture | what it does | expected |
|---|---|---|
| `tip_hardcoded` | always prints the numbers of the first listed tip example | passes ONE example per behaviour, fails the varied criteria |
| `expense_static` | dashboard prints constants (`65.00`, `35.00`) | same |
| `habit_static` | stats page prints constants (`1`, `0`) | same |
| `tip_lookup_listed` | a lookup table of exactly the listed examples | PASSES the listed criteria. This is the residual gap: varied examples reduce, they do not eliminate, special-casing of what the criteria show. Unseen inputs expose it. |
