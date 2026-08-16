# FlowDesk — Parked Ideas

The architecture is frozen (v4.9.x). Ideas land here, not in the spec. Each entry states
what it is, why it's parked, and **the specific evidence that would justify unfreezing for
it**. Nothing is lost; nothing jumps the queue.

---

## IDEA-001: Automated Research Loop ("self-improving FlowDesk")

**Status:** Parked — Phase 3+, gated
**Raised:** 2026-08-01

### The proposal
An agent that continuously proposes, tests, and refines strategies, deciding what to try next
based on what it just learned — becoming more accurate and profitable over time.

### Why it is genuinely appealing
FlowDesk already has almost every component such a loop needs, which is unusual: a hypothesis
registry with economic-rationale requirements, honest trial counting, a simulator with factor
attribution and capacity modelling, outcome scorecards, a graveyard with failure
classification, and full lineage. Most people proposing this have none of that. The loop is
the natural top of the stack.

### Why it is parked (four reasons, in order of severity)

**1. The Kaggle disanalogy.** Self-improving research agents work well on data-science
competitions because the data is stationary, the ground truth is fixed, and the metric is
honest — a better model genuinely scores better. Markets are non-stationary and adversarial:
the "ground truth" changes regime, and any edge that becomes widely traded stops existing. An
agent that improves against a fixed historical dataset is improving at *fitting that dataset*,
which is precisely what must not be rewarded.

**2. Automated search multiplies the multiple-testing problem catastrophically.** A human runs
50 trials; an agent runs 50,000. At a 5% bar, 50,000 trials on pure noise yield ~2,500
"significant" strategies. The deflated-Sharpe haircut scales with N, so the bar the agent must
clear rises with its own productivity — and the best-looking survivor of a 50,000-way search is
almost certainly the luckiest, not the best. Automation without a trial budget converts a
research platform into a false-discovery generator.

**3. The held-out set is finite and non-renewable.** There are only ~20 years of usable daily
history. Every touch of the final held-out period spends a resource that cannot be replenished,
and an agent optimising in a loop will burn it in an afternoon. Any automated loop needs a hard
held-out budget (e.g. N looks per quarter, human-approved), enforced in code.

**4. "More profitable after every test" misstates what testing does.** Testing does not create
edge; it discovers or fails to discover it. What can genuinely improve over time is the
*search process* — which hypothesis classes are worth generating, which fail fast, which data
is worth the effort. That is meta-learning about research, not compounding profitability, and
claiming the latter is how these systems get oversold.

### The version that would be legitimate
Not a self-improving trader. A **search executor operating strictly under §7.17's protocol**:
- Generates hypotheses from a fixed grammar (context filter × trigger × confirmation × exit)
  seeded by human-supplied economic rationales — an agent may not invent the rationale, because
  "why should this edge exist" is the filter that kills most bad ideas.
- Pre-registers every trial, counts every parameter combination, respects the trial budget.
- Runs the existing pipeline; classifies failures; writes to the graveyard and knowledge graph.
- **Cannot touch the held-out set.** Ever. Human-gated, budgeted, logged.
- Reports 1-of-N with deflated metrics, exactly as a human trial would.
- Its "improvement" is measured as: does the hit rate of *promoted* candidates rise over time,
  and does time-to-rejection of bad ideas fall? Not "did returns go up."

In that shape it is automation of the tedious parts — which is valuable — rather than an
optimiser pointed at a finite dataset.

### Unfreeze triggers (all required)
1. Milestones M0–M8 complete; v0.1 Core MVP soaked for 14 days.
2. The **manual** research protocol has been used for ≥25 pre-registered trials, with a
   populated graveyard and at least one honest negative result published.
3. ≥6 months of live outcome scorecard data exists, so "improvement" has a real-world
   reference rather than a backtest one.
4. A written held-out budget policy exists and is enforced in code.

### Related existing design
Phase 3 Research Knowledge Graph and meta-learning notes (§7.18) are the honest 80% of this
idea and are already specified. Building those first is the prerequisite, not a substitute.

---

## IDEA-002: Zoning-change / land-value real estate module

**Status:** REJECTED for the Intelligence Plane — recorded so it does not return
**Raised:** 2026-08-01

### The proposal
Track parcels in areas undergoing rezoning (low-density → high-density), on the theory that
land value jumps when zoning flips, and connect this to REIT exposure.

### Why it fails the §1 admission test
The test is *"does this help explain where capital is flowing or how the economy is changing?"*
Buying individual parcels before a zoning hearing is a **private-market business operation**,
not a capital-flow signal — the same category as the real-estate wholesaling idea rejected
earlier, and the same category as budgeting and loan calculators excluded on day one. There is
no data feed for "which homeowner will sell below post-rezoning value"; the transaction is
negotiated, parcel-specific, and non-public by nature. Nothing to measure means nothing to
detect.

### Three specific problems with the pitch itself
1. **"The owner doesn't know it's happening yet" is the weakest claim in it.** Rezoning runs
   through published comprehensive-plan amendments, planning-commission agendas, and public
   hearing notices. Local developers, land brokers, and listing agents track these
   professionally. The asymmetry is against the *uninformed individual seller*, not against the
   market — which is a different, and ethically loaded, proposition than "spotting a
   mispricing."
2. **It is a local operator's business, not a fund strategy at scale.** Institutional real
   estate arms mostly do CRE debt, single-family rental aggregation, and large development —
   not parcel-by-parcel zoning arbitrage, which does not scale past a metro and requires
   entitlement expertise, local relationships, and multi-year timelines.
3. **No unified data source exists.** Zoning is administered by thousands of independent
   municipalities with incompatible formats and no national feed. Even a well-funded attempt
   would be a per-county scraping project, permanently.

### What already covers the legitimate part
FlowDesk's real-estate exposure is already built, in market-intelligence form:
- **§7.4 Economy & Credit:** 30-year mortgage rate, housing starts, Case-Shiller home price
  index, credit-card delinquency — the macro housing cycle.
- **§7.6 Housing theme:** XHB, ITB, LEN, DHI, with mortgage-rate and housing-start macro links,
  so homebuilder/REIT rotation is picked up by the same engine as every other theme.
That is the version that measures where capital is moving in real estate. It requires no new
module.

### Unfreeze trigger
None. If a genuinely public, national, machine-readable zoning-change dataset ever appears,
revisit as an annotation layer only (like the geopolitical context feed, §7.18) — never as a
parcel-selection tool.


---

## IDEA-003: Options-flow detail — volume/OI ratio, peak-OI levels, gamma flip

**Status:** Parked — belongs to the existing Phase 3 options module (§7.18), not a new module
**Raised:** 2026-08-01

### The proposal
Three specific options-flow measures: (a) call volume exceeding open interest, (b) "apex"
levels where put and call open interest concentrate, (c) the gamma flip level where aggregate
dealer positioning turns from negative to positive.

### Assessment — these are real, with different evidence quality

**(a) Volume > open interest — the strongest of the three.** Open interest is the count of
contracts outstanding at yesterday's close; volume is today's trading. Volume exceeding OI on a
strike means more contracts traded today than exist in total, which cannot be closing activity
alone — it is new positioning. This is the cleanest, most computable, least assumption-laden of
the three, and it is the standard basis of unusual-options-activity screens. **Caveat:** it
identifies *new* positioning, not *bullish* positioning — a large call buy and a large call
sale produce identical volume, and the aggressor side is not visible in free chain data.

**(b) Peak open-interest strikes ("apex") — real as levels, weak as mechanism.** Strikes with
the largest OI concentration are genuine reference points and do show clustering of price
around them near expiry, partly through hedging mechanics. But the framing that they *are*
support and resistance overstates it: max-pain-style theories have thin and contested empirical
support, and OI concentration reflects where positions were opened historically, not where
demand exists now. **Adopt as:** a labelled OI-by-strike distribution with the largest
concentrations marked — descriptive, never "support."

**(c) Gamma flip — real concept, model output not observation.** The level where aggregate
dealer gamma crosses zero, above which hedging dampens moves and below which it amplifies them.
This is already half-specified in §7.18's Dealer Positioning panel. **The honest caveat, which
must be displayed:** computing it requires *assuming* which side of each contract dealers hold
(the standard convention: dealers long calls / short puts relative to customer flow). That
assumption is unverifiable from public data, so a gamma flip level is an estimate resting on a
positioning model, computed from delayed open-interest data — a regime descriptor, not a
tradeable level.

### The version that would be legitimate
Extend §7.18's options module with an **OI & positioning panel** per underlying:
- Volume/OI ratio by strike and expiry, flagging strikes where volume > OI (new positioning),
  with the aggressor-side caveat displayed.
- OI-by-strike distribution with the largest call and put concentrations marked, labelled as
  concentration levels — never as support or resistance.
- Estimated gamma flip level from the existing GEX computation, with its positioning-assumption
  caveat, presented alongside the positive/negative-gamma regime tag.
All three are computable from delayed chains under `ENABLE_OPTIONS_EXPERIMENTAL`, so they cost
nothing in Zero-Cost Mode beyond ingestion work. Forbidden framings (grep-enforced, per §7.5's
rule): "support," "resistance," "magnet," "price target," and any claim that a level will be
reached or defended.

### Unfreeze triggers
1. Milestones M0–M8 complete (v0.1 Core MVP soaked).
2. The options module itself has been built at least to the vol-gap/earnings-implied-move stage,
   since these measures are refinements of it rather than standalone features.
3. Options-chain ingestion has proven stable enough that OI and volume fields are trustworthy
   (delayed chains are the weakest data source in the stack — confidence tier: low, per ADR-0007).


---

## IDEA-004: Professional metrics audit — what's already in, what's missing, what's worth adding

**Status:** Mostly ALREADY SPECIFIED. Four genuine gaps parked; one recommended for early adoption.
**Raised:** 2026-08-14

### Already in the spec (no change needed)
| Metric | Where |
|---|---|
| VIX level, 1-year percentile | §7.1 tiles; FRED `VIXCLS`; sentiment composite; Regime Badge |
| Put/call ratio (CBOE) | §6.3; sentiment composite |
| Float, short interest % float, days-to-cover | §7.11 scanner universe + scoring |
| IV vs realized vol, IV rank, earnings implied move | §7.18 options module (vol-gap detection) |
| Dealer gamma / GEX regime | §7.18 Dealer Positioning panel |
| Volume vs open interest, OI concentration, gamma flip | IDEA-003 (parked, belongs to §7.18) |
| Credit spreads (HY/IG OAS), yield curve, mortgage rates | §7.4 Economy & Credit |
| Breadth, % above 50/200DMA, new highs/lows, equal-weight ratio | §7.1 |
| Sharpe, beta, max drawdown, factor attribution, deflated Sharpe | §7.17 simulator |
| Capacity ceiling, market-impact (square-root law), TCA | §7.17 |
| Correlation clustering, exposure decomposition, scenario replay | §7.15 Risk Lens |

The honest summary: FlowDesk already carries most of the institutional metric set that is
obtainable for free. The gaps below are the real ones.

### GAP 1 — CFTC Commitments of Traders (COT). **Recommended: adopt early.**
Weekly, free, official positioning data from the CFTC: net futures positioning split by
commercial hedgers, large speculators (non-commercial), and small traders, across gold, oil,
copper, currencies, and index futures. Published Friday afternoon reflecting Tuesday positions —
a clean, known publication lag that fits the §6.4 `available_at` model exactly.

Why this ranks above everything else on the list: FlowDesk's entire thesis is *where is capital
positioned and how is it moving*, and COT is the only free, official, institutional-grade
dataset that answers that question directly rather than by proxy. Everything else in the
platform infers positioning from price and volume; COT reports it. It is also the dataset most
directly relevant to the owner's own gold futures trading.

Honest caveats to encode: positions are as of Tuesday and published Friday (never present as
current); "commercial" includes swap dealers hedging index exposure, so the hedger/speculator
framing is looser than it sounds; and COT extremes have a long history of being cited as
reversal signals with weak out-of-sample support — it enters as a *positioning-context* signal
with a scorecard, like everything else.

### GAP 2 — Volatility structure beyond spot VIX
- **VIX term structure** (`VIX9D` / `VIX` / `VIX3M` / `VIX6M` ratios): backwardation — front
  above back — is a genuine near-term stress marker that a spot VIX level cannot express.
- **CBOE SKEW index**: the price of crash protection; rising SKEW with a calm VIX is a specific
  and useful divergence.
- **Implied correlation** (`COR1M`/`COR3M`): directly relevant to FlowDesk's core thesis — high
  implied correlation means a macro-driven tape where sector rotation signals should work
  *worse*, low correlation means dispersion where they should work better. This is a natural
  conditioning variable for the §7.16 scorecards (report rotation hit rate by correlation
  regime).
All CBOE-published and free at daily frequency.

### GAP 3 — Funding and real-rate plumbing (FRED, free, config rows only)
- `DFII10` — 10-year real yield. Drives gold, long-duration equity valuation.
- `T10YIE` — 10-year breakeven inflation. Real yield vs breakeven decomposes any nominal move.
- `SOFR`, `IORB` — the SOFR-minus-IORB spread is a live funding-stress indicator that moves
  before credit spreads in a plumbing squeeze.
These are **rows in the existing §6.4 config table**, which the spec explicitly states requires
no code changes — the cheapest genuine upgrade available.

### GAP 4 — Liquidity and float refinements (scanner)
- **Float rotation** (daily volume ÷ free float): the small-cap metric that distinguishes a
  genuine repricing from a large print in a thin name.
- **Amihud illiquidity** (|return| ÷ dollar volume): an academic-standard illiquidity measure
  that also feeds the §7.17 capacity ceiling directly.

### Deliberately NOT added
Forward P/E, EV/EBITDA, FCF yield, DCF inputs, analyst revisions, fund-flow data (EPFR-class),
13F aggregation. These are the equity-research metric set rather than the capital-flow set, they
require paid fundamentals feeds (§18 Tier 1–3), and adding them would drift FlowDesk toward the
"everything terminal" failure mode rejected in §19.1. Valuation is not where this product
competes.

### Unfreeze triggers
- **GAP 3** (FRED config rows): may be populated during M6 (Economy) as configuration, not
  architecture — the spec already states adding a macro tile is a config-list row.
- **GAP 1** (COT): after M5 outcome tracking exists, so it enters with a scorecard from day one.
  Requires its own ingest client and a `positioning` panel; treat as a Phase 2b module.
- **GAP 2** (vol structure): with the §7.18 options module, or earlier as sentiment-composite
  inputs if CBOE index history proves easy to ingest.
- **GAP 4**: with M5.5 Swing Desk, as scanner scoring components.


---

## IDEA-005: Short-squeeze conditions and "liquidation levels" for equities

**Status:** Split verdict — squeeze composite parked as a small §7.11 refinement; equity
liquidation heatmap REJECTED (no data exists); liquidity-sweep belief redirected to the
hypothesis registry.
**Raised:** 2026-08-14

### Part A — Short interest / squeeze conditions

**Already in the spec:** `scanner_universe` carries `float_shares` and `short_interest_pct`;
§7.11 scores short interest % float and mentions days-to-cover from a biweekly reference feed;
v4.9.7 added float rotation. So the inputs exist; what does not exist is a squeeze-condition
composite or the staleness handling the data demands.

**Two corrections the squeeze narrative needs, both of which must appear on any panel built:**

1. **The data is structurally stale.** US short interest is reported to FINRA/exchanges twice
   monthly (mid-month and month-end settlement) and published roughly eight business days after
   each settlement date. So a "high short interest" reading can describe positioning up to three
   weeks old — in a name that has already moved. Any squeeze panel must display the settlement
   date and the age of the reading, exactly as §7.3 does for disclosure lag. The genuinely
   real-time input is **borrow cost and availability**, which is paid (§18 Tier 2, Ortex-class).

2. **High short interest is, on average, a NEGATIVE return predictor.** This is the short-interest
   anomaly: heavily shorted stocks underperform on average, because short sellers are in
   aggregate informed. The squeeze is the visible, memorable tail; the quiet grind lower is the
   base case. "High SI → upward spike" is survivorship reasoning over the winners. The honest
   statement is that **high short interest raises variance in both directions without raising
   expected return** — which makes it a volatility-condition input, not a bullish signal, and
   makes an expectancy-graded scorecard mandatory before it is acted on.

**The permitted refinement (small, §7.11 scoring — not a new module):** a `squeeze_condition`
composite from data already ingested — days-to-cover (short interest ÷ ADV), short % float,
float rotation, and the age of the short-interest reading — presented as a *volatility-regime
flag on a candidate*, never as a directional score component, with the settlement date and both
corrections displayed. Enters with a §7.16 scorecard so its actual expectancy in this universe
is measured rather than assumed.

### Part B — "Liquidation levels" for stocks: REJECTED (the data does not exist)

Crypto liquidation heatmaps are real because **exchanges publish the inputs**: perpetual-futures
open interest, leverage tiers, and liquidation events are on-venue and reported. A leveraged
position has a mechanically determined liquidation price, and the venue knows it.

Equities have no equivalent, and not for want of a vendor:
- Retail margin calls are not published, individually or in aggregate by price.
- Stop-loss orders rest at brokers, are not centralised, and are not disclosed.
- There is no per-symbol registry of leverage or forced-exit levels anywhere.
- FINRA publishes aggregate margin debt **monthly and market-wide** — useful macro context,
  useless for a price level in one name.

Building an equity "liquidation heatmap" would therefore mean **inventing levels and rendering
them as data**, which is the precise failure mode §7.5's forbidden-vocabulary rule and decision 7
exist to prevent. Rejected permanently in that form.

**The two honest analogues, both already accounted for:**
- **Options open interest by strike** (IDEA-003): genuinely published, and unlike stop-loss
  guesswork it creates *mechanically real* hedging flows through dealer delta rebalancing. This
  is the closest true equivalent to a crypto liquidation map and it is already scoped.
- **Volume profile (bar-approximated)** (§7.18 note): high- and low-volume nodes as accepted-value
  and rejection zones, permanently labelled approximate.

### Part C — The interesting redirect: make the liquidity-sweep belief testable

"Price gravitates to prior swing highs/lows because stops cluster there" is a belief, not an
observation — nobody can see the stops. But it is **falsifiable**, and turning it into a
pre-registered hypothesis is more valuable than any panel:

> H: On a symbol universe over N years, price approaching within X% of a prior swing high/low
> exceeds it and reverses within K sessions more often than a matched random-level control.

Register it per §7.17 with an economic rationale (why would stops cluster there, who is on the
other side), count every parameter set as a trial, and let the result stand. A confirmed result
would justify a levels panel with an actual measured hit rate attached; a negative result is
equally valuable and retires a belief that currently costs money silently. This is the correct
home for the whole "liquidity sweep" family, including the owner's ICT-based gold work.

### Unfreeze triggers
- **Part A composite:** with M5.5 Swing Desk, only after outcome tracking exists (M5), so it
  ships with a scorecard.
- **Part B:** none. Revisit only if a genuinely published per-symbol forced-exit dataset appears,
  which is not foreseeable under current US market structure.
- **Part C:** available immediately as a manual registry entry — needs no code, only the
  spreadsheet discipline and daily bars.


---

## IDEA-006: Entry-timing edge-decay curve (simulator metric)

**Status:** Parked — new §7.17 simulator metric; §18.0 Gate R stays deliberately unsatisfiable until it exists
**Raised:** n/a — named directly in docs/architecture.md's own changelog (§16 v4.9.9, refined
v4.9.10(d)), not raised as a separate proposal the way IDEA-001–005 were.

### The proposal
Replace a boolean pass/fail entry-timing test with an edge-decay curve measured at signal-time /
+1 / +5 / +15 / +30 sessions / next-open (§16 changelog v4.9.10(d)) — "a half-life is
decision-relevant where a boolean is not." It belongs in §7.17 (Backtest & Strategy Simulator)
alongside the entry-timing conventions already specified there (v4.8).

### Why it is appealing
Not stated in the spec beyond the rationale already quoted above. No separate appeal case was
written for this entry the way IDEA-001 or IDEA-005 got one.

### Why it is parked
"[It] is a new simulator metric, which the feature freeze forbids adding here" (§18.0 registry
note, v4.9.9). §18.0's Gate R (the real-time-data-upgrade evidence gate) depends on it and is, in
the spec's own words, "unsatisfiable by construction, which is the intended state" until it exists.

### The version that would be legitimate
This entry already describes the legitimate version — there is no separate interim form the way
IDEA-001 and IDEA-005 describe one.

### Unfreeze triggers
§7.17's simulator must exist, including its already-declared entry-timing conventions (v4.8) —
the edge-decay curve is added "alongside" them.

**Note on citation:** the registry note and the v4.9.9 changelog both say this entry "enters §7.16
when the simulator is built" — but the Backtest & Strategy Simulator, and the entry-timing
conventions this is meant to sit "alongside," are specified in §7.17, not §7.16 (§7.16 is Alert
Engine & Signal Outcome Tracking). Same class of stale cross-reference as `events.episode_id`'s
`§7.15` comment (IDEA-014) — see IDEA-015 for the pattern across all of these. Flagged, not
corrected here.

## IDEA-007: Execution order state machine with broker reconciliation-disarm

**Status:** Parked — Execution-Plane work, registered by the spec's own changelog, not proposed separately
**Raised:** n/a — named directly in docs/architecture.md's §16 v4.9.10(f) changelog entry; no
separate proposal exists.

### The proposal
Title only, verbatim from the spec: "execution order state machine with broker
reconciliation-disarm." No further description, mechanism, or scope is given anywhere in the
document.

### Why it is appealing
Not stated in the spec.

### Why it is parked
Grouped with IDEA-008 through IDEA-012 under one shared line: "Each is Execution-Plane or
post-outcome-data work; specifying them now would be design elaboration in place of execution,
which the freeze exists to prevent" (§16 v4.9.10(f)).

### The version that would be legitimate
Not specified — the changelog registers this as deferred outright, without describing an interim
or narrower legitimate version the way IDEA-001 and IDEA-005 do for their respective ideas.

### Unfreeze triggers
Not stated individually beyond the shared "Execution-Plane or post-outcome-data work" framing
above. This entry is explicitly about broker order execution and reconciliation — Execution-Plane
territory under decision 15 (§2) — see **IDEA-015** for a direct conflict between this project's
CLAUDE.md hard rules and decision 15 that bears on whether this can ever be built at all.

## IDEA-008: Expanded live risk-state table

**Status:** Parked — Execution-Plane work, registered by the spec's own changelog, not proposed separately
**Raised:** n/a — named directly in docs/architecture.md's §16 v4.9.10(f) changelog entry; no
separate proposal exists.

### The proposal
From the changelog gloss (§16 v4.9.10(f)): liquidity/ADV participation, gross/net exposure,
theme/sector/correlation concentration, and stale-data and execution-quality disarms. No further
description beyond this list.

### Why it is appealing
Not stated in the spec.

### Why it is parked
Shared line with IDEA-007/009–012: "Each is Execution-Plane or post-outcome-data work;
specifying them now would be design elaboration in place of execution, which the freeze exists to
prevent" (§16 v4.9.10(f)).

### The version that would be legitimate
Not specified in the spec.

### Unfreeze triggers
Not stated individually. This entry's "execution-quality disarms" phrasing places it on the same
Execution-Plane side of IDEA-015's open question as IDEA-007.

## IDEA-009: Real-time market-data substrate

**Status:** Parked — Execution-Plane or post-outcome-data work, registered by the spec's own changelog
**Raised:** n/a — named directly in docs/architecture.md's §16 v4.9.10(f) changelog entry; no
separate proposal exists.

### The proposal
From the changelog gloss (§16 v4.9.10(f)): dual event/receive timestamps, sequence-gap detection
with degraded states, and market-status/tradability as a first-class object. No further
description beyond this list.

### Why it is appealing
Not stated in the spec.

### Why it is parked
Shared line with IDEA-007/008/010–012, quoted under IDEA-007 (§16 v4.9.10(f)).

### The version that would be legitimate
Not specified in the spec.

### Unfreeze triggers
Not stated individually. Possibly overlaps with the note that "the Execution Plane already gets
live data free from broker paper APIs" (§16 v4.9.9(b)) — unconfirmed; the spec does not state
these are the same thing.

## IDEA-010: Regime hysteresis + minimum residence

**Status:** Parked — post-outcome-data work (title only), registered by the spec's own changelog
**Raised:** n/a — named directly in docs/architecture.md's §16 v4.9.10(f) changelog entry; no
separate proposal exists.

### The proposal
Title only, verbatim from the spec: "regime hysteresis + minimum residence." No description,
mechanism, or scope given anywhere else in the document. Plausibly related to the §7.1/§7.4
Regime Badge (risk_on/neutral/risk_off), but the spec does not state this connection explicitly.

### Why it is appealing
Not stated in the spec.

### Why it is parked
Shared line, quoted under IDEA-007 (§16 v4.9.10(f)).

### The version that would be legitimate
Not specified in the spec.

### Unfreeze triggers
Not stated individually.

## IDEA-011: Block-bootstrap/HAC inference for overlapping forward-return windows

**Status:** Parked — post-outcome-data work (title only), registered by the spec's own changelog
**Raised:** n/a — named directly in docs/architecture.md's §16 v4.9.10(f) changelog entry; no
separate proposal exists.

### The proposal
Title only, verbatim from the spec: "block-bootstrap/HAC inference for overlapping forward-return
windows." No description given elsewhere. Plausibly a refinement of the §7.16 scorecard's existing
bootstrap confidence intervals and "overlapping-horizon %" statistic — both already specified —
but the spec does not state this connection explicitly.

### Why it is appealing
Not stated in the spec.

### Why it is parked
Shared line, quoted under IDEA-007 (§16 v4.9.10(f)).

### The version that would be legitimate
Not specified in the spec.

### Unfreeze triggers
Not stated individually.

## IDEA-012: Incremental-information confluence (effective independent signal count)

**Status:** Parked — post-outcome-data work, registered by the spec's own changelog
**Raised:** n/a — named directly in docs/architecture.md's §16 v4.9.10(f) changelog entry; no
separate proposal exists.

### The proposal
"Incremental-information confluence (effective independent signal count) on the Signal
Interaction Graph" (§16 v4.9.10(f)). The Signal Interaction Graph itself is already specified
under §7.15(b2) — co-occurrence rates, conditional hit rates, mutual information, and lead-lag
between signal types, "only possible because every event carries lineage and outcomes." This
entry is the further step of collapsing correlated signals into one effective independent count;
no computation method is specified.

### Why it is appealing
Not stated beyond §7.15(b2)'s own framing of the graph it extends: "turning 'confluence' from a
feeling into a measured quantity."

### Why it is parked
Shared line, quoted under IDEA-007 (§16 v4.9.10(f)).

### The version that would be legitimate
Not specified in the spec.

### Unfreeze triggers
Not stated individually. Structurally depends on §7.15(b2) already running and accumulating event
history, per that section's own "only possible because" framing — but the spec does not state
this as a formal prerequisite for IDEA-012.

## IDEA-013: Natural-key / episode contradiction in `events` (§8 vs §7.16)

**Status:** Open — spec correction needed, blocks M3
**Raised:** 2026-08-16 (ADR-0005)

### The proposal
Not a feature proposal — a contradiction between two parts of the frozen spec. `events.natural_key`
(§8) is defined as `kind + specific subjects + trigger state + config_version`, with no
date/timestamp component. §7.16's own worked example — "a premium at z>2 for five straight days
is one episode, and a scorecard that counts it as five is lying with its own n" — requires five
distinct rows to exist, or there is nothing for a scorecard to miscount as five. These two cannot
both hold unless "trigger state" is day-specific; if it is, the natural key is deduplicating
same-day reprocessing only, not a persistent condition — narrower than "dedup" implies elsewhere
in the spec (decision 26, §16 M3 gate).

### Why it is appealing
N/A — this is a flagged spec defect, not a proposed feature. No appeal case applies.

### Why it is parked
Not parked by choice — genuinely unresolved. M3 cannot be built without deciding which side is
wrong: either §8's natural-key formula needs an explicit time component, or §7.16's example (and
the raw-count-vs-episode-count scorecard distinction it implies) needs correcting. ADR-0005
documents the tension but does not resolve it, per this project's rule that ADRs cite spec
sections rather than invent detail.

### The version that would be legitimate
Two candidate resolutions, neither chosen here: (a) add a time/date component to the natural-key
formula so day-over-day persistence produces distinct rows, restoring the §7.16 example; or (b)
correct §7.16's example and the raw-count-vs-episode-count scorecard distinction to match a
coarser, non-time-scoped natural key. Either is a one-section correction release, same class as
v4.9.9/v4.9.10.

### Unfreeze triggers
Resolve with a `# DECISION:` comment or a spec correction release before the M3 dedup framework
is implemented — not a gate that opens on its own, a decision someone has to make. See ADR-0005,
"Open questions for M3," item 1.

## IDEA-014: Stale `§7.15` cross-reference on `events.episode_id` (should read §7.16)

**Status:** Open — one-line spec correction, cosmetic (does not block M3 on its own)
**Raised:** 2026-08-16 (ADR-0005)

### The proposal
Not a feature proposal — a stale citation. `events.episode_id`'s inline comment (§8) reads `--
§7.15 sample independence`. §7.15 is Risk Lens (Scenario Replay & Signal Crowding), unrelated to
episodes. The actual definition of episodes and sample independence is in §7.16 ("Episodes
(sample independence)"). The changelog records that §7.15 Risk Lens was inserted in v4.7 and
"subsequent module sections renumbered"; this comment most likely predates that renumbering and
was never updated.

### Why it is appealing
N/A — flagged spec defect, not a proposed feature.

### Why it is parked
Not parked by choice — a one-line comment fix that simply hasn't been made yet. Noted here so it
isn't rediscovered from scratch; see also the matching pattern under IDEA-006 (the "enters §7.16
when the simulator is built" line, which should likely read §7.17 for the same renumbering
reason), and IDEA-015 (a third, unrelated instance of the same class of drift).

### The version that would be legitimate
Change the §8 comment from `-- §7.15 sample independence` to `-- §7.16 sample independence`.

### Unfreeze triggers
None needed — this is a same-class correction to the v4.9.9/v4.9.10 changelog corrections already
in the document, whenever the next correction pass happens. See ADR-0005, decision 4.

## IDEA-015: CLAUDE.md's "never integrate a broker API" hard rule contradicts revised decision 15

**Status:** Open — project-instruction correction needed; also documents a systemic pattern (see note)
**Raised:** 2026-08-16 (found while backfilling this registry for ADR-0005)

### The proposal
Not a feature proposal — a contradiction between this project's CLAUDE.md hard rules and the spec
CLAUDE.md cites as its own authority. §17.1 states, verbatim: "Never integrate a broker API, order
routing, or any trade-execution code, in any phase, regardless of user-story framing (§2.15)."
Decision 15 — the section §17.1 cites — says the opposite: "(Revised at owner's direction, v4.1)
FlowDesk is two planes: the Intelligence Plane never executes; the Execution Plane may — own
capital only, behind hard gates." §17.1's hard rule is pre-v4.1 text that was never updated when
decision 15 was revised to add the gated Execution Plane, and it still cites the now-contradictory
section as its own authority. CLAUDE.md transcribed §17.1 faithfully — the drift originates in the
spec, not in CLAUDE.md's transcription of it.

### Why it is appealing
N/A — flagged spec/instruction defect, not a proposed feature.

### Why it is parked
Not parked by choice — this is a live disagreement between CLAUDE.md (which states it overrides
default behavior) and docs/architecture.md §2 decision 15, and an implementing agent will follow
whichever one it read most recently unless the owner states which governs. Not something an agent
should resolve unilaterally, since it changes what is and is not permitted to be built.

### The version that would be legitimate
Two options, both defensible, neither chosen here:
(a) **CLAUDE.md is a deliberate, stricter override** — the owner does not want execution code in
this repository regardless of what the spec's two-plane design permits. In that case CLAUDE.md's
line should stand, but should say explicitly that it overrides §2.15 rather than (incorrectly)
citing it as agreeing.
(b) **CLAUDE.md should match decision 15's gated two-plane design**, e.g.: "The Intelligence
Plane never executes. Execution-Plane code is out of scope until M0–M8 ship and decision 15's
gates are met — never build it opportunistically." This preserves the practical protection
(nothing gets built prematurely) without contradicting §2.

### Unfreeze triggers
Requires an explicit owner decision between (a) and (b), then a matching edit to CLAUDE.md's
§17.1 hard-rule line. Not resolved by this entry.

**Broader pattern (updated as later ADRs surfaced more instances — see IDEA-016, IDEA-018):**
this is now five confirmed instances of the same drift class, found across ADR-0004 through
ADR-0006 — a revision or renumbering happened somewhere in the document and a dependent reference
elsewhere was never updated to match: (1) `events.episode_id`'s `§7.15` comment, stale since the
v4.7 section renumbering (IDEA-014); (2) IDEA-006's "enters §7.16 when the simulator is built"
line, same v4.7 renumbering, should read §7.17; (3) this entry — pre-v4.1 text surviving the
decision-15 revision; (4) `IDEA-003`'s "confidence tier: low, per ADR-0007" citation — confidence
tiers are §7.16/v4.9 content, and none of the seven planned ADRs (ADR-0007 is private-vs-public
deployment mode) covers them (IDEA-018); (5) `alerts` (§8) having no `event_id` FK back to
`events`, because `alert_rules`/`alerts` predate the events-v2 lineage redesign and were never
updated to match it (IDEA-016) — this last one matters most for the pattern, since it shows the
drift isn't confined to prose citations, it reached the schema itself. A systematic pass — grep
every `§` cross-reference in the spec and CLAUDE.md, and check the target section actually still
says what the citing text claims, plus a schema-level check for tables that predate a later
redesign — is worth doing as its own piece of work, separate from the remaining ADRs.

---

## IDEA-016: `alerts` has no FK back to the `events` row it fired from

**Status:** Open — spec/schema defect, likely blocks M4's "alert history" join and M8's
per-alert retry view
**Raised:** 2026-08-16 (ADR-0006)

### The proposal
Not a feature proposal — a structural gap between two subsystems in the same schema.
`alerts(id PK, rule_id FK, fingerprint TEXT UNIQUE, entity TEXT, payload JSONB, fired_at,
delivered JSONB)` (§8) names no `event_id`, `calculation_run_id`, or `snapshot_id` — nothing
ties an alert row to the specific `events` row that triggered it. §4 states directly that
"Alerts, outcome tracking, websockets, and scorecards all consume this one stream [`events`],"
and decision 26's lineage discipline stamps snapshot/calculation/code lineage on every event so
any displayed number traces back to its source; `alerts` has none of that. The M4 gate requires
"one event appears in API, alert history, and outcome queue" (§16) — querying "the alert(s) for
event X," or displaying the event lineage behind a given alert, needs a link that isn't declared.

### Why it is appealing
N/A — flagged spec defect, not a proposed feature.

### Why it is parked
Not parked by choice — genuinely unresolved. `alert_rules`/`alerts` read as though they predate
the events-v2 lineage redesign (the v4.0 changelog: schema-versioned payloads, event_subjects,
rich natural keys, episodes, supersession, transactional outbox) and were never updated to match
it — the same class of drift as IDEA-014 and IDEA-015 (a revision happened and a neighboring
piece of the document/schema wasn't updated to match), this time in the schema itself rather than
in a cross-reference or a hard rule.

### The version that would be legitimate
Most likely fix, not applied here: add `event_id FK` (and arguably `calculation_run_id`,
`snapshot_id` for full parity with `events`) to `alerts`, so an alert's lineage is a direct join
rather than an implicit match on `fingerprint`'s embedded `entity`/date against
`events.primary_subject_id`/`occurred_at`.

### Unfreeze triggers
A `# DECISION:` comment or spec correction release, before M3's outbox work or M8's alert-rules
work begins — not something to discover mid-implementation. See ADR-0006, Open Questions and
Consequences.

## IDEA-017: M3's "exactly once" dispatch guarantee is unachievable for webhook channels

**Status:** Open — spec defect (overclaim), not just an unspecified mechanism
**Raised:** 2026-08-16 (ADR-0006)

### The proposal
Not a feature — an overclaim in the M3 gate. "dispatcher restart delivers pending exactly once"
(§16 M3 gate) is stated as a flat, universal guarantee across whatever
`notification_deliveries.channel` covers (`discord|websocket|inapp|email`, §8). Exactly-once
delivery is not achievable for Discord (the only P1 external channel, §7.16) by any dispatcher
design: Discord's webhook API exposes no idempotency key, so a send that times out after the
server actually received it is indistinguishable, from the dispatcher's side, from one that never
arrived. Retrying either duplicates the post or drops it — there is no third option. At-least-once
(accepting occasional duplicate posts on retry) is the real ceiling for that channel. In-app
delivery is different in kind: it's a Postgres write the app fully controls, so exactly-once
*materialization* (e.g., an insert gated by a `UNIQUE(outbox_id, channel)`-style constraint — not
currently declared in §8) is genuinely achievable there. This is presumably why the M8 gate
narrows its own claim to "retries never duplicate **in-app** alerts" rather than repeating M3's
unqualified "exactly once."

### Why it is appealing
N/A — flagged spec defect, not a proposed feature.

### Why it is parked
Not parked by choice. M3's literal gate text, taken as written, is unsatisfiable for the Discord
channel — no implementation can pass a test asserting exactly-once webhook delivery, because the
failure mode (an ambiguous timeout) is a property of the transport, not the code.

### The version that would be legitimate
Split the M3 gate's single "exactly once" line into two explicit guarantees: (a) in-app delivery
and outbox/event bookkeeping are exactly-once (Postgres-enforced); (b) external webhook channels
(Discord, and any future paid email channel) are at-least-once, with
`notification_deliveries.attempts`/`status` used to bound and surface retries rather than to
promise a guarantee the transport can't back. M8's "retries never duplicate in-app alerts" already
reads consistently with this split; M3's gate is what needs the correction.

### Unfreeze triggers
A spec correction release (same class as v4.9.9/v4.9.10) to reword the M3 gate, ideally before
M3's acceptance tests are drafted — a test suite that literally asserts "exactly once" against a
mocked Discord webhook would either be quietly weakened later or block the gate on an impossible
bar. See ADR-0006, Open Questions and Consequences.

## IDEA-018: `IDEA-003`'s "per ADR-0007" citation is dangling

**Status:** Open — stale cross-reference in the registry itself
**Raised:** 2026-08-16 (found while backfilling this registry for ADR-0005; registered separately
per ADR-0006 review)

### The proposal
Not a feature proposal — a stale citation inside `IDEA-003` (Options-flow detail), unrelated to
that entry's own subject. Its Unfreeze triggers section reads: "Options-chain ingestion has proven
stable enough that OI and volume fields are trustworthy (delayed chains are the weakest data
source in the stack — confidence tier: low, per ADR-0007)." Confidence tiers (high/medium/low,
weakest-input chips, hedged brief wording) are §7.16/v4.9 content ("Input confidence tiers and
score bands," introduced in the v4.9 changelog) — nothing to do with ADR-0007, which this
project's ADR plan (CLAUDE.md; ADR-0004/0005/0006's own numbering) assigns to private-vs-public
deployment mode. None of the seven planned ADRs covers confidence tiers at all.

### Why it is appealing
N/A — flagged citation defect, not a proposed feature.

### Why it is parked
Not parked by choice — a dangling reference that predates the current ADR numbering (IDEA-003 was
raised 2026-08-01, before ADR-0001 through ADR-0006 existed) and was never reconciled against it.
Left in place rather than corrected here per the instruction not to modify IDEA-001 through
IDEA-005 (IDEA-003 is one of those).

### The version that would be legitimate
Either drop the "(...per ADR-0007)" parenthetical from IDEA-003 (confidence tiers are already
fully specified in §7.16 with no ADR needed), or point it at wherever confidence-tier provenance
actually ends up documented, if that ever becomes its own ADR-adjacent note.

### Unfreeze triggers
A one-line edit to IDEA-003 itself, whenever someone other than this drift-preserving pass touches
that entry. Not resolved here.

---

## How to add an idea here

```
## IDEA-00N: <name>
**Status:** Parked
**Raised:** <date>
### The proposal
### Why it is appealing
### Why it is parked
### The version that would be legitimate
### Unfreeze triggers
```
