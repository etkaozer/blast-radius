# Known limitations of the severity engine

This document covers defects in `sev-v1` that are **understood and not fixed**.
It is separate from the "Limitations" section of the README, which describes what
blast-radius does not attempt. These are different: each one below is a way the
tool can be wrong, or can be made to be wrong, inside the thing it does attempt.

Everything here was found by reviewing `core/severity/` and
`core/impact/rules.py` rather than by an incident. None of it is hypothetical —
each entry carries the arithmetic that demonstrates it, reproducible with
`core.severity.scoring.compute`.

Three related findings **were** fixed and are not listed here: contract coverage
and assertion coverage were dataset-level rather than column-level (both now
require the changed column), and an unmeasured `query_usage` produced a score
indistinguishable from a measured zero (the report now marks such a score as a
lower bound). See `core/tests/test_impact_rules.py` and
`core/tests/test_severity_lower_bound.py`.

A note on what none of these break: the architectural guarantee that free text
cannot influence a severity score is unaffected by every item below. These are
failures of *the formula's judgement*, not of the boundary between the
deterministic core and the model. Fixture 03 still scores 77.0 whether or not
its description argues otherwise.

---

## 1. Threshold cliffs

**What it is.** Every normalisation in `sev-v1` is a step function, and the
levels are hard cut-offs at 70 / 45 / 20. So the score is discontinuous in its
inputs, and two adjacent realities can land on opposite sides of a line that
determines whether a reviewer treats the PR as urgent.

The bucket edges bite hardest. `normalize_downstream_reach` jumps 0.6 → 0.8
between three and four consumers, which is four points of score:

```
removed, 1 hop, 50 queries, 3 downstream  ->  65.25 (high)
removed, 1 hop, 50 queries, 4 downstream  ->  69.25 (high)
```

and a score of 69.9 is `high` while 70.0 is `critical`, for a difference no
reviewer would consider meaningful. The same applies at the usage boundaries
(9 vs 10 queries is 4.5 points; 99 vs 100 is 3.75).

**Why it is not fixed in this version.** The step functions are load-bearing for
a property we are not willing to trade away: a reviewer must be able to re-derive
the score by hand from the PR comment. `rules.py` says this explicitly — "4
downstream entities scored 0.8" is checkable in a way that
`log1p(4)/log1p(20) = 0.537` is not. A smooth curve removes the cliffs and
removes the auditability at the same time. Replacing one defect with a worse one
is not an improvement, and choosing between them properly needs calibration data
we do not have.

**What would fix it.** Two options, neither free:

- Keep the steps, add a **proximity-to-boundary flag**. When a score is within
  ~3 points of a level threshold, or an input is within one of a bucket edge, say
  so in the report: "69.25, just below critical; one more consumer would cross
  it." This is cheap, preserves auditability, and converts a cliff into
  information. It is the option we would take first.
- Move to a monotone continuous curve per factor and publish a lookup table
  alongside it, so hand-derivation becomes table lookup rather than arithmetic.
  More faithful, and a bigger change to how the comment is read.

Neither is possible without also deciding whether the thresholds themselves are
right, and they are currently **arbitrary** — see §2 of the reviewer briefing.
Nothing derives 70, 45 and 20.

---

## 2. `overall()` takes the maximum, so breadth is invisible

**What it is.** A pull request's severity is `max()` over its column severities.
A PR that removes one load-bearing column scores exactly the same as a PR that
removes that column *and nineteen others*. Breadth of change contributes nothing.

The failure runs both ways, and the second direction is the one that gets the
tool switched off:

- **Under-reporting.** Twenty columns each scoring 55 is a migration, not a
  patch, and it reports as a single `high` — the same label as one column at 55.
- **Over-reporting.** One false positive among fifty columns drives the whole PR
  to `critical`. Since the level is what a reviewer reads first, a single bad
  lineage edge can make a routine PR look like an emergency, repeatedly.

**Why it is not fixed in this version.** The reasoning behind `max()` is sound
and we still agree with it: one critical break *is* a critical pull request, and
averaging would let a PR touching nine harmless columns and one load-bearing one
report as moderate. The problem is not that `max()` is the wrong aggregate; it is
that a single scalar cannot carry both "how bad is the worst thing" and "how much
is going on here". Fixing it means adding a second number to the report, and
`impact_report.schema.json` is frozen — `overall_severity` is a single
`severity` object with no field for breadth. That is a contract change requiring
both owners, and it was not worth spending the freeze on before the numbers
themselves are calibrated.

**What would fix it.** Add a PR-level breadth measure alongside the maximum
rather than folding it into the score: the count of columns at each level, e.g.
`critical: 1, high: 3, medium: 12`. Renderers already receive every
`column_impact`, so **OWNER B can compute and display this today without any
schema change** — the data is in the report. The schema change is only needed to
make it authoritative rather than derived. Explicitly do *not* sum severities:
that makes a PR of twenty trivial changes outrank one genuine break, which is the
failure `max()` was chosen to avoid.

---

## 3. Staged removal walks under the thresholds

**What it is.** `sev-v1` scores one pull request in isolation. It has no memory
of previous pull requests, so a change that would be `critical` in one step can
be split into steps that are each below the line, with the same end state.

The arithmetic, for a column with four consumers and ~500 queries a month:

```
remove outright                        ->  73.0  (critical)
step 1: rename to a deprecated name    ->  68.5  (high)
step 2: remove, after consumers moved  ->  30.0  (medium)
```

Nothing was ever reported as critical, and the column is gone. Note that step 1
crosses the threshold purely on the `change_kind` weight — the graph facts are
identical.

**Why it is not fixed in this version — and the reason is not effort.** The
sequence above is *also exactly what a careful staged migration looks like*.
Deprecate, migrate the consumers, then remove once nothing reads it. That is the
behaviour the tool should encourage, and it should score low, because by step 2
the change genuinely is low risk. A rule that penalised it would punish the
correct workflow to catch the evasive one.

The two are distinguishable only by intent and by history, and `sev-v1` can see
neither. We are not willing to guess at intent, and the history does not exist
yet. Any heuristic we invented now — "penalise removals of recently renamed
columns" — would produce false positives on good behaviour, which is worse than
the gap it closes.

**What would fix it.** History, and blast-radius is already building the
mechanism. The `io.blastradius.impactRecord` structured property written back to
DataHub (`core/writeback/`) is per-column, carries `detected_at`, the severity,
and a `supersedes` pointer. Once write-back runs in production, a later analysis
can read the prior records for the same column and see the chain. Then the honest
rule is not a penalty but a **disclosure**: "this column was renamed 11 days ago
in PR #128, which scored 68.5; this is step 2 of a staged change." Show the
reviewer the sequence and let them judge — the same principle as the untrusted
findings, where the tool reports and does not decide.

That needs the write path working end to end, which needs a live DataHub. Until
then this is open, and someone who wants to evade the tool can.

---

## 4. `change_kind` is an inference from the diff, not a graph fact

**What it is.** `change_kind_risk` is the heaviest factor at 30 points — more
than a fifth of the total, and the largest single term. It is also the **only
factor not derived from the metadata graph**. It comes from `ci/diff`'s
classification of the textual diff, which the README correctly describes as
inference: a same-position, same-expression column with a new name is *probably*
a rename, and when the evidence is weak the extractor reports add + remove
instead.

That makes the biggest weight in the formula rest on the least certain input.
Holding every graph fact constant and varying only the label:

```
removed       ->  73.0  (critical)
renamed       ->  68.5  (high)
type_changed  ->  61.0  (high)
added         ->  44.5  (medium)
```

A 28.5-point spread, and the top two straddle the critical threshold. An author
who wants a lower number does not need to touch the lineage graph; they need the
extractor to read the diff differently. Restructuring a removal so it parses as a
type change is worth 12 points. The README also notes that `SELECT *` defeats the
extractor entirely, which means a model projecting a star yields no column
changes and therefore no findings at all.

**Why it is not fixed in this version.** There is no better source available.
DataHub's schema history could confirm a column disappeared, but only *after* the
change is merged and re-ingested — which is too late for a pull-request check,
and the whole point is to catch this at review time. The dbt manifest is a
stronger signal than raw SQL and the extractor already supports a `dbt_manifest`
method, but the manifest is generated from the same PR branch and is equally
author-controlled. Fundamentally, at review time the diff is the only evidence
that the change is happening at all.

Note also the boundary: `ci/diff` is OWNER B's. This is not something OWNER A can
fix unilaterally, and the interface between them (`change_kind`) is frozen.

**What would fix it.** Do not remove the inference — reduce what rides on it:

- **Carry the extractor's confidence into the contract.** `ColumnChange` has no
  confidence field. If it did, a low-confidence `removed` could be scored more
  conservatively and, more importantly, *shown* as uncertain: "classified as a
  removal, low confidence; if this is a rename the score is 68.5." Schema change,
  both owners.
- **Cross-check against DataHub's current schema.** The pre-change schema *is*
  in the graph and is not author-controlled. If the diff says a column was
  removed and `schemaMetadata` confirms it existed, the classification is
  corroborated by a source the PR author does not control. This is implementable
  today with `list_schema_fields`, already built in `core/datahub/`. It is the
  cheapest real improvement on this list.
- **Report `SELECT *` models as unanalysable** rather than as having no changes,
  so a star projection produces a visible degradation instead of silence.

---

## 5. Table-level lineage inflates `downstream_reach`

**What it is.** `core/impact/analyzer.py` counts an entity as downstream even
when it was reached without a column-level edge — when `via_column` is `None`.
It emits a `column_level_lineage` degradation saying so, but the entity still
counts toward `downstream_reach` (up to 20 points) and toward `hop_proximity`
(up to 15) and can set `critical_consumer` (4).

On a catalog with only table-level lineage, every consumer of the *table* is
counted as a consumer of the *column*:

```
1 genuine column-level consumer     ->  60.25 (high)
widened to 6 table-level consumers  ->  69.25 (high)
```

Nine points, from consumers that may not read the changed column at all. This
directly contradicts the README, which states that blast-radius "reports a
`column_level_lineage` degradation rather than widening to table-level
reachability, because widening would inflate severity". The degradation is
emitted; the widening happens anyway.

**Why it is not fixed in this version.** Because the alternative is worse in the
common case, and choosing between them is a judgement we did not want to make
silently. Dropping table-level consumers entirely means that on a catalog without
fine-grained lineage — which is most catalogs — every change scores near zero and
the tool reports "nothing downstream" for a column with real consumers. That is
a false negative, in the direction that gets someone paged. Counting them is a
false positive, in the direction that wastes a reviewer's time. Given the
project's stated preference for failing loudly, over-counting is the less bad
default, but it is still wrong and it is still undisclosed in the score itself.

**What would fix it.** Separate the two populations rather than choosing between
them. Concretely: score `downstream_reach` from the column-level consumers only,
and report the table-level ones as a distinct, clearly-labelled set — "3 entities
consume this column; a further 5 consume the table and may or may not read it."
That is honest in both directions, and it is what the README already claims
happens. It needs either a `severity` input change (which changes
`inputs_digest`, and therefore the frozen fixtures) or a second reach factor,
so it is a `contracts/` conversation and a `sev-v2` rule version. It is the
highest-priority item on this list, because it is the only one where the
documentation and the behaviour disagree.

---

## Status

| # | Limitation | Fixable in `core/` alone? | Needs `contracts/` change? |
| --- | --- | --- | --- |
| 1 | Threshold cliffs | Yes (boundary flag) | No |
| 2 | `max()` ignores breadth | Renderer-side today | Yes, to be authoritative |
| 3 | Staged removal | No — needs write-back history | No |
| 4 | `change_kind` is inferred | Partly (schema cross-check) | Yes, for confidence |
| 5 | Table-level inflation | No — changes the score | Yes, and `sev-v2` |

None of these is scheduled. They are written down so that a reviewer who finds
one has confirmation that it is known rather than missed, and so that the next
person to touch `sev-v1` knows what they are inheriting.

---

*Ownership note: `docs/` requires both owners' approval per the table in
[CLAUDE.md](../CLAUDE.md). This file was drafted by OWNER A and needs OWNER B's
review before it counts as agreed.*
