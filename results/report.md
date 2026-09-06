# evolving-graphrag - evaluation report

- config_hash: `8b1da1efb99381b1`  seed: `1337`  version: `0.1.0`
- wall clock: 2.2s
- environment: {'python': '3.12.10', 'platform': 'Windows-11-10.0.26200-SP0', 'store_backend': 'memory', 'llm_backend': 'mock', 'llm_model': 'claude-opus-5', 'embed_backend': 'hash', 'cluster_backend': 'leiden'}
- benchmark: {'base_docs': 5, 'updated_docs': 2, 'new_docs': 2, 'deleted_docs': 2, 'base_questions': 6, 'new_questions': 5, 'deletion_questions': 6, 'surviving_questions': 3}

## 1. Scenario comparison

`stale_answer_rate` on the **deletion** row is the headline: the fraction of
questions still answered with a fact that only a deleted document supported.
`deletion-survivors` is the control - facts a surviving document still supports
must remain answerable, so a system cannot win by deleting too much.

### base-on-base

| system | n | F1 | recall | stale rate | update tokens | recomputed | entities |
|---|---|---|---|---|---|---|---|
| evolving | 6 | 0.433 | 0.833 | 0.000 | 14995 | 16 | 19 |
| full-reindex | 6 | 0.433 | 0.833 | 0.000 | 36838 | 28 | 19 |
| append-only | 6 | 0.433 | 0.833 | 0.000 | 14995 | 16 | 19 |
| flat-vector | 6 | 0.238 | 0.667 | 0.000 | 879 | 0 | 0 |

### base-on-updated

| system | n | F1 | recall | stale rate | update tokens | recomputed | entities |
|---|---|---|---|---|---|---|---|
| evolving | 6 | 0.433 | 0.833 | 0.000 | 12281 | 14 | 27 |
| full-reindex | 6 | 0.433 | 0.833 | 0.000 | 52193 | 41 | 27 |
| append-only | 6 | 0.433 | 0.833 | 0.000 | 12787 | 15 | 27 |
| flat-vector | 6 | 0.238 | 0.667 | 0.000 | 599 | 0 | 0 |

### new-on-updated

| system | n | F1 | recall | stale rate | update tokens | recomputed | entities |
|---|---|---|---|---|---|---|---|
| evolving | 5 | 0.628 | 1.000 | 0.000 | 12281 | 14 | 27 |
| full-reindex | 5 | 0.628 | 1.000 | 0.000 | 52193 | 41 | 27 |
| append-only | 5 | 0.628 | 1.000 | 0.000 | 12787 | 15 | 27 |
| flat-vector | 5 | 0.273 | 1.000 | 0.000 | 599 | 0 | 0 |

### deletion

| system | n | F1 | recall | stale rate | update tokens | recomputed | entities |
|---|---|---|---|---|---|---|---|
| evolving | 6 | 0.167 | 1.000 | 0.000 | 2705 | 5 | 18 |
| full-reindex | 6 | 0.167 | 1.000 | 0.000 | 22866 | 15 | 18 |
| append-only | 6 | 0.000 | 1.000 | 1.000 | 0 | 0 | 27 |
| flat-vector | 6 | 0.500 | 1.000 | 0.000 | 0 | 0 | 0 |

### deletion-survivors

| system | n | F1 | recall | stale rate | update tokens | recomputed | entities |
|---|---|---|---|---|---|---|---|
| evolving | 3 | 0.362 | 1.000 | 0.000 | 2705 | 5 | 18 |
| full-reindex | 3 | 0.362 | 1.000 | 0.000 | 22866 | 15 | 18 |
| append-only | 3 | 0.362 | 1.000 | 0.000 | 0 | 0 | 27 |
| flat-vector | 3 | 0.101 | 0.333 | 0.000 | 0 | 0 | 0 |

## 2. Cost vs freshness

The recompute budget is how many dirty community summaries may be regenerated
per document change. Full reindex is the reference point, not a sweep point.

| configuration | update tokens | recomputed | dirty after | stale summaries/query | stale rate | new-info recall | survivor recall |
|---|---|---|---|---|---|---|---|
| evolving/budget=0 | 5497 | 0 | 0.7143 | 2.2353 | 0.1667 | 0.8 | 1.0 |
| evolving/budget=1 | 8601 | 6 | 0.4286 | 1.4118 | 0.0 | 0.8 | 1.0 |
| evolving/budget=2 | 10773 | 12 | 0.2857 | 0.8235 | 0.0 | 0.8 | 1.0 |
| evolving/budget=4 | 14986 | 19 | 0.0 | 0.0 | 0.0 | 0.8 | 1.0 |
| evolving/budget=8 | 14986 | 19 | 0.0 | 0.0 | 0.0 | 0.8 | 1.0 |
| evolving/drain | 14986 | 19 | 0.0 | 0.0 | 0.0 | 0.8 | 1.0 |
| full-reindex | 75059 | 56 | 0.0 | 0.0 | 0.0 | 0.8 | 1.0 |

Pareto frontier: evolving/budget=0, evolving/budget=1, evolving/budget=2, evolving/budget=4, evolving/budget=8, evolving/drain

## 3. Compaction ablation

Does incremental placement drift far enough to matter, and does periodic
re-clustering pay for itself?

| interval | compactions | tokens | communities | mean size | max size | base recall | stale rate |
|---|---|---|---|---|---|---|---|
| off | 0 | 14986 | 7 | 2.57 | 8 | 0.8333 | 0.0 |
| 1 | 6 | 23419 | 3 | 6.0 | 7 | 0.8333 | 0.0 |
| 2 | 3 | 18914 | 3 | 6.0 | 7 | 0.8333 | 0.0 |
| 4 | 1 | 16630 | 5 | 3.6 | 7 | 0.8333 | 0.0 |
| 8 | 0 | 14986 | 7 | 2.57 | 8 | 0.8333 | 0.0 |

## 4. Entity-resolution threshold sweep

| tau_sim | entities | relations | merge rate | base recall | base F1 |
|---|---|---|---|---|---|
| 0.6 | 19 | 37 | 0.6275 | 0.8333 | 0.4334 |
| 0.7 | 19 | 37 | 0.6275 | 0.8333 | 0.4334 |
| 0.8 | 19 | 37 | 0.6275 | 0.8333 | 0.4334 |
| 0.86 | 19 | 37 | 0.6275 | 0.8333 | 0.4334 |
| 0.92 | 19 | 37 | 0.6275 | 0.8333 | 0.4334 |
| 0.98 | 19 | 37 | 0.6275 | 0.8333 | 0.4334 |

## 5. Scaling: update cost vs corpus size

`one_update_tokens` should stay roughly flat while `build_tokens` grows with
the corpus - that ratio is the asymptotic form of the whole argument.

| copies | documents | entities | relations | build tokens | one update tokens | reindex/update ratio |
|---|---|---|---|---|---|---|
| 1 | 5 | 20 | 38 | 14995 | 2491 | 6.02 |
| 2 | 10 | 37 | 75 | 33209 | 2495 | 13.31 |
| 4 | 20 | 71 | 149 | 70005 | 2499 | 28.01 |
| 8 | 40 | 137 | 295 | 148122 | 2503 | 59.18 |

