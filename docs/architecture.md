# FlowDesk — Cross-Market Capital Flow & Economic Intelligence Platform
## Architectural Design Document (Agent-Ready Build Spec)

**Version:** 4.9.10 (FROZEN — final pre-build corrections; the next artifact after this document is code)
**Status:** Feature-frozen — build phase. M0 spikes complete; **M0 not closed** (ADRs + oracle fixtures outstanding since 2026-07-31). Zero modules shipped, zero outcome rows accumulated. **This is the last documentation release before implementation. Further findings go to `docs/IDEAS.md` or arrive as code.**
**Changes in 4.9.10 (final pre-build corrections from external quant review — permitted under the freeze; no new modules, signals, or integrations; no §2 decision changed):** (a) **§7.16 cost-model contradiction resolved** — the v4.8 capacity/market-impact paragraph and the older stated-limitations block both claimed to govern the same simulator (square-root impact vs flat 10 bps, in the same section); the limitations block is rewritten as a two-level cost hierarchy so each report declares which level produced it. (b) **§16 M2 gains effective-dated universe membership** — closing a look-ahead hole the point-in-time layer missed: querying today's S&P 500 constituents for a 2019 breadth or scanner backtest leaks survivorship even over perfect bars; membership becomes an as-of-dated table like every other revision-prone feed, and observations gain provider/feed/venue-scope provenance so two strategies can never silently run on different market views. (c) **§18.0 pricing corrected against the vendor's current page (verified 2026-08-14)** — the "~$79 real-time tier" this document warned against does not exist: $79 (Developer) is also 15-minute delayed, and real-time is the $199 Advanced tier, which strengthens the existing conclusion; tier references are de-anchored from plan names and dollar figures to capabilities, per the new rule in §18.0. (d) **§18.0a Gate R refined** — the pass/fail entry-timing test becomes an edge-decay curve (signal-time / +1 / +5 / +15 / +30 / next-open), because a half-life is decision-relevant where a boolean is not; folded into IDEA-006's registry description. (e) **§18.0a paper-gate clarification** — paper trading validates software behavior, not fill economics; broker paper simulators model neither impact, queue position, nor information leakage, and broker paper feeds may be single-venue rather than consolidated, so decision 15's gate 3 judgment weighs reliability, live-vs-paper divergence, and the §7.16 rule-7 tick-TCA — never paper P&L alone. (f) **Registered, deliberately not specified** (the freeze's registry mechanism, `docs/IDEAS.md`): IDEA-007 execution order state machine with broker reconciliation-disarm; IDEA-008 expanded live risk-state table (liquidity/ADV participation, gross/net exposure, theme/sector/correlation concentration, stale-data and execution-quality disarms); IDEA-009 real-time market-data substrate (dual event/receive timestamps, sequence-gap detection with degraded states, market-status/tradability as a first-class object); IDEA-010 regime hysteresis + minimum residence; IDEA-011 block-bootstrap/HAC inference for overlapping forward-return windows; IDEA-012 incremental-information confluence (effective independent signal count) on the Signal Interaction Graph. Each is Execution-Plane or post-outcome-data work; specifying them now would be design elaboration in place of execution, which the freeze exists to prevent.

**Changes in 4.9.9 (corrections and clarifications only — permitted under the freeze; no new modules, signals, or integrations):** (a) **§3 Wave 2a sequencing corrected** — Websockets were listed first in §3 while §16 places them last in Phase 2a; §16 governs and §3 now says so, because an agent reading §3 first would build streaming before there is anything worth streaming. (b) **§18.0 gains the real-time evidence gate** — the existing binding budget rule is made specific for the one upgrade the owner keeps returning to, and the gate names the measurement that would justify it rather than leaving the decision to appetite; also records that the Execution Plane already gets live data free from broker paper APIs, so "real-time" and "paid data feed" are not the same purchase. (c) **§16 M0 status sharpened** — the two remaining deliverables are dated and the moat cost of the delay is stated. (d) **§16 gains an interim outcome-logging note** — a zero-code practice that starts the §7.15 evidence clock before M5 ships. **Deliberately NOT adopted here:** the entry-timing sensitivity metric that the §18.0 gate depends on is a new simulator metric, which the freeze forbids; it is registered as **IDEA-006** in `docs/IDEAS.md` and enters §7.16 when the simulator is built, not before. No §2 decision changed.

**Changes in 4.9.8 (documentation only — no architecture change):** §18 gains **18.0, a value-per-dollar ranking** of every paid data option, ordered by how much of the platform improves per dollar rather than by capability. Headline findings: a **free** second data source ranks above every paid item because it converts yfinance's silent failures (ADR-0005) into detectable cross-source disagreements; the ~$29/mo delayed equity feed is the only S-tier purchase, and the ~$79 real-time tier is explicitly *not* worth buying because FlowDesk's signals cannot use sub-15-minute precision; component-pricing research is analytically the strongest upgrade in the document and simultaneously costs more per year than the portfolio it would inform. Includes a four-step purchase sequence tied to the existing budget rule and a CAD FX note.
**Changes in 4.9.7 (owner-directed additions of free institutional metrics):** (a) new **§7.3.1 Futures Positioning (CFTC COT)** — weekly, free, official net positioning by trader category with 3-year percentile z-scores, week-over-week change, commercial-vs-speculator divergence events, the Tuesday-data/Friday-release `available_at` pattern, and mandatory caveats on category definitions and the weak out-of-sample record of COT extremes; slots into Phase 2b. (b) §6.3 extended from put/call alone to full **volatility structure** — VIX9D/VIX/VIX3M/VIX6M term ratio (backwardation as a stress marker a spot level cannot express), CBOE SKEW, and implied correlation. (c) §6.4 gains four FRED rows (`DFII10`, `T10YIE`, `SOFR`, `IORB`) and three computed series decomposing nominal rate moves into real-yield vs breakeven components plus a SOFR−IORB funding-stress spread. (d) §7.11 Swing Desk gains **float rotation** and **Amihud illiquidity**, the latter feeding the §7.17 capacity ceiling from the same number that scores the candidate. (e) §7.16 scorecards gain an implied-correlation regime breakdown, testing whether rotation signals degrade in high-correlation tapes as theory predicts. Deliberately still excluded: valuation and equity-research metrics (forward P/E, EV/EBITDA, FCF yield, analyst revisions, fund flows) — paid, and outside the capital-flow frame per §19.1.
**Changes in 4.9.6 (owner-directed sequencing change + extension of an existing module; no new modules):** §7.11 extended from a breakout scanner into the **Swing Desk** — setup geometry (swing pivots, ATR distances, MA distances, range position) as descriptive measurement with grep-enforced forbidden recommendation vocabulary; a risk calculator that computes position arithmetic **only** from user-supplied invalidation and risk budget, recommending neither; **idea-source tagging** feeding per-source expectancy into outcome tracking; and swing-specific scoring components (relative strength vs sector, pullback depth, volume dry-up/thrust, thematic and bottleneck membership, earnings-proximity flag). Promoted in §16 from Phase 2b to a new **M5.5**, immediately after outcome tracking — never before it. Entries, exits, automated sizing, and execution remain governed unchanged by decisions 15 and 17.
**Changes in 4.9.5 (corrections from M0 spike 4; M0 milestone complete):** §6.5 records verified EDGAR behaviour — mandatory contact User-Agent (403 without; now a required env var that must fail loudly at startup), CIK as durable identity over reassignable tickers, the ~1,000-filing `recent` cap that is 57% Form 4 for a mega-cap and therefore requires paging the `files` array for history, measured disclosure lags (Form 4 4–5 days, 8-K 3–6 days) with `acceptanceDateTime` as `available_at`, the Form 4 XML-parsing requirement for transaction dates, and observed rate-limit headroom. §16 M0 marked complete.
**Changes in 4.9.4 (corrections from M0 spike 3b, no new features):** §6.4 records measured revision magnitudes and publication lags — 27 of 28 observations revised after first print (payrolls revised −801k to −1,246k jobs per month), publication lag varying more than twofold within a series (31–80 days) so freshness thresholds must be per-series and cadence-aware rather than a global constant, missing observations returned as the string `"."` requiring explicit gap handling, and GDP's 119–175-day lag confining quarterly macro to slow-context use.
**Changes in 4.9.3 (corrections from M0 spike 3, no new features):** §6.4 records verified FRED point-in-time behaviour — revisions confirmed on GDPC1 (+0.094% on Q1-2025), the observation *set* shown to grow over time (1 quarter visible on 2025-05-01 vs 4 today, making availability filtering mandatory for row existence as well as value), the `realtime_start`/`output_type=4` mechanism for as-of queries and publication dates, the widened-real-time-window requirement and its misleading 400 error, loud failure on unknown series, and the one-session lag on daily series. §16 M0 records spike 3 complete.
**Changes in 4.9.2 (corrections from M0 spike 2, no new features):** §6.12 records the verified yfinance price-basis mapping — `auto_adjust=False` returns split-adjusted (not raw) prices, so `c_raw` must be reconstructed from cumulative split factors or captured live; `auto_adjust=True` silently substitutes total-return OHLC; total-return series are revision-prone and excluded from the idempotency gate; column sets vary by security type; unknown and delisted tickers return empty frames without raising and must degrade provider health. §16 M0 records spike 2 complete.
**Changes in 4.9.1 (corrections from M0 spike 1, no new features — permitted under the freeze):** (a) §6.10 records the verified FX direction for `KRW=X` (KRW per USD; invert) plus a magnitude-range assertion on ingestion; (b) §7.5 gains the session-pairing decision with the measured 23.90% vs 61.01% divergence that motivated it, the requirement to display all three source timestamps, and volatility/insufficient-history caveats for newly listed pairs; (c) §16 M0 records spike 1 complete with its carried-forward open item (verify ADR ratio with the depositary).
**Changes in 4.9 (final):** (a) reproducibility manifests emitted with every report and scorecard snapshot (full lineage + result hash; no manifest, no publication); (b) right-sized uncertainty — input confidence tiers (high/medium/low) with weakest-input chips and hedged brief wording, plus empirical score bands from calibration tables instead of parametric error propagation; (c) futures continuous-contract roll methodology required before the first Execution-Plane futures strategy; (d) feature freeze declared — build M0–M2 and accumulate six months of outcome data before any further architectural change.
**Changes in 4.8:** (a) **mandatory factor attribution** in every simulator report (alpha, t-stat, factor loadings; factor-replicator rule) plus a multi-benchmark panel with vol-normalized comparison; (b) capacity ceilings and square-root market-impact modeling replacing flat-bps costs; (c) declared entry-timing conventions per strategy; (d) required economic-rationale block in every pre-registered hypothesis; (e) graveyard upgraded to a research database with failure classification and retest conditions; (f) score calibration tables (what a score of 87 historically meant) and FDR correction across the live signal family; (g) signal interaction graph (co-occurrence, conditional hit rates, lead-lag) extending crowding detection; (h) effective-dated theme membership with confidence/source; (i) Phase 3 Portfolio Construction Layer and Research Knowledge Graph; (j) §19 opportunity ranking revised — research infrastructure and data products above dashboards.
**Changes in 4.7:** new §7.15 Risk Lens (Phase 2b) adopting institutional risk-system questions in descriptive form — historical Scenario Replay over stress episodes and macro-condition windows (labeled how-it-behaved, never forecast) and Signal Crowding Detection over FlowDesk's own active events (correlated events flagged as one bet, not five confirmations), plus exposure decomposition extending the Phase 3 portfolio note; Aladdin row added to the §19.1 category table; subsequent module sections renumbered.
**Changes in 4.6:** §7.16 gains the Performance Claim Standard — eight mandatory disclosures on every performance figure FlowDesk publishes (live-vs-simulated, benchmark, net-of-costs, drawdown/Sharpe with trial count, capital scale, named verifier, regime coverage, graveyard denominator) plus the red-flag list for evaluating third-party track-record claims.
**Changes in 4.5:** Phase 3 gains an optional Geopolitical & Supply-Chain Context Feed — hosted-API-only integration (AGPL source barred by decision 24), strictly annotation-not-signal, labeled coincident-not-causal, promotion gated on outcome-scorecard evidence; explicit rejection of "pre-news data" claims.
**Changes in 4.4:** Strategy Research Protocol gains the data-resolution floor (declared decision timescale per registry entry; coarser-than-decision-resolution backtests rejected; tick-based TCA required before live arming even for slow strategies); §18 gains an Execution-Plane tick-data row (usage-based Databento-class pricing); glossary gains time & sales, TCA, and fill-model entries.
**Changes in 4.3:** §7.16 gains the binding Strategy Research Protocol for mass strategy testing — pre-registered hypothesis registry, honest trial counting (every parameter set is a trial), survivors reported as 1-of-N with deflated-Sharpe-class haircuts, in-sample/validation/held-out splits with a touch-once rule, sample and regime floors, a cost-realism first gate, and the strategy graveyard as a first-class searchable output feeding the decision-15 pipeline.
**Changes in 4.2:** Phase 3 options module gains Income Strategy Analytics — premium-yield measurement at standard deltas/DTE with VRP context and assignment breakevens, grep-enforced forbidden income framings, mandatory CBOE PUT/BXM benchmark ingestion vs SPY as the empirical anchor for wheel-class claims, and an optional flagged `wheel_synthetic` simulator template with model-risk caveats.
**Changes in 4.1:** (a) decision 15 revised: FlowDesk split into an Intelligence Plane (never executes; the only plane ever hosted/shipped) and an Execution Plane (own capital, ENABLE_EXECUTION default-off, profile-excluded from public builds, armed per-strategy only through backtest → paper → written human arming, with per-position caps, daily-loss kill switch, and order-rate limits hard-coded); decisions 23 and §19.5 reconciled; (b) Dealer Positioning (GEX) panel added to the Phase 3 options module; (c) orderflow/microstructure routing note — tick concepts (delta, absorption, speed of tape, TPO) routed to the Execution Plane with paid tick data; bar-approximated volume profile overlay adopted as the one honest Intelligence-Plane import.
**Changes in 4.0:** (a) §16 replaced with a vertical-slice milestone plan (M0–M10, release tags v0.0–v0.3, exit gates, 14-day soaks, honest 4–5-month estimate) — now the single normative sequence, resolving the §3/§16 contradiction; (b) typed FX (base/quote + convert()) fixing a direction ambiguity that could silently invert premiums, and raw-vs-adjusted price bases declared per signal with an effective-dated corporate_actions confirmed-action workflow; (c) point-in-time provenance (available_at/revision) on revision-prone feeds with availability-filtered backtests; (d) snapshot/watermark/calculation-run lineage, events v2 (schema-versioned payloads, event_subjects, rich natural keys, episodes, supersession), transactional outbox + per-channel delivery states, task_runs, external dead-man monitor; (e) scorecard episodes with bootstrap CIs and frozen live-vs-replay separation; (f) LLM brief re-specified as evidence-path structured claims with semantic validation, Tensions section added to the verbatim prompt; (g) two explicit security deployment modes; (h) binding decision 26 defining what lineage is adopted and what is deliberately not, at solo/zero-cost scale.
**Changes in 3.0 (adversarial self-review):** (a) Phase 2 restructured into three waves with gates and an explicit cut order; (b) new §6.12 hardening layer — bar validation & quarantine, corporate-action/split handling, delisting & survivorship policy, exchange calendars, and a yfinance rate-budget table (constituent breadth demoted to daily; intraday breadth is sector-level); (c) unified `events` table replaces six per-module event tables, with FK-integral outcome tracking; (d) `config_version` stamping + segmented scorecards protecting self-grading integrity from threshold tuning; (e) runtime numeral validator with template fallback for the LLM brief; (f) ops heartbeat/dead-man alerts, first-run backfill screen, restore-drill and split-detection acceptance criteria, property/golden tests; (g) build-order pacing estimates and wave gates; (h) binding decision 25 making all of the above Phase 1 infrastructure.
**Changes in 2.14:** §19 gains 19.4 The Novelty Thesis — counter-positioning via self-grading, dislocation-intelligence category ownership, the auditability why-now, and the honest-research content flywheel; former 19.4 (regulatory line) renumbered 19.5.
**Changes in 2.13:** §7.11 scanner upgraded — thematic cross-reference against theme/bottleneck states, a mandatory red-flag panel (dilution, offering detection, insider net selling, liquidity reality), scorecard + positive-skew note on the page; Physical AI / Autonomy starter theme added to §7.6.
**Changes in 2.12:** §7.13 Earnings Intelligence expanded — post-earnings reaction monitor (move vs implied, surprise–reaction divergence, extreme-reaction flags with forbidden-vocabulary rule), drift-vs-reversion measured via outcome tracking plus opposing `pead_drift`/`earnings_reversion` simulator templates, and an EDGAR 8-K report debrief (AI-gated, grounded) with paid transcripts as a §18 Tier 2 row.
**Changes in 2.11:** (a) binding decision 24 — licensing & IP hygiene: FlowDesk licensed MIT/Apache-2.0, LGPL confined to the companion repo, AGPL and restrictively dual-licensed code banned with a CI license audit; (b) new §19 Commercialization Notes (non-binding): incumbent comparison, differentiation thesis (dislocation focus + self-grading signals + auditability), beachhead sketch, and the regulatory line.
**Changes in 2.10:** (a) research–live parity named as an explicit architectural property (§4); (b) binding decision 23: execution research confined to a companion repo with a two-stage engine progression (minimal paper consumer → NautilusTrader), FlowDesk's simulator deliberately simple; (c) Execution Research Companion design note added (§7.17); (d) CI supply-chain hygiene line (§13).
**Changes in 2.9:** (a) Phase 3 options module gains a volatility-gap detection spec — IV-vs-realized spread with percentiles and an earnings implied-move monitor, plus a flagged zero-cost experimental variant via delayed chains (§7.17); (b) simulator metrics add payout ratio and a caveated full-Kelly descriptive statistic (§7.16); (c) binding decision 22: straddles never framed as direction-proof, Kelly never sizing advice, "profit regardless of direction" added to forbidden vocabulary.
**Changes in 2.8:** (a) Signal Outcome Tracking added to §7.15 — every emitted event auto-scored on forward returns, with public per-signal-type scorecards and decay detection; (b) deterministic tension detector in the brief's context builder + a Tensions brief section (§7.14); (c) Decision Journal added as a Phase 3 design note (§7.17); (d) binding decision 21: multi-agent LLM decision frameworks rejected as decision-makers, with their reflection-loop and adversarial-analysis ideas adopted in deterministic form.
**Changes in 2.7:** (a) simulator gains a challenger interface for external signal generators (§7.16); (b) ML Challenger Harness added as a Phase 3 design note with Kronos as reference implementation and a walk-forward evaluation protocol (§7.17); (c) binding decision 20: ML price forecasts are offline challengers only, never dashboard features, with anomaly detection named as the one permissible live-side research direction.
**Changes in 2.6:** new §18 Future Work — Paid Upgrade Path: three spend tiers mapping cost → data unlock → module improvement, a binding budget rule (the simulator earns the budget), a never-pay-for list, and suggested sequencing. Zero-Cost Mode remains the permanent base configuration.
**Changes in 2.5:** (a) new Phase 2 module — Supply-Bottleneck Detector (§7.10): segment-level scarcity-repricing states (Emerging/Confirmed/Easing) over theme role tags, industry-agnostic by construction; (b) `bottleneck_hold` simulator template with mandatory validation against the 2025–26 memory/photonics window (§7.16); (c) starter Quantum theme demonstrating generality (§7.6); (d) binding decision 19: repricing detection, not scarcity forecasting.
**Changes in 2.4:** (a) Smart Money extended to executive-branch (OGE) disclosures with a mandatory high-volume "managed/systematic" filer rule (§6.5, §7.3); (b) dual-date chart markers making disclosure lag visible (§7.3); (c) `disclosure_copy` simulator template — the honest test of copy-the-politician claims (§7.16); (d) binding decision 18: disclosure analysis, never "real-time copy trading."
**Changes in 2.3:** (a) new Phase 2 module — Catalyst Sympathy Scanner (§7.9, second-order laggard detection with theme role tags); (b) Market Regime Badge added to §7.1 (Phase 1) with a `rotation_long_gated` simulator template in §7.16; (c) Pair Watch documents single-stock-vs-sector-ETF relative-strength-loss monitoring; (d) Ticker Dossier added as a Phase 3 design note; (e) binding decision 17 defines what is adopted vs permanently excluded from momentum-trading playbooks.
**Changes in 2.2:** (a) new Phase 2 module — Pair Watch relative-value monitor (§7.8); (b) §7.14 backtest upgraded to a Backtest & Strategy Simulator with long/short, market-neutral, and pair-reversion strategy templates and hedge-fund-grade metrics; (c) binding decision 16 defines which hedge-fund strategy concepts are in scope (as analytics) and which are permanently out; (d) Deal Spread Watch added as a Phase 3 design note.
**Changes in 2.1:** (a) new Phase 1 module — Cross-Listing Premium Monitor (§7.5); (b) the entire platform now defaults to **Zero-Cost Mode** (§6.0): free data sources, free hosting, LLM optional with a deterministic template fallback; (c) new binding decisions 13–15 in §2.
**FEATURE FREEZE (declared at v4.9).** The architecture is frozen. From this point only bug fixes, corrections, and clarifications are accepted — no new modules, no new signals, no new integrations — until Milestones M0–M2 are built as specified and at least six months of real outcome-tracking data exist. Future changes are to be driven by observed performance and live failure modes, not by further design refinement. An agent or owner tempted to add a feature should instead open a registry entry and let the data decide. Rationale: every remaining risk in this project is now an execution risk, and design elaboration is the most comfortable way to avoid execution.

**Intended reader:** Any AI coding agent or engineer — including smaller/less capable models — with zero prior context. This document is deliberately over-specified: exact formulas, pinned data-series IDs, verbatim prompt templates, JSON examples, and a strict sequential build order. When in doubt, follow §17 (Agent Guardrails) literally. Do not invent features not listed here.

---

## 1. Product Vision

FlowDesk answers one question: **"Where is capital moving right now, and why?"**

It is a *market intelligence* platform, not a market *data* platform. Bloomberg/TradingView show raw data and expect the user to interpret it. FlowDesk computes deterministic signals across markets and the macro economy, then uses an LLM to synthesize them into plain-English narratives.

The product is organized into five **layers** (not asset-class tabs):

| Layer | Question it answers | Core content |
|---|---|---|
| **Markets** | What is price/volume doing? | Indices, sector ETFs, breadth, crypto, commodities proxies |
| **Economy & Credit** | What are financial conditions doing? | Rates, yield curve, mortgage rates, credit spreads, housing, inflation, delinquencies |
| **Smart Money** | Who is buying/selling? | Congressional disclosures, corporate insider (Form 4) buying, disclosure clusters |
| **Themes** | Which narratives are gaining capital? | Cross-asset theme baskets (AI Infrastructure, Energy, Defense, …) with composite scores |
| **Expectations** | What does the market think happens next? | Prediction-market probabilities (rate cuts, elections, macro events) |
| **Dislocations** | Where are related assets mispriced against each other? | Cross-listing premium monitor (ADR vs home listing, e.g. SKHY vs KRX 000660), FX-adjusted, with persistence context |

On top: a **Daily Capital Flow Brief** and an **Ask-the-Market** Q&A, both LLM-generated strictly from computed signals.

The primary user is the builder (a technically sophisticated retail trader); the secondary audience is hiring managers evaluating this as a portfolio project demonstrating real-time data engineering, time-series analytics, event-driven systems, and applied AI.

**Feature admission test (apply to every proposed addition):** *"Does this help explain where capital is flowing or how the economy is changing?"* If no (budgeting, bill pay, loan calculators, listings), it is out of scope permanently.

---

## 2. Deliberate Design Decisions (binding on the implementing agent)

These correct factual errors in the original brainstorm and de-risk scope. Do not override them.

1. **Congressional trades are NOT real-time.** The STOCK Act allows up to 45 days between transaction and disclosure. Module is named "Smart Money / Disclosure Tracker." UI must always show both `transaction_date` and `disclosure_date` and the computed lag. Same rule for 13F-style institutional data (quarterly, 45-day delay) — if ever added, delays are always displayed.
2. **Prediction markets replace sportsbooks as the "expectations" signal.** Polymarket (and optionally Kalshi) have public APIs, are macro-relevant (rate cuts, elections, geopolitics), and are legally/analytically cleaner than sharp-money sports feeds. Sports odds are demoted to an optional Phase 3 module behind flag `ENABLE_SPORTS`; nothing depends on it.
3. **Social-media sentiment scraping is excluded.** Twitter/Reddit APIs are expensive or ToS-hostile, and sentiment scraping is a maintenance tarpit. The "sentiment" role is filled by measurable proxies FlowDesk already computes: breadth, put/call ratio (CBOE daily, free), VIX, credit spreads, and prediction markets.
4. **Options intelligence is Phase 3.** Real-time unusual-options-activity feeds are expensive. Phase 1 includes only the free daily CBOE total put/call ratio as a sentiment tile. Full options flow is a flag-gated Phase 3 module.
5. **Kafka and Kubernetes are removed.** Data volume (hundreds of symbols, minute bars at most, daily macro series) does not justify them. Stack: Docker Compose, Redis pub/sub, Celery. Upgrade paths are documented, not built.
6. **"Institutional flows" are approximated with honest proxies:** ETF shares-outstanding changes (creation/redemption), relative strength, volume z-scores, and equal-weight vs cap-weight ratios. No fake "institutional inflow" numbers.
7. **The LLM is a summarizer, never a calculator.** All signals are computed by deterministic, unit-tested Python in `signals/`. The LLM receives a compact JSON of pre-computed signals and writes prose. It is forbidden (by system prompt and by context design) from inventing numbers, predicting prices, or giving trade advice.
8. **"Multi-agent AI" is rejected as architecture.** The GPT-suggested six-agent design (Market Agent, Risk Agent, …) is implemented as *sections of one structured brief from one LLM call* over one context JSON. Identical output quality, one-sixth the cost and complexity. Do not build an agent framework.
9. **Whale-wallet tracking, satellite data, and social scraping are cut.** Each is a standalone project. Crypto flows use ETF flow data, funding, OI, liquidations only.
10. **Personal portfolio intelligence is Phase 3+ (design note only).** Do not build user accounts, brokerage linking, or portfolio tables before then.
11. **Macro data is pinned to FRED series IDs (§6.4)** so any agent fetches exactly the right series with zero ambiguity. FRED is free, stable, and official.
12. **A backtest-lite harness is mandatory in Phase 2.** Rotation, theme, and breakout signals must be replayable over history for credibility.
13. **Zero-Cost Mode is the default operating mode (§6.0).** Every Phase 1–2 feature must run on free data sources, free hosting, and zero recurring spend. Paid providers are optional upgrades behind the same interfaces, never requirements; the full upgrade path is specified in §18. Any agent adding a dependency with a mandatory fee is violating this document.
14. **The Cross-Listing Premium Monitor detects and explains dislocations; it never calls them "arbitrage," "free money," "guaranteed," or "riskless."** Cross-listing premiums are real and measurable, but they can persist or widen for structural reasons: limited ADR↔ordinary-share fungibility/conversion, short-sale constraints and borrow costs, FX risk, capital-market access frictions, and index-inclusion flows. The module's job is to quantify the premium, track its history, and surface those persistence factors — the user decides what to do. All generated sentences must include a persistence qualifier (template enforced, §7.5).
15. **(Revised at owner's direction, v4.1) FlowDesk is two planes: the Intelligence Plane never executes; the Execution Plane may — own capital only, behind hard gates.**
    - **Intelligence Plane** = everything else in this document: signals, events, scorecards, brief, API, UI. It never emits recommendations, sizing, or orders, and it is the ONLY plane that may ever exist in a hosted, public, shared, or commercial deployment (§19.5). All prior "never say buy X" rules bind this plane unchanged.
    - **Execution Plane** = a distinct component (separate service in the monorepo or the §7.17 companion repo — owner's packaging choice), `ENABLE_EXECUTION=false` by default, excluded at the compose-profile level from public mode so it cannot be accidentally shipped. It consumes the Intelligence Plane's read-only signal API only.
    - **Arming gates (all mandatory, in order):** a strategy may trade real money only if (1) it has a passing, committed simulator report (§7.16) under the §16 lineage rules; (2) it has completed a defined paper period (default 60 sessions) on the same code with live-vs-paper divergence within stated tolerance; (3) a human explicitly arms it per-strategy, in writing, in `docs/ARMING_LOG.md` with capital cap and review date. No gate may be waived by config.
    - **Standing risk controls (non-negotiable in code, not config):** per-position cap, daily-loss kill switch that flattens and disarms, order-rate limit, paper-mode default on every restart until re-armed, and full order/decision logging through the same events/outbox machinery so execution gets scorecards too.
    - The Execution Plane trades only the owner's own capital. Managing anyone else's money is out of scope permanently (adviser registration territory).
16. **Hedge-fund strategies are incorporated as analytics, not as products.** Concepts that map onto FlowDesk's monitoring/simulation role are IN: pairs relative value → Pair Watch (§7.8); long/short & market-neutral & momentum → strategy templates in the simulator (§7.16); global macro → the Economy/Expectations layers FlowDesk already is; quant discipline → the pure-function + backtest architecture itself. Strategies that are execution businesses or need paid institutional data are OUT permanently: convertible arbitrage and capital-structure arbitrage (bond/CDS/convert pricing isn't freely available), distressed debt, HFT, and live merger arbitrage. A manual-entry Deal Spread Watch is a Phase 3 design note only (§7.17).
17. **Momentum-playbook ideas are incorporated as detection and simulation, never as an execution loop.** From discretionary momentum/relative-strength playbooks, FlowDesk adopts: second-order beneficiary mapping → Catalyst Sympathy Scanner (§7.9); the market-condition gate → Regime Badge (§7.1) plus a gated simulator template (§7.16); single-name relative-strength-loss vs sector → Pair Watch stock/ETF pairs (§7.8); the pre-trade research checklist → Ticker Dossier design note (§7.17). Permanently excluded: the wake-every-15-minutes trade loop, stop enforcement, position sizing, entry/exit timing (opening ranges, pullback buys), and any buy/hold/trim/exit output — these are execution (§2.15), and Zero-Cost 15-minute data couldn't support intraday tactics honestly anyway. FlowDesk may say "AVGO moved, VRT hasn't"; it may never say "buy VRT."
18. **Government-official trade tracking is disclosure analysis, never "real-time copy trading."** Executive-branch disclosures join the Smart Money module under the same honesty rules as Congress, plus two of their own: (a) FlowDesk never claims real-time tracking of any official's trades — disclosures lag by weeks and are frequently filed late, and the UI must show the lag and late-rate; (b) filers whose portfolios are professionally/systematically managed (detected by the high-volume rule, §7.3) are analyzed at aggregate level only — per-trade signals from a direct-indexed portfolio are noise, and presenting them as an official's "picks" would be false. "Predicting an official's next trades" is out permanently: there is no informational basis for it in range-banded, weeks-late filings. All officials are presented neutrally, by the same rules, regardless of party or office.
19. **Bottleneck detection is repricing detection, not scarcity forecasting.** The Supply-Bottleneck Detector (§7.10) identifies segments the market is *already* repricing as supply-constrained, with persistence and breadth filters to separate segment scarcity from single-stock stories, and a mandatory `Easing` state so every detection has a defined end. It never forecasts which segment becomes constrained next, never uses "next Nvidia/Lumentum" language, and is never hardcoded to AI — segments derive entirely from theme role tags in config, so new industries (quantum, biotech tooling, grid equipment) onboard without code changes. Its claims are validated in the simulator (`bottleneck_hold`, §7.16) against the 2025–26 memory/photonics window before any marketing statement about the feature is made.
20. **ML price-forecast models are offline challengers, never dashboard features.** Time-series foundation models (Kronos-class K-line models and successors) may be integrated ONLY through the simulator's challenger interface (§7.16/§7.17): their forecasts are evaluated as candidate signals under identical cost assumptions and walk-forward protocol, and results appear in backtest reports only. FlowDesk never displays a model's price forecast on any live page, chart, or brief — a probabilistic OHLCV forecast shown to a user functions as advice regardless of disclaimers, and violates decision 7's separation. If a challenger ever demonstrates robust after-cost edge, the promotion path is: documented report → paper-trading harness (separate repo) → human decision. The one live-side use permitted as a future research note is inverted: using such a model's implied distribution for *anomaly detection* (realized bars far outside model-implied range = dislocation flag), which is detection, not prediction.
21. **Multi-agent LLM decision frameworks (TradingAgents-class) are not integrated as decision-makers.** The rejection reasons are stated by such frameworks' own documentation: LLM-driven runs are non-reproducible ("two runs of the same ticker and date can differ"), backtest figures are explicitly not replicable, and per-decision multi-agent runs are costly — which together make attribution (the foundation of every FlowDesk evaluation gate) impossible. What FlowDesk adopts from the category instead, in deterministic form: the decision-log-with-realized-outcome reflection loop → Signal Outcome Tracking (§7.15); adversarial bull/bear analysis → the deterministic tension detector feeding the brief's Tensions section (§7.14); grounded-snapshot discipline → already decision 7. An LLM-agent challenger could in principle plug into the §7.16 challenger interface, but is deprioritized indefinitely: a non-deterministic challenger requires N-run distributional evaluation at material LLM cost to say anything, and Zero-Cost Mode forbids the spend. A user-facing **Decision Journal** (log your own trade decisions with the FlowDesk context snapshot auto-attached; auto-scored later vs SPY) is added as a Phase 3 design note in §7.17 — the reflection loop pointed at the human, which is FlowDesk's actual decision-maker.
22. **Options-mispricing ideas are integrated as volatility-gap detection; straddles are never framed as direction-proof; Kelly is never sizing advice.** A long ATM straddle is a *volatility* bet with a breakeven at strike ± total premium — the market charges the expected move up front, so "profit regardless of direction" is false and that phrasing (and equivalents) joins the grep-enforced forbidden vocabulary for the options module. The legitimate kernel — comparing what the options market implies to what history delivered (IV vs realized vol; earnings implied move vs past realized moves) — is pure dislocation detection and enters the Phase 3 options module (§7.17), with a flagged zero-cost experimental variant. The Kelly criterion appears only as a caveated descriptive statistic of backtest results (§7.16); position sizing for the user's actual trades remains excluded under decision 17.
23. **Execution research lives in a companion repository with a defined engine progression; FlowDesk's simulator stays simple on purpose.** The simulator (§7.16) evaluates *signals* at daily cadence with flat costs — adopting a production execution engine (NautilusTrader-class: nanosecond event-driven fills, order-book simulation, venue adapters) inside FlowDesk would add enormous complexity for zero benefit at that granularity and would blur decision 15. The sanctioned path, specified as a §7.17 design note: strategies that pass the backtest gate graduate to a separate execution-research repo whose only interface to FlowDesk is the read-only signal API — stage A, a minimal paper-trading consumer (broker paper API, deterministic rules, benchmark reporting); stage B, if warranted, a NautilusTrader implementation for high-fidelity re-testing (realistic fills, slippage, the borrow/margin modeling the FlowDesk simulator explicitly disclaims) and eventual paper/live execution with research-to-live parity. Under revised decision 15, execution now lives in the Execution Plane (companion repo or monorepo service — same gates either way); the Intelligence Plane and its simulator still never gain order types, venue adapters, or execution semantics, and the engine progression stands: minimal paper consumer first, NautilusTrader for high-fidelity re-test and live.
24. **Licensing and IP hygiene (critical if commercialization is ever pursued).** FlowDesk's own repository is licensed MIT or Apache-2.0 (Apache-2.0 preferred for its patent grant) so the codebase stays clean for both the portfolio phase and any future commercial phase. Dependency rules: (a) permissive licenses (MIT/BSD/Apache) — freely usable; (b) LGPL (NautilusTrader-class) — usable as a library/separate process in the *companion* repo, never vendored into FlowDesk source; (c) **AGPL and restrictively dual-licensed projects (FinceptTerminal-class) — no code, no vendoring, no forking, ever.** AGPL's network clause would force open-sourcing any hosted service, and aggressive dual-license terms (commercial license required for "any business use," claims surviving API replacement, liquidated-damages postures) make such code radioactive for a startup path regardless of enforceability. Reading such projects for *ideas* is fine; their code never enters this tree. CI enforces this with an automated license audit (pip-licenses / license-checker) that fails the build on AGPL or unknown licenses. (This document is engineering guidance, not legal advice; consult a lawyer before any commercial launch.)
25. **Integrity hardening is Phase 1 infrastructure, not polish.** Four mechanisms exist because free data lies and self-grading demands trust, and all four are built before any signal ships: (a) the unified `events` stream — one table all modules emit into and all consumers read from; (b) bar validation, corporate-action detection, delisting handling, and exchange calendars per §6.12; (c) `config_version` stamped on every event so scorecards segment across threshold changes instead of silently blending them; (d) the runtime numeral validator that falls back to the template brief when the LLM states any number absent from its context. An implementing agent that defers any of these to "later" is building the untrustworthy version of the product.
26. **Point-in-time correctness and lineage, right-sized (adopted from external review of v3.0).** Feeds that revise or lag (macro, disclosures) carry `available_at`/`revision` provenance, and every backtest filters on availability, not effective date — look-ahead through revised CPI vintages or final-consensus estimates is silent and fatal to credibility. Prices store raw and adjusted variants with per-signal basis declarations (§6.12); FX is typed base/quote with one conversion function (§6.10); every calculation and event stamps its snapshot, signal version, config version, and code SHA; events deduplicate on rich natural keys and group into episodes; delivery runs through a transactional outbox; the dead-man monitor lives outside the monitored host. Deliberately NOT adopted at this scale: bitemporal modeling of every table, immutable raw-payload archives for high-volume bar feeds (hash-only there; full raw retention for low-volume feeds), and separate universe/provider/calendar version stamps (folded into the config hash). Lineage exists where look-ahead and trust actually break — not as ceremony.


---

## 3. Phased Roadmap

**Phase 1 — MVP (ship end-to-end, fully automated, $0/month in Zero-Cost Mode):**
1. Market Overview: index tiles, sector heatmap, breadth panel, Regime Badge, VIX, put/call, yields, DXY
2. Sector Rotation Engine (12-ETF universe + benchmark)
3. Economy & Credit dashboard (FRED: rates, mortgage, credit spreads, housing, inflation, delinquencies)
4. Smart Money: congressional disclosures + corporate insider Form 4 buys, cluster detection
5. Cross-Listing Premium Monitor (config-defined ADR/home-listing pairs, FX-adjusted, e.g. SKHY vs 000660.KS)
6. Daily Brief — template-composed by default (free); LLM-composed when `ENABLE_AI=true` (cached, auditable)
7. Alert engine v1 (in-app + Discord webhook — both free)

**Phase 2 — three waves, in order. Wave gates: do not start a wave until the prior wave's modules pass their tests AND have run live for two weeks without manual intervention.**

*Wave 2a — validate and build the moat (highest value per effort):*
8. ~~Websockets (small; everything after benefits)~~ — **corrected v4.9.9: build Websockets LAST in Wave 2a, after the simulator.** §16 governs the sequence and places delivery polish after research correctness; this line previously read as an instruction to build streaming first, which is exactly backwards — streaming is a transport for events that do not yet exist. Item numbering retained so cross-references stay valid; build order is §16's.
9. Themes Engine (theme baskets + ThemeScore + theme pages) — prerequisite for half of 2b
10. Pair Watch — relative-value ratio monitor (§7.8)
11. Backtest & Strategy Simulator (§7.16) — required early: the §18 budget rule and all signal-validation gates depend on it
12. Signal Outcome Tracking (§7.15) — the moat; starts accumulating history from the moment it ships, so every week of delay is unrecoverable

*Wave 2b — analytical depth:*
13. Supply-Bottleneck Detector (§7.10)
14. Catalyst Sympathy Scanner (§7.9)
15. *(promoted to M5.5 — see §16)* Swing Desk — scanner + setup geometry + risk arithmetic + idea-source tagging (§7.11)
16. Earnings Intelligence — calendar, surprises, reaction monitor, EDGAR debrief (§7.13)
17. Executive-branch (OGE) disclosure feed with high-volume filer rule and chart markers (§7.3)
17b. Risk Lens — scenario replay + signal crowding detection (§7.15)

*Wave 2c — breadth (each individually deferrable; cut first under time pressure):*
18. Expectations module (Polymarket probabilities)
19. Crypto Flows (BTC/ETH/SOL)
20. Ask-the-Market chat (streaming LLM Q&A)

**Phase 3 (all optional, flag-gated):**
20. Options intelligence (unusual activity, IV metrics)
21. Market Graph (correlation-based cross-asset relationship view)
22. Regime Analogs ("current conditions resemble Aug 2019") via time-series similarity
23. ML Challenger Harness — Kronos-class foundation-model evaluation via the simulator's challenger interface (§7.17)
24. Ticker Dossier — per-symbol evidence page (§7.17)
25. Decision Journal — user decisions logged with auto-attached context snapshots, outcome-scored (§7.17)
26. Deal Spread Watch (manual-entry merger spreads, §7.17)
27. Sports odds module
28. Multi-user auth + personal portfolio exposure analysis

**MVP definition of done:** at 9:00 AM ET a user sees, with zero manual steps and zero recurring cost: sector heatmap + breadth, ranked rotation table with ≥1 plain-English rotation statement when thresholds are met, economy dashboard with fresh FRED data, the 20 latest congressional + insider disclosures with lags shown, the premium panel for every configured listing pair with FX-adjusted premium and 1-line persistence context, and a cached daily brief (template or LLM) whose every number traces to its stored context JSON.

---

## 4. System Architecture

```
                        ┌──────────────────────────────┐
                        │   Frontend (Next.js, React)  │
                        │  SSR pages + WS live updates │
                        └──────────────┬───────────────┘
                                       │ HTTPS / WSS
                        ┌──────────────▼───────────────┐
                        │      API Gateway (FastAPI)   │
                        │  REST /api/v1/*  +  /ws feed │
                        └───────┬──────────────┬───────┘
                                │              │
                 ┌──────────────▼──┐      ┌────▼─────────────┐
                 │  PostgreSQL +   │      │  Redis            │
                 │  TimescaleDB    │      │  cache · pub/sub  │
                 │  (all state)    │      │  · rate limiting  │
                 └──────────────▲──┘      └────▲─────────────┘
                                │              │
        ┌───────────────────────┴──────────────┴───────────────────┐
        │              Worker layer (Celery + Beat)                │
        │ ingest_bars  ingest_foreign_bars  ingest_fx              │
        │ ingest_macro(FRED)  ingest_congress  ingest_insider      │
        │ ingest_predictions  ingest_crypto                        │
        │ compute_rotation  compute_breadth  compute_premiums      │
        │ compute_themes  compute_scanner  compute_alerts          │
        │ generate_daily_brief (template or LLM)                   │
        └───────────────────────┬──────────────────────────────────┘
                                │ outbound HTTPS
        ┌───────────────────────▼──────────────────────────────────┐
        │ External providers (Zero-Cost defaults, §6.0):           │
        │ yfinance/Stooq · FRED · Frankfurter(FX) · SEC EDGAR ·    │
        │ Stock Watcher · Polymarket · CoinGecko/Coinalyze · CBOE ·│
        │ Discord webhooks · Anthropic (optional, flag-gated)      │
        └──────────────────────────────────────────────────────────┘
```

**Repository layout (monorepo):**

```
flowdesk/
├── docker-compose.yml
├── .env.example
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI app factory
│   │   ├── config.py               # pydantic-settings; ALL tunables & universes live here
│   │   ├── api/                    # routers: market, rotation, economy, smartmoney,
│   │   │                           #          themes, expectations, scanner, crypto,
│   │   │                           #          earnings, ai, alerts, health
│   │   ├── models/                 # SQLAlchemy models (§8)
│   │   ├── schemas/                # Pydantic response schemas
│   │   ├── services/               # orchestration/business logic, one file per module
│   │   ├── ingest/                 # provider clients + normalizers (common interfaces)
│   │   ├── signals/                # PURE functions only: rotation.py, breadth.py,
│   │   │                           # premiums.py, themes.py, scanner.py,
│   │   │                           # crypto_stance.py, regime.py
│   │   ├── ai/                     # context_builder.py, prompts.py, client.py
│   │   └── ws.py                   # websocket manager, Redis pub/sub bridge
│   ├── workers/
│   │   ├── celery_app.py
│   │   └── tasks/                  # one file per scheduled job (§11)
│   ├── alembic/                    # migrations
│   ├── tests/
│   └── backtest/                   # replay harness (§7.16)
└── frontend/
    ├── app/                        # Next.js App Router pages (§10)
    ├── components/
    ├── lib/api.ts                  # typed API client
    └── lib/ws.ts                   # websocket hook
```

**Architectural rules (binding):**
- `signals/` contains **pure functions only** (arrays/dataframes in, dataclasses out; no I/O, no DB, no network). This makes unit tests and the backtest harness trivial, and gives FlowDesk **research–live parity**: the exact code that computed a signal in a backtest computes it in production, so evaluation results transfer without reimplementation drift.
- Every provider client implements a shared interface and is selected via env config, so providers are swappable without touching services.
- The LLM is called only from worker tasks and the one interactive `/ai/ask` endpoint; never in dashboard render paths.
- Everything the UI shows comes from Postgres/Redis. If every external API dies, the app serves last-known state with stale badges — never 500s.
- **One event stream.** Every module emits events into the single `events` table (§8) — wherever a module spec says "emit `<kind>_event`," it means an insert there with that kind. Alerts, outcome tracking, websockets, and scorecards all consume this one stream; no module gets a private event table.
- Themes, universes, weights, thresholds, and FRED series lists live in `config.py` (env-overridable), never hardcoded in logic.

---

## 5. Tech Stack (final)

| Layer | Choice | Notes |
|---|---|---|
| Frontend | Next.js 14+ (App Router), TypeScript, Tailwind, shadcn/ui, TradingView Lightweight Charts, Recharts | Lightweight Charts for price/RS series; Recharts for heatmaps/bars |
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0 async, Uvicorn | |
| DB | PostgreSQL 16 + TimescaleDB | Hypertables for all time series |
| Cache/bus | Redis 7 | Response cache, WS fan-out, Celery broker, rate limits |
| Jobs | Celery + Beat | Retries, locks, visibility |
| AI | Optional (`ENABLE_AI`, default false): Anthropic API, small/cheap model class (Haiku-tier) | Template-composed brief is the free default; LLM brief + Q&A when enabled |
| Notifications | In-app + Discord webhook (free); email (Resend/SES) only as an opt-in paid upgrade | |
| Deploy | Docker Compose on the developer's own machine or a free-tier cloud VM (e.g. Oracle Cloud Always Free ARM) | K8s/Kafka explicitly out of scope; paid VPS is an optional upgrade |
| Observability | structlog JSON, Sentry, /healthz per service | |

---

## 6. Data Sources & Ingestion

Provider clients live in `ingest/`, one file each, behind common interfaces. Verify pricing/terms at build time; preserve the interfaces here and adapt client internals if provider APIs changed.

### 6.0 Zero-Cost Mode (default: `ZERO_COST_MODE=true`)
FlowDesk must run end-to-end at **$0/month**. This mode makes three trade-offs, all acceptable for this product's signal cadence:
1. **Data latency drops from ~real-time to 15-minute/daily cadence.** Every FlowDesk signal (rotation, breadth, premiums, credit, smart money) is a swing/positioning signal, not an execution signal — 15-minute snapshots lose nothing analytically.
2. **The primary equity source is unofficial (yfinance).** It can break without notice; the provider-interface design (§4) exists precisely so a paid provider can be swapped in later with one env var. Every ingest failure degrades to stale badges, never crashes (§6.11).
3. **The LLM is optional.** `ENABLE_AI=false` produces the daily brief entirely from the deterministic template layer (every module already generates template sentences); `ENABLE_AI=true` upgrades composition to an LLM at roughly cents/day with the hard budget stop in §14.

Zero-cost provider map (all free): equities/ETFs/foreign listings → **yfinance** (Stooq CSV as daily fallback); FX → **Frankfurter** (ECB rates, no key); macro/credit → **FRED** (free key); congress → **Senate/House Stock Watcher** GitHub dumps; insiders → **SEC EDGAR** Form 4; put/call → **CBOE** published stats; prediction markets → **Polymarket** public API; crypto → **CoinGecko + Coinalyze** free tiers; alerts → **Discord webhook**; hosting → own machine or **Oracle Cloud Always Free**; TLS → Caddy + Let's Encrypt. Paid upgrades (Polygon/Alpaca, FMP, email, VPS) slot behind the same interfaces and are never required.

### 6.1 Equities & ETFs (prices/volume)
- **Zero-cost primary:** yfinance — 15-minute-cadence quote/bar polling during market hours for the ETF universe (scanner universe in P2 uses the same cadence), plus 2 years of daily bars for all tracked symbols (rotation lookbacks, breadth, backtests) and reference data (market cap, shares outstanding where exposed). Respect informal limits: batch tickers per request, ≥1 s between batches, exponential backoff on failures.
- **Daily fallback:** Stooq free CSV endpoint (daily bars only) if yfinance breaks.
- **Paid upgrade path (optional, off by default):** Polygon.io Starter or Alpaca Market Data for true 1-min bars; identical client interface.

### 6.2 Universes (defined in `config.py`)
- **ROTATION_UNIVERSE:** `XLK, SMH, IGV, XLF, XLE, XLI, XLV, XLY, XLP, XLU, XLB, XLRE, IWM, ARKK` · **BENCHMARK:** `SPY` · style extras: `QQQ, RSP` (RSP needed for breadth §7.1).
- **COMMODITY_PROXIES:** `USO (oil), UNG (nat gas), GLD (gold), SLV (silver), CPER (copper), URA (uranium), DBA (agriculture)` — commodity exposure via ETFs keeps everything on one equity data feed. Label them as ETF proxies in the UI.
- **BREADTH_UNIVERSE:** S&P 500 constituents, daily bars only (for % above 50DMA and new highs/lows). If constituent-level data exceeds provider tier, use documented fallback in §7.1.

### 6.3 CBOE volatility-structure series (put/call, term structure, skew, correlation)
Daily total put/call ratio from CBOE's free published stats (CSV/page). Ingested once daily; one number per day into `macro_metrics(metric='cboe_total_pcr')`.

**Volatility structure (added v4.9.7).** The same daily CBOE ingest path also pulls, each as a `macro_metrics` row:
- **Term structure:** `VIX9D`, `VIX`, `VIX3M`, `VIX6M`, plus the computed `vix_term_ratio = VIX9D / VIX3M`. A ratio > 1 (backwardation — near-term fear priced above longer-dated) is a stress marker that a spot VIX *level* cannot express: VIX can sit at 15 in calm contango or 15 in early backwardation, and only the structure distinguishes them.
- **`SKEW`:** the price of crash protection. Rising SKEW alongside a calm VIX is a specific, nameable divergence (tail hedging without spot fear) and is one of the §7.14 tension-detector pairs.
- **Implied correlation:** `COR1M`/`COR3M`, with its own 1-year percentile.
All are daily, free, and revision-free; ingestion failures degrade only the affected tiles.

### 6.4 Macro & credit — FRED (free official API, key required). Pinned series IDs:

| Metric | FRED series | Frequency |
|---|---|---|
| 10Y Treasury yield | `DGS10` | daily |
| 2Y Treasury yield | `DGS2` | daily |
| 10Y–2Y spread | `T10Y2Y` | daily |
| 30Y mortgage rate | `MORTGAGE30US` | weekly |
| High-yield credit spread (OAS) | `BAMLH0A0HYM2` | daily |
| Investment-grade spread (OAS) | `BAMLC0A0CM` | daily |
| CPI (index) | `CPIAUCSL` | monthly |
| Unemployment rate | `UNRATE` | monthly |
| Housing starts | `HOUST` | monthly |
| Case-Shiller home price index | `CSUSHPINSA` | monthly |
| Credit-card delinquency rate | `DRCCLACBS` | quarterly |
| VIX (close) | `VIXCLS` | daily |
| Trade-weighted dollar index | `DTWEXBGS` | daily |
| 10-year real yield (TIPS) | `DFII10` | daily |
| 10-year breakeven inflation | `T10YIE` | daily |
| Secured Overnight Financing Rate | `SOFR` | daily |
| Interest on reserve balances | `IORB` | daily |

**VERIFIED (M0 spike 3, 2026-07-31) — point-in-time retrieval works, and the availability filter is non-optional.** Tested against `GDPC1`:
- **Values are revised.** Q1-2025 real GDP read 23,526.085 to an observer standing on 2025-05-01; today the same period reads 23,548.210 (+0.094%). Storing only the latest value silently rewrites history.
- **The observation SET changes too, which is the larger trap.** On 2025-05-01 exactly one 2025 quarter existed; today four do. A backtest querying today's store while simulating that date would see three quarters that had not been published — look-ahead that filtering on `effective_at` alone cannot catch. This is the concrete justification for decision 26's `available_at` filter, and it applies to the *existence* of a row as much as its value.
- **Mechanism:** `realtime_start`/`realtime_end` on `series/observations` reproduce the world as known on any past date; `output_type=4` returns each observation's first published value with `realtime_start` = its publication date (this is the `available_at` column). **Both require a widened real-time window** (e.g. `realtime_start=1776-07-04&realtime_end=9999-12-31`); leaving the default (today→today) returns HTTP 400 "No vintage dates exist for the specified real-time period" — a wrong-parameter error that reads like a data-availability error, so ingestion must not treat it as "no data."
- **Failures are loud** — an unknown series returns HTTP 400 with an explicit message, in welcome contrast to the provider behaviour recorded in §6.12.
- **Daily series carry their own lag:** `DGS10` reported `last_updated` 2026-07-31 against `observation_end` 2026-07-30, so even daily market series are a session behind and must be treated as such by the freshness thresholds in §6.11.
**Measured revision magnitudes and publication lags (spike 3b, same date) — four consequences for ingestion:**
| Series | Publication lag (days) | Revised? | Revision magnitude |
|---|---|---|---|
| `GDPC1` (real GDP) | 119–175 | 4 of 4 | up to ±0.4% |
| `CPIAUCSL` (CPI) | 39–78 | 11 of 12 | ~±0.04% |
| `PAYEMS` (payrolls) | 31–80 | 12 of 12 | **−801k to −1,246k jobs (−0.5% to −0.8%)** |
1. **Revision is the norm, not the exception** — 27 of 28 observations changed after first publication, so `available_at` filtering plus vintage retention is mandatory rather than defensive. Payrolls are the extreme case: every month was revised downward by roughly a million jobs, which is large enough to invert the sign of a "labour market strengthening" reading computed from stored latest values.
2. **Publication lag is not a constant and must never be hardcoded.** Within a single series it varied more than twofold (`PAYEMS` 31 → 80 days; `CPIAUCSL` 39 → 78), so freshness thresholds (§6.11) are per-series *and* tolerant of irregular publication; a late release must not be mistaken for a dead feed. Provider-health checks therefore compare against each series' own published cadence, not a global staleness constant.
3. **Missing observations are returned as the string `"."`, not null or an error** (`CPIAUCSL` 2025-10-01). Parsers must treat `"."` as a genuine gap — never coerce it to 0.0, never let it crash a series load, and never forward-fill it silently into a signal.
4. **`GDPC1`'s 119–175-day lag means quarterly macro is a slow-moving context input only** — any signal reacting to GDP within a quarter of the period it describes is reacting to information that did not exist.
Raw evidence retained at `research/spikes/fred-vintages/`.

All land in `macro_metrics(metric, ts, value)`. The Economy dashboard (§7.4) renders from these plus computed deltas. Adding a macro tile = adding one row to the config list; no code changes.

**Real-rate and funding decomposition (added v4.9.7).** Three computed series follow from the rows above and belong in the §7.4 Rates group: `real_yield_10y` (`DFII10`) and `breakeven_10y` (`T10YIE`) decompose any nominal 10-year move into growth-vs-inflation components — the distinction that determines whether a rate move pressures long-duration equities or lifts gold; and `sofr_iorb_spread` (`SOFR` − `IORB`) is a funding-stress indicator that can move before credit spreads in a plumbing squeeze. Template line, condition-driven like the rest of §7.4: "The 10-year rose 18 bps, entirely in real yields with breakevens flat — a growth/policy move, not an inflation move."

### 6.5 Smart Money
- **Congressional disclosures:** Financial Modeling Prep senate/house endpoints or Quiver Quantitative; free fallback = Senate/House Stock Watcher JSON dumps (GitHub, poll daily). Amounts are ranges — store `amount_min/amount_max`; never fabricate point estimates.
- **Executive-branch officials (President, VP, cabinet, senior officials):** OGE periodic transaction reports (PTRs) + annual OGE Form 278e. Free sources: the OGE eFiling portal and community aggregators (Open-Cabinet-style JSON). Data caveats an agent must encode, not smooth over: values are broad ranges only; filings often omit security type (stock vs bond) and account; many are filed past the 30-day STOCK Act window — store and display `lag_days` and a `filed_late` flag; assets typically sit in trusts managed by third parties, so trades may reflect a manager's model, not the official's view (see the high-volume rule in §7.3).
**VERIFIED (M0 spike 4, 2026-07-31) — EDGAR access confirmed, with four implementation constraints.** Tested against NVDA (CIK 0001045810):
- **A contact User-Agent is mandatory, not advisory.** An unidentified request returns HTTP 403; the same request with `User-Agent: <app> <email>` returns 200. This is therefore a required config value (`SEC_USER_AGENT` in `.env`, a real address), and a missing/placeholder value must fail loudly at startup rather than surfacing as a mystery 403 during ingestion.
- **Identity is CIK, never ticker.** `company_tickers.json` (10,432 rows) maps ticker → CIK. Tickers are reassigned between companies over time, so `symbols` stores CIK as the durable key and treats ticker as a mutable label.
- **The `recent` submissions batch is capped (~1,000 filings) and is dominated by insider forms** — NVDA's batch held 566 Form 4s and 243 Form 144s against only 61 8-Ks, so for a heavily-traded issuer the batch covers barely a year. Historical ingestion MUST page through the `files` array of the submissions JSON; reading only `recent` silently truncates history for exactly the largest, most-watched companies.
- **Disclosure lag is directly computable from the index:** `reportDate` is the period covered and `filingDate`/`acceptanceDateTime` (UTC, to the second) is when it became public. Measured: Form 4 lags of 4–5 days, 8-K lags of 3–6 days. `acceptanceDateTime` is the `available_at` value for §7.3 and for any backtest entry rule.
- **Form 4 transaction dates require document parsing.** The index gives filing dates only; the actual transaction date, security, size, and open-market-vs-plan flag live inside the referenced XML (`xslF345X06/wk-form4_*.xml`). §7.3's transaction-vs-disclosure display and the high-volume filer rule therefore depend on an XML parsing step, not an index read — scope this into M10 explicitly.
- **Rate limiting:** 12 rapid serial requests all returned 200 with no throttling, consistent with the documented ~10 req/sec ceiling. Ingestion still throttles deliberately (the test was serial and slower than the cap); a 403 under load is the documented failure mode.
Raw evidence retained at `research/spikes/edgar/`.

- **Corporate insider (Form 4):** FMP insider-trading endpoint, or parse SEC EDGAR Form 4 filings directly (free, official). v1 tracks **open-market purchases and sales by officers/directors**; ignore option exercises and 10b5-1 tagged plans when the source flags them.

### 6.6 Prediction markets (Phase 2)
Polymarket public API (free). Track a config-listed set of market slugs: Fed rate decision by next meeting, recession-by-year, election markets, selected geopolitical markets. Store daily (and 6-hourly) probability snapshots. Optionally Kalshi as a second source behind the same interface.

### 6.7 Crypto (Phase 2)
CoinGecko (prices, free tier); Coinalyze (funding, OI, liquidations, free tier) or CoinGlass (paid). Spot ETF daily net flows from issuer/aggregator pages — ingest once daily; an authenticated manual-override admin endpoint is acceptable given source fragility.

### 6.8 Earnings (Phase 2)
FMP earnings calendar + surprises endpoints: report dates, EPS/revenue estimate vs actual for a config universe (S&P 500 + scanner universe).

### 6.9 Sports odds (Phase 3 only)
The Odds API. Line snapshots, movement, closing-line value. Behind `ENABLE_SPORTS`.

### 6.10 FX rates & foreign listings (Premium Monitor)
- **Foreign listing prices:** yfinance covers major foreign exchanges with suffixed tickers (KRX: `000660.KS`; Tokyo: `.T`; Hong Kong: `.HK`; Taiwan: `.TW`; London: `.L`). Poll each configured pair's foreign leg at 15-min cadence during that exchange's hours and store its official daily close. Note: for most pairs the two markets' sessions **do not overlap** (KRX closes before NYSE opens) — the premium is computed against the foreign leg's latest close plus current FX, and the UI must label it "vs latest Seoul close," never imply simultaneous prices.
- **FX rates:** Frankfurter API (ECB reference rates, free, no key) for daily rates; yfinance FX tickers for intraday. **Never stored as ambiguously named scalars.** Rates live in a typed `fx_rates` table (base_currency, quote_currency, rate, observed_at, source), and all conversion goes through one function — `convert(amount, from_currency, to_currency, observed_at)` — so direction can never be guessed. The premium engine receives `usd_per_foreign_currency` by name; a bare `fx_rate` variable is forbidden in that code path (an inverted KRW/USD rate produces a syntactically valid, catastrophically wrong premium — this is the doc's canonical example of why typing beats convention).
  - **VERIFIED (M0 spike 1, 2026-07-31):** yfinance ticker `KRW=X` returns **KRW per USD** (observed 1428.60), so `usd_per_krw = 1 / rate`. Any provider rate whose magnitude is > 1 for a USD-quoted pair of this type must be inverted; ingestion asserts the expected magnitude range per pair and quarantines out-of-range values rather than converting them. Raw evidence retained at `research/spikes/foreign-listing-fx/`.
- **Listing pairs** are config-defined (`LISTING_PAIRS` in `config.py`), each with: US ticker, foreign ticker, foreign currency, and **ADR ratio** (ordinary shares per ADR — e.g. SKHY = 0.1, i.e. 10 ADRs = 1 Korean share). Initial set: `SKHY/000660.KS (ratio 0.1, KRW)`, `TSM/2330.TW (ratio 5, TWD)`, `SONY/6758.T (ratio 1, JPY)`, `BABA/9988.HK (ratio 8, HKD)`. Verify each ratio from the depositary bank's documentation at build time — a wrong ratio silently corrupts the whole signal.

### 6.11 Ingestion rules (all providers)
- Typed responses; tenacity retries with exponential backoff; per-provider Redis rate limiter; raw payload logged at DEBUG.
- **Idempotent upserts** on natural keys (symbol+interval+ts; source+external_id). Re-running any job is always safe.
- Every job updates `providers_health(provider, feed, last_success_at, last_error_at)`. API responses carry `stale: true` when `now − last_success > threshold` (per-feed thresholds in config); UI shows red stale badges.

### 6.12 Data Quality, Corporate Actions & Rate Budgets (hardening layer — Phase 1)
Free data doesn't just fail loudly; it lies quietly. This layer is mandatory and built in step 2, before any signal code.

**Bar validation (every ingest):** reject/quarantine bars failing sanity rules — high ≥ max(open, close), low ≤ min(open, close), non-negative volume, |day move| > 40% without a matching corporate action, timestamps aligned to the exchange calendar. Quarantined rows land in `data_quarantine` with reason; quarantine counts feed providers_health and the ops heartbeat.

**Corporate actions & price bases:** "adjusted everywhere" is wrong — different calculations require different bases, so bars store raw and adjusted variants side by side (see §8) and each signal declares its basis: momentum/long-horizon returns → split-adjusted (total-return where dividends matter); intraday moves and gaps → raw tradable prices; **cross-listing premiums → raw prices at their observed timestamps, never adjusted**; dollar volume → raw price × consistently-adjusted volume; dilution → split-normalized shares outstanding. **VERIFIED (M0 spike 2, 2026-07-31) — what yfinance actually returns, and the correction it forces.** Tested against NVDA's 10:1 split (2024-06-10) and KO dividends:
- `auto_adjust=False` → `Close` is **split-adjusted, not raw** (NVDA 2024-06-07 returned 120.888 against ~1,208.88 as actually traded — exactly 1/10). `Adj Close` is split **and** dividend adjusted.
- `auto_adjust=True` → OHLC silently becomes the total-return series and the `Adj Close` column disappears (True-mode `Close` 122.2285 == False-mode `Adj Close` 122.2285, identical).
- **Therefore true as-traded prices are NOT directly obtainable from this provider's history.** Column mapping for §8: `c_split_adj` ← False-mode `Close`; `c_total_return` ← False-mode `Adj Close`; `c_raw` ← **reconstructed** by multiplying `c_split_adj` by the cumulative split factor from the `Stock Splits` column for all bars before each ex-date, or taken directly from bars captured live at the time (a live-ingested bar IS as-traded on its own date). Backfilled history spanning a split is split-adjusted until reconstructed — a premium recomputed from such bars against an effective-dated ADR ratio would be wrong by the split factor. Ingestion must always request `auto_adjust=False` explicitly; relying on the default silently yields total-return prices everywhere.
- **Total-return series are not stable across re-fetches.** `Adj Close` is indexed to the present, so every future dividend rewrites all earlier values (KO 2025-03-14: Close 69.16 vs Adj Close 66.7805 — a $2.38 gap that is ~5 quarters of accumulated dividends, not the $0.51 dividend of that date). Consequence for M2's idempotency gate: **idempotency is defined on `c_raw`/`c_split_adj` only**; `c_total_return` is revision-prone and versioned like a FRED vintage (§6.4), never treated as a fixed historical fact.
- **Column sets vary by security type** — SPY returned an extra `Capital Gains` column that NVDA and KO did not. The normalizer must map columns by name defensively and reject unexpected schemas rather than positional/fixed-set assumptions.
- **Failure modes are silent.** An unknown ticker returns an **empty frame without raising** (a 404 is printed to the console only), and a delisted symbol is indistinguishable from a typo by the return value. Ingestion must therefore treat an empty frame as a **failure requiring provider-health degradation**, never as "no new data," and symbol status must be tracked explicitly (§6.12 delistings) rather than inferred.
- **Holidays confirmed absent** — the 2025-07-04 row simply does not exist in the SPY window, confirming that gaps are implicit and validating the exchange-calendar requirement below.
Raw evidence retained at `research/spikes/yfinance/`.

The split detector (close ratio ≈ simple fraction + volume jump) only *flags* a suspected action into quarantine; canonical history is rewritten solely on a **confirmed** entry in the effective-dated `corporate_actions` table (raw history is immutable — corrections create new versions). Splits interact with two modules critically: the §7.11 dilution flag MUST use split-normalized share counts (a 2:1 split is not 100% dilution), and §7.5 premium math MUST re-verify the ADR ratio after any corporate action on either leg.

**Delistings & survivorship:** when a symbol stops trading, mark `symbols.active=false` with a delist date — never delete history. Simulator reports MUST print a survivorship warning specifically for the scanner and bottleneck templates: free-data history under-represents delisted small caps, so those backtests are upper bounds; the honest counterweight is §7.15 outcome tracking, which is survivorship-free going forward by construction.

**Trading calendars:** all "sessions" math uses a proper exchange-calendar library (exchange_calendars-class) per venue — NYSE for US, XKRX for the premium monitor's Korean leg, etc. Naive weekday math is forbidden; holidays and half-days otherwise corrupt every windowed statistic several times a year.

**Rate budget (zero-cost reality check):** yfinance sustains roughly batched requests of ~100 tickers with ≥1s spacing. Budget table: ETF/rotation universe (~20 symbols) at 15-min — trivial; scanner universe (~500) at 15-min — 5 batches/cycle, fine; S&P 500 constituent bars — **daily only**. Consequently, intraday breadth is computed from the 11 sector ETFs + RSP/SPY (already specified as the §7.1 fallback), and full constituent breadth refreshes once daily after the close. Any job exceeding its budget must degrade cadence, not hammer the source.

---

## 7. Module Specifications

### 7.1 Market Overview & Breadth
**Tiles:** SPY/QQQ/IWM/DIA snapshots; sector heatmap (day % change per sector ETF); VIX; 10Y & 2Y + T10Y2Y spread; DXY; CBOE put/call; FlowDesk Sentiment composite; Breadth panel.

**Breadth signals** (pure functions in `signals/breadth.py`):
- `pct_above_50dma` and `pct_above_200dma` over BREADTH_UNIVERSE daily closes.
- `new_highs_minus_new_lows` (63-session highs/lows count).
- **Equal-weight ratio:** `RSP_close / SPY_close`, 21-day slope → "rally is broadening/narrowing."
- Divergence detector: if SPY day-change > +0.5% AND pct_above_50dma < 40% → breadth_warning event: *"S&P 500 is up but only {x}% of constituents are above their 50-day average — participation is narrow."*
- **Cadence (per the §6.12 rate budget):** full constituent breadth computes once daily from daily bars; intraday refreshes use sector-level breadth (11 sector ETFs + RSP/SPY ratio), labeled as such. If constituent bars are entirely unavailable, sector-level breadth is the full-time fallback. The API shape is identical in all cases.

**Market Regime Badge** (`signals/regime.py`, deterministic — this is the "market check before buying" gate from discretionary playbooks, formalized): classify each session as **Risk-On / Neutral / Risk-Off** from rules over signals FlowDesk already computes: Risk-On requires pct_above_50dma > 55% AND VIX below its 1-year 60th percentile AND HY OAS 21-day delta < +25 bps AND ≥ 6 of 11 sector ETFs positive over 21 sessions; Risk-Off = the mirror conditions; else Neutral (exact thresholds in config). Regime is displayed as a Command Center badge with its component checklist on hover, stored per session, included in the brief context, and emits a `regime_change` event when the label flips. FlowDesk states the regime; it never tells the user to be "aggressive" or "defensive" — the checklist speaks for itself.

**FlowDesk Sentiment composite ∈ [0,100]** (replaces scraping CNN Fear & Greed; deterministic and defensible): equal-weighted average of five percentile ranks over trailing 1-year: (a) inverted VIX percentile, (b) breadth pct_above_50dma percentile, (c) inverted put/call percentile, (d) SPY 21-day momentum percentile, (e) inverted HY-spread percentile. Show components on hover.

API: `GET /market/overview` (one cached JSON, TTL 60s market hours / 15 min off), `GET /market/breadth/history`.

### 7.2 Sector Rotation Engine (flagship)
Pure functions in `signals/rotation.py` over daily bars (optionally 30-min intraday refresh).

For each ETF `s` vs `SPY`:
- **RS ratio:** `RS(s,t) = close(s,t) / close(SPY,t)`.
- **RS momentum:** slope of OLS linear regression on `log(RS)` over windows W ∈ {5, 10, 21, 63} sessions; z-score each slope against its own trailing 252-session slope distribution.
- **Volume signal:** z-score of (10-day avg dollar volume ÷ 63-day avg dollar volume).
- **Flow proxy:** z-score of 5-day % change in ETF shares outstanding (creation/redemption), when available; else weight redistributed.
- **RotationScore ∈ [−100, +100]:** `100 · tanh( 0.40·z_mom10 + 0.25·z_mom21 + 0.20·z_vol + 0.15·z_flow )` — weights in config.

**Pairwise rotation events:** for each ordered pair (loser, winner): 10-session relative performance spread = `(winner_10d_return − loser_10d_return)`. Emit `rotation_event` when spread > 3.0% AND winner score > +20 AND loser score < −20 (all thresholds config). Sentence is **template-generated Python** (deterministic, tested), e.g.:
> "Capital rotation detected: out of Semiconductors (SMH, −34) into Software (IGV, +41). IGV has outperformed SMH by 4.2% over 10 sessions on above-average volume."

Reference pseudocode in §17.2. API: `GET /rotation/scores`, `GET /rotation/events?days=30`, `GET /rotation/pair?a=IGV&b=SMH`.

### 7.3 Smart Money (Congress + Corporate Insiders)
One page, three feeds (Congress, Executive Branch, Corporate Insiders), shared cluster logic. Executive filers live in the same `politicians` table with `chamber='executive'` and the same trade tables (`source='oge'`).

**High-volume filer rule (mandatory):** any filer exceeding a config threshold (default 200 trades/quarter) is auto-tagged `managed/systematic` and their *individual* trades are excluded from cluster detection and per-trade alerts — at that volume the trades are a manager's model (direct indexing, tax harvesting), not decisions, and per-trade copying signals are noise. For these filers FlowDesk shows aggregate views only: net buy/sell flow by sector per quarter, largest net position changes, and the late-filing rate. Low-volume filers keep full per-trade treatment.

**Chart markers:** on any ticker's price chart (smart-money pages and the Phase 3 Ticker Dossier), disclosed trades render as paired markers — a hollow marker at `transaction_date` and a solid marker at `disclosure_date`, connected. The horizontal gap between the pair IS the lag, made visible: the user sees exactly how stale the information was when it became public, and can judge copy-trading honestly by looking only at solid markers.

- **Congress feed:** politician, party, chamber, ticker, side, amount range, transaction_date, disclosure_date, `lag_days` (always displayed). Estimated performance from range midpoint, labeled "est."
- **Insider feed (Form 4):** insider name, role (CEO/CFO/Director/Officer), ticker, side, shares, value, transaction_date, filing_date, lag shown. v1 highlights **open-market buys** (strongest signal).
- **Cluster detection (`signals/smartmoney.py`):**
  - `congress_cluster`: ≥3 distinct politicians, same GICS sector, same side, 30-day window.
  - `insider_cluster`: ≥3 distinct insiders buying the same ticker, 30-day window ("Five executives bought shares in the last 30 days").
  - `conviction_overlap`: ticker appears in BOTH congress buys and insider buys within 45 days → highest-priority event.
- Aggregations: most-traded tickers 30/90d, sector exposure rollups.

API: `GET /smartmoney/congress`, `/smartmoney/insiders`, `/smartmoney/clusters`, `/smartmoney/tickers/top`, `/smartmoney/politicians/{id}`.

### 7.3.1 Futures Positioning — CFTC Commitments of Traders (Phase 2b, added v4.9.7)
The only free, official dataset that **reports** positioning rather than inferring it from price and volume. Every other FlowDesk module answers "where is capital moving?" by proxy; COT answers it directly for futures markets.

**Source & cadence.** CFTC Commitments of Traders, published weekly (Friday afternoon, reflecting the prior **Tuesday's** positions), free, no key. Legacy and disaggregated report formats. Config-listed contracts: gold, silver, copper, crude, natural gas, USD index, major FX pairs, and equity-index futures. The Tuesday-data/Friday-release pattern is a textbook `available_at` case (§6.4) — the publication lag is fixed and known, so both dates are stored and displayed, and any backtest enters no earlier than the Friday release.

**Signals** (`signals/positioning.py`, pure):
- Net position by trader category (commercial / non-commercial / non-reportable) per contract.
- **Positioning z-score:** net position of each category against its own trailing 3-year distribution — the level is meaningless without that context, since contract open interest grows over time.
- Week-over-week change in net positioning, and the 13-week trend.
- **Divergence flag:** commercials and large speculators moving in opposite directions beyond a config threshold (`positioning_divergence` event, one per contract per week).

Template sentence (mandatory qualifier, grep-enforced as in §7.5):
> "Gold: large speculators are net long at the 91st percentile of their 3-year range, up 12% week-over-week, while commercials extended net short. Positioning is reported as of Tuesday and published Friday — it describes where participants stood, not where price goes, and crowded positioning can persist or extend for months."

**Mandatory caveats displayed on the panel:** (a) data is as of Tuesday, published Friday — never presented as current; (b) "commercial" includes swap dealers hedging index and ETF exposure, so the hedger-versus-speculator framing is looser than the category names imply; (c) COT extremes have a long history of being cited as reversal signals with weak out-of-sample support — this enters as positioning *context* with a §7.16 scorecard from day one, like every other signal.

API: `GET /positioning`, `GET /positioning/{contract}/history?weeks=156`. UI: per-contract panel with the three category series, percentile bands, and divergence markers; badge on the Command Center when any tracked contract sits beyond the 90th or below the 10th percentile.

### 7.4 Economy & Credit Dashboard
Renders from `macro_metrics` (§6.4). Groups:
- **Rates:** 10Y, 2Y, T10Y2Y curve (inversion badge when < 0), 30Y mortgage.
- **Credit:** HY OAS and IG OAS with 21-day delta and 1-year percentile; **credit_stress event** when HY OAS rises > 50 bps in 21 sessions (early-warning signal that often leads equities).
- **Housing:** mortgage rate trend, housing starts, Case-Shiller YoY.
- **Consumer:** credit-card delinquency rate, unemployment.
- **Inflation:** CPI YoY (computed from CPIAUCSL).
Each tile: latest value, delta over natural window, 1-year sparkline, 1-year percentile, plain-English template line ("Mortgage rates fell 18 bps this month — supportive for homebuilders and REITs" — template conditions in config, not LLM).

API: `GET /economy/overview`, `GET /economy/series/{metric}?years=2`.

### 7.5 Cross-Listing Premium Monitor (Phase 1)
Detects and tracks price dislocations between two listings of the same underlying company (US ADR vs home-market shares). Motivating example: SK Hynix's Nasdaq ADR (SKHY) trading at a large FX-adjusted premium to its KRX shares (000660.KS) in the days after its July 2026 IPO.

**Core computation** (`signals/premiums.py`, pure):
```
usd_per_fc       = convert(1, from=foreign_currency, to="USD", observed_at=fx_ts)
implied_us_price = foreign_price_local × usd_per_fc × adr_ratio
premium_pct      = (us_price / implied_us_price − 1) × 100
```
All three inputs carry their own timestamps (US price time, foreign price time, FX observation time), each displayed in the UI; prices are RAW, never adjusted (§6.12); the engine refuses to compute — surfacing a config error, not a number — when currency or ADR-ratio metadata is missing or ambiguous. ADR ratios are effective-dated records, so a ratio change never corrupts prior history.

**Session-pairing rule (VERIFIED and DECIDED, M0 spike 1, 2026-07-31).** The two legs' most recent closes are routinely from *different calendar dates* — Seoul closes hours before New York, so a live pull returns a Seoul close dated one day ahead of the latest New York close. Measured impact on the SKHY/000660.KS pair that day: pairing the NY 2026-07-30 close ($149.00) with the Seoul 2026-07-31 close (₩1,718,000) gives **+23.90%**; pairing it with the date-aligned Seoul 2026-07-30 close (₩1,322,000) gives **+61.01%**. Both are defensible; the difference is 37 points, so the convention must be explicit, not incidental.
- **Decision:** pair **most-recent-close to most-recent-close** (the freshest observation available for each leg), and label it exactly that. Never silently date-align, and never render a premium without all three source timestamps and a session badge stating which legs are live vs at-close.
- **Consequence:** during fast-moving periods a material share of the reported premium is a timing artifact. Premium history stores the paired timestamps alongside the value so any later analysis can re-pair; changing the pairing convention creates a new signal version (§7.16).

**Volatility and history caveats (same spike).** Observed daily moves on this pair were −14.6%, −9.6%, −5.6%, +30.0% (Seoul) against −7.5%, −9.0%, −2.6%, +17.5% (ADR). Two consequences: absolute-threshold premium alerts must be set wide on newly listed, high-volatility pairs or they fire on timing noise; and a recent listing has no z-score baseline (the ADR returned only days of history), so the "insufficient history" display path is the normal case for the first ~60 sessions, not an edge case.
Worked example: KRX close ₩2,186,000; USD/KRW 1,390 → foreign price $1,572.66; ADR ratio 0.1 → implied ADR $157.27; ADR trading $152 → premium = −3.3% (discount). Unit tests MUST include this style of hand-computed fixture, including one with a non-0.1 ratio, to catch ratio/FX inversion bugs.

**Per pair, per snapshot (15-min cadence when either market is open; always at each market's close):**
- `premium_pct` (as above)
- `premium_z`: z-score vs the pair's own trailing 90-day premium distribution (until 30 days of history exist, display raw premium with an "insufficient history" tag instead of z)
- `premium_5d_delta`, `premium_21d_delta`
- Which legs are live vs at-close (session flags)

**Events (`premium_event`):** emitted when (a) |premium_z| > 2.0, or (b) |premium_pct| crosses a config absolute threshold (default 10%), or (c) premium moves > 5 points in 5 sessions. Template sentence — persistence qualifier is MANDATORY and non-removable:
> "SKHY is trading at a 14.2% premium to its Seoul listing (FX-adjusted, vs latest KRX close). This gap has widened 6 points in 5 sessions. Cross-listing premiums can persist or widen due to limited share fungibility, borrow constraints, FX moves, and index-flow effects — a premium is a dislocation signal, not a guaranteed convergence."

**UI (`/premiums` page + Command Center strip):** per pair: current premium badge, 90-day premium history chart, both legs' prices (each labeled live/close + timestamp), FX rate used, ADR ratio, and a fixed "Why premiums persist" explainer panel listing: ADR↔ordinary conversion mechanics/limits, short-borrow availability and cost on the expensive leg, home-market short-sale rules, FX exposure, index-inclusion flows, and market-hours mismatch. Forbidden vocabulary anywhere in this module's UI or templates: "arbitrage opportunity," "free money," "guaranteed," "riskless" (enforced by a unit test that greps templates).

**Explicit non-goals:** no trade-leg suggestions, no position sizing, no convergence-date predictions, no broker integration (§2.15).

API: `GET /premiums` (all pairs, current), `GET /premiums/{pair}/history?days=90`.

### 7.6 Themes Engine (Phase 2)
Themes are **static config-defined baskets** spanning asset classes (AI does NOT invent themes in v1; "AI theme detection" is a Phase 3 stretch).

`config.py THEMES` (initial set; each member has a weight, default equal):
- **AI Infrastructure:** NVDA, AMD, AVGO, SMH, MSFT, GOOGL, VRT, ETN, CPER, URA, SRVR, XLU
- **Cybersecurity:** PANW, CRWD, ZS, FTNT, S, HACK, IGV
- **Energy:** XLE, USO, UNG, XOP, OIH, KMI
- **Precious Metals:** GLD, SLV, GDX, SIL (inverse link: DTWEXBGS)
- **Defense & Aerospace:** LMT, RTX, NOC, GD, ITA, PPA
- **Housing:** XHB, ITB, LEN, DHI (macro links: MORTGAGE30US, HOUST)
- **Nuclear:** URA, CCJ, LEU, SMR, OKLO
- **Physical AI / Autonomy (starter):** OUST, LAZR (role: lidar), BB (role: embedded-os), AMBA (role: edge-vision), MBLY (role: core), TER, ROK (role: robotics/automation), NVDA (role: core). Mixes caps deliberately so the scanner's thematic cross-reference has small-cap members to match against.
- **Quantum (starter, demonstrates §7.10 generality):** IONQ, RGTI, QBTS (role: core), LITE, COHR (role: optics — photonics overlaps chains), FORM (role: supplier). Small/speculative caps — the theme exists to prove the engine is industry-agnostic, and its segments participate in bottleneck detection the moment they meet the 3-member minimum.

**ThemeScore ∈ [−100, +100]** (`signals/themes.py`): `100 · tanh( 0.5·avg_member_21d_return_z + 0.3·avg_member_RS_slope_z + 0.2·avg_member_volume_z )` — member z-scores vs their own 1-year history; macro-linked members (FRED series) contribute via their delta percentile with sign from config (`inverse: true` supported). Emit `theme_event` when a ThemeScore crosses ±40 or moves >25 points in 5 sessions.

UI: Themes index (ranked cards, sparkline, top movers) + theme detail page (members table, score history, related smart-money activity filtered to member tickers, related prediction markets). API: `GET /themes`, `GET /themes/{slug}`.

### 7.7 Expectations — Prediction Markets (Phase 2)
Config-listed Polymarket markets; snapshot probability every 6 h + daily close. Panel groups: **Fed policy** (cut/hold/hike probabilities by meeting), **Recession**, **Elections**, **Geopolitics**. Signals: probability, 24 h and 7 d deltas; `expectation_shift` event when any tracked probability moves >10 points in 24 h ("Markets now price a 68% probability of a September rate cut, up 12 points this week"). Treated as *market expectations data*, not betting content. API: `GET /expectations`, `GET /expectations/{market_id}/history`.

### 7.8 Pair Watch — Relative Value Monitor (Phase 2)
Generalizes the pairs-trading concept (the core of long/short equity relative-value work) into a *monitoring* tool: track the price ratio of any two related instruments and flag statistical extremes. No trade legs, no execution — dislocation detection only, same philosophy as §7.5.

**Config (`PAIR_WATCHLIST` in `config.py`):** each pair = two tickers + display name + optional note. Initial set: `MU/SKHY` (memory peers), `GM/F`, `XLE/USO` (equities vs commodity), `GLD/GDX` (metal vs miners), `QQQ/RSP` (growth vs equal-weight). User-extendable. Single-stock-vs-its-sector-ETF pairs (e.g. `NVDA/SMH`) are an intended use: a stock's ratio to its own sector breaking down while the sector holds is the classic relative-strength-loss warning, and Pair Watch flags exactly that.

**Per pair, daily + 30-min intraday (`signals/pairs.py`, pure — reuses the §7.2 RS math):**
- `ratio = close(a) / close(b)`; `ratio_z`: z-score of log-ratio vs its trailing 252-session distribution (until 60 sessions of history, show raw ratio with "insufficient history" tag)
- 21-day ratio percentile vs 1-year range; 5-day ratio change
- Return correlation (90-day) — shown so the user can judge whether the pair relationship is even stable

**Events (`pair_zscore`):** |ratio_z| > 2.0 (config) or z crosses back through 0 after an extreme (mean-reversion-completed marker). Template sentence with mandatory qualifier:
> "MU/SKHY ratio is 2.3 standard deviations above its 1-year mean — MU has outperformed SKHY by 18% over 21 sessions. Ratio extremes can persist or extend; a z-score is a dislocation flag, not a reversion forecast."

Same forbidden-vocabulary rule and grep test as §7.5. API: `GET /pairs`, `GET /pairs/{slug}/history?days=365`. UI: `/pairs` page (ratio chart with ±2σ bands, both legs, correlation badge) + extreme-pair badges on the Command Center strip alongside premiums.

### 7.9 Catalyst Sympathy Scanner (Phase 2)
Implements the "second-order beneficiary" idea from momentum playbooks as a *detection* tool: when a catalyst moves one member of a theme hard, the related names that haven't moved yet are the interesting ones — not the gapper itself.

**Prerequisite:** `theme_members` gains a `role` tag (`core | supplier | network | optics | memory | foundry | power | operator`), set in the THEMES config, so the engine knows what "supply chain of the mover" means.

**Detection (`signals/sympathy.py`, pure; runs each intraday compute tick):**
1. **First-order mover:** any theme-member symbol with day move > +4% (or gap > +3%) on ≥ 2× relative volume.
2. **Sympathy set:** all other members of that mover's theme(s), each annotated with role, day move, and relative volume.
3. **Laggard ranking:** sort the sympathy set ascending by |day move| — the playbook insight, inverted into analytics: **smallest move with shared exposure = most informative**, because it hasn't repriced yet (or the market is saying it isn't actually exposed — both worth knowing).
4. Emit `sympathy_event` once per theme per day (fingerprint: theme + date): mover, move size, top-5 laggards with roles.

Template sentence (mandatory qualifier, same grep test as §7.5/§7.8):
> "AVGO is +6.8% on 2.9× volume within AI Infrastructure. Least-moved members sharing the theme: VRT (power, +0.4%), CPER (copper, +0.2%), SRVR (data-center REITs, −0.1%). A sympathy lag is a repricing question, not a catch-up forecast — laggards can stay flat because the market judges them unexposed."

**Explicit non-goals:** no entry/stop/target suggestions, no "buyable" labels, no gap-quality scoring of trade setups. News-sweep automation is excluded in Zero-Cost Mode (no reliable free real-time news API); the event links out to a prefilled news search URL per laggard instead. API: `GET /sympathy/events?days=5`. UI: events feed on the Themes pages + Command Center when fresh.

### 7.10 Supply-Bottleneck Detector (Phase 2)
Detects when the market is repricing a supply-chain **segment** — a (theme, role) group like AI Infrastructure/memory or AI Infrastructure/optics — as scarce. Motivating cases: the 2025–26 memory and photonics runs, where the constrained links of the AI chain led the entire index. The detector is industry-agnostic by construction: it operates on theme role tags (§7.6/§7.9), so a Quantum theme with its own roles onboards the same engine with zero code changes.

**Definition:** a segment = all symbol members sharing a `role` within one theme, minimum 3 members (smaller segments are skipped — one stock is a company story, not a segment).

**The economic signature of a bottleneck, translated to measurables** (`signals/bottleneck.py`, pure, daily):
1. **Sustained segment leadership vs its own theme** — z-scores of the 21- and 63-session OLS slopes of `log(segment_equal_weight_index / theme_equal_weight_index)`. Versus the theme, not SPY: a bottleneck outperforms *its own boom*.
2. **Internal breadth** — % of segment members above their 50DMA and positive over 21 sessions. Scarcity lifts every supplier's pricing power; a genuine bottleneck moves the whole segment.
3. **Low winner dispersion** — cross-member 21-session return dispersion, inverted. Broad repricing (low dispersion, high breadth) = segment scarcity; one runaway winner (high dispersion) = a company story, scored down.
4. **Volume** — segment aggregate dollar-volume z.
5. **Earnings support** (once §7.13 is live) — mean revenue-surprise % of segment members, most recent quarter. Pricing power shows up in revenue beats.

**BottleneckScore ∈ [0, 100]** = weighted blend of the five (config weights; earnings weight redistributed until P2 earnings ships). **States:** `Emerging` (score crosses 60), `Confirmed` (score ≥ 60 for 15 consecutive sessions), `Easing` (from Confirmed: score < 40, or internal breadth < 50% for 10 sessions — the supply response arriving or demand cooling), `None`. State transitions emit `bottleneck_event`.

Template sentences (mandatory qualifiers, same grep test):
> "Confirmed bottleneck signature: AI Infrastructure / memory has outperformed its own theme for 34 sessions with 6 of 6 members above their 50-day average and low dispersion — the market is pricing segment-wide scarcity, not a single winner. This detects a repricing already underway, not its beginning, and supply responses or demand shifts can end it without notice."
> "Easing: AI Infrastructure / optics breadth fell to 3 of 7 members positive over 21 sessions — the scarcity repricing is narrowing."

**What this module is honest about:** price-based detection *confirms* a bottleneck the market has started pricing; it will never hand over the next Lumentum at the bottom, and it must never claim to. Its value is (a) catching **persistence** early enough to matter — scarcity cycles historically run months to years, so the middle of the move is long — and (b) the `Easing` state, the underrated half: knowing when the segment trade is dying is rarer and more valuable than one more way to notice it's alive. FlowDesk reports segment states; the user decides what they mean.

UI: segment table on `/themes` (score, state badge, sessions-in-state, member count), state history on theme detail pages, Confirmed/Easing badges on the Command Center. API: `GET /bottlenecks` (all segments, current), `GET /bottlenecks/{theme}/{role}/history`.

### 7.11 Swing Desk — Scanner, Setup Geometry & Risk Arithmetic (Phase 2, promoted)
The scanner below produces *candidates*; the Swing Desk wraps it in the three things that
determine whether a candidate becomes a good or bad trade. All three are descriptive
arithmetic on stored data — no recommendations, no entries, no execution (decision 15/17).

**(a) Setup geometry (per candidate).** Displayed alongside the score: most recent swing low
and swing high (fractal pivots over a config lookback), distance from price to each in % and in
ATR(14) multiples, distance from the 20/50/200-day moving averages, position within the 20-day
range, and the last consolidation's high/low. These are **measurements of where price sits
relative to its own structure** — FlowDesk states the geometry; the user decides whether it
constitutes a setup. Forbidden framings (grep-enforced): "entry", "buy zone", "target",
"support", "resistance".

**(b) Risk arithmetic (calculator, not sizing advice).** Given a user-supplied invalidation
level and a user-supplied risk budget, the panel computes the arithmetic the user would
otherwise do by hand: distance-to-invalidation in % and ATR, implied share count for that risk
budget, resulting position weight, and the R-multiple of any user-supplied upside level. It
computes nothing the user has not supplied an input for, recommends no risk budget, and
suggests no invalidation level. Rationale for including it despite decision 17: the arithmetic
is the user's own, and a position entered without a defined invalidation is the documented
origin of the "many small-to-medium losses in positions that never became convictions" failure
mode this module exists to attack.

**(c) Idea-source tagging (feeds §7.16 outcome tracking).** Every candidate the user acts on is
logged with its **origin** — own setup, FlowDesk signal, news, third-party call/signal channel,
social — alongside the thesis in one line. Outcome tracking then produces a hit rate and
expectancy **per source**. This is the highest-value line in the module: within two quarters it
answers empirically which idea sources have positive expectancy for this user and which are
donating money, which no amount of better filtering can substitute for.

**(d2) Liquidity and float refinements (added v4.9.7).** Two components added to the candidate
score and displayed on every row: **float rotation** (session volume ÷ free float) — the measure
that separates a genuine repricing from one large print in a thin name, and the small-cap metric
most predictive of continuation; and **Amihud illiquidity** (mean |daily return| ÷ daily dollar
volume over 21 sessions), an academic-standard illiquidity measure that also feeds the §7.17
capacity ceiling directly, so a candidate's score and its tradeable size are computed from the
same number.

**(d) Swing filters (scanner scoring extension).** Beyond the breakout gates below, the
candidate score adds config-weighted components for: relative strength vs the symbol's sector
ETF over 21/63 sessions, pullback depth from the 20-day high, volume contraction followed by
expansion (dry-up then thrust), distance from the 50DMA in ATR units, thematic membership
(§7.6) and bottleneck-segment membership (§7.10), and earnings proximity (a hard flag, not a
score component — a report inside the holding window changes the risk profile entirely).

**Promotion note (v4.9.6):** this module is promoted from Phase 2b step 15 to immediately after
M5 in §16, because scanner-without-scorecards is an idea firehose and scanner-with-scorecards is
an attribution instrument. It ships only after outcome tracking exists, never before.

### 7.11.1 Small Cap Breakout Scanner (the underlying candidate generator)
Small caps are where theme repricings produce their largest percentage moves — and where dilution, illiquidity, and lottery-ticket odds destroy the unwary. The scanner therefore has three layers: the technical score, a thematic cross-reference, and a red-flag panel that is never optional.

**Thematic cross-reference:** every scanner candidate is joined against theme membership (§7.6). A candidate that is a member of a theme scoring > +40, or of an Emerging/Confirmed bottleneck segment (§7.10), gets a `thematic` badge naming the theme and role — the systematized version of the pattern where small autonomy names (embedded OS, lidar) rerate when their theme heats up. Thematic small caps sort above equal-scored non-thematic ones; a volume spike with no theme, no catalyst, and no role is exactly the lottery ticket the user's own playbook rules exclude.

**Red-flag panel (mandatory on every scanner row and detail view):**
- **Dilution:** shares outstanding change over 1y (flag > 15%) and any detected recent offering or shelf registration (8-K/S-3 via the EDGAR polling already built for §7.3/§7.13). Small-cap rallies are routinely sold *into* by the company itself — a breakout plus a fresh share offering is a different object than a breakout alone.
- **Insider net selling:** 90-day net insider sells from the §7.3 Form 4 data — already ingested; the join is free. Executives selling into the same rally the scanner flagged belongs next to the score, not buried in another tab.
- **Liquidity reality:** average dollar volume and an estimate of position size that would exceed 1% of it.
The panel presents facts without verdicts — a flagged name isn't "bad," but a user who buys a breakout without seeing the concurrent offering was failed by the tool.

**Universe** rebuilt weekly: US common stock, price $2–$50, market cap $100M–$5B, 30-day avg dollar volume > $2M → `scanner_universe`. Every 5 min in market hours compute: relative volume (vs 30-day time-of-day-adjusted average), gap %, day-range position, distance to 20/50-day highs, short interest % float (biweekly reference), insider net buys 90d (reuse §7.3 data). **BreakoutScore ∈ [0,100]** with hard gate rel_vol ≥ 3 to appear at all; weights in config. Alert on first crossing ≥ 80 per day. The page displays the scanner's own §7.15 outcome scorecard prominently, plus a fixed distribution note: small-cap returns are heavily positively skewed — most flagged names underperform and a few large winners carry the averages — so hit rate and median outcome matter more than remembered winners. Mandatory disclaimer on page.

### 7.12 Crypto Flows (Phase 2)
BTC, ETH, SOL. Panels: price, daily ETF net flow (BTC/ETH), aggregate funding rate, OI 24 h change, liquidations long/short. **CryptoStance** (Bullish/Neutral/Bearish) from a deterministic rules table in config, each stance rendered with its reasons ("Bullish: ETF inflows +$420M, funding neutral, OI rising alongside price").

### 7.13 Earnings Intelligence (Phase 2)
Three layers: calendar, reaction monitor, debrief.

**Calendar & surprises (base):** calendar (this week / next week, filterable by theme membership), surprise tracker (EPS/revenue beat-miss for config universe), aggregate line computed by template: "Of {n} S&P tech reports this season, {x}% beat on EPS; average revenue surprise {y}%." Feeds the daily brief context.

**Post-Earnings Reaction Monitor:** for each report in the universe, record next session's gap % and full-day move, then compute:
- **Move vs implied move** — realized earnings-day move against the options-implied move when the §7.17 experimental options variant is enabled (a ±12% move on a ±6% implied move is information; so is the reverse).
- **Surprise–reaction divergence** — beat-but-fell and miss-but-rallied flags (`earnings_divergence` event). Price reacting against the surprise reveals what expectations really were; these are among the most informative single-day data points a report produces.
- **Extreme-reaction flag** — earnings-day move beyond the stock's own 2σ of past earnings reactions, with context attached (segment/bottleneck state, RS vs sector, theme membership). This is the honest version of "discount detection": FlowDesk flags the extreme and attaches evidence; it never labels a sell-off a discount. **Forbidden vocabulary for this module (grep-enforced): "discount opportunity," "buy the dip," "swing play," "oversold bargain."**

**Drift vs reversion, answered empirically:** every earnings reaction feeds §7.15 outcome tracking (fwd_5/fwd_21 bucketed by surprise sign × reaction direction), and the simulator gains two opposing templates — `pead_drift` (enter in the direction of the surprise after the reaction day; the post-earnings-announcement-drift literature's side) and `earnings_reversion` (fade extreme negative reactions; the "discount" thesis). Report both against `spy_hold`. Note the honest prior stated in the report template: decades of PEAD research find prices *continue drifting in the surprise direction* for weeks — meaning the average post-miss crash keeps sagging, and "buying the earnings dip" is historically the wrong side on average. Whether that holds in this universe and era is exactly what the two templates measure.

**Report/Call Debrief:** ingest the earnings press release from SEC EDGAR (8-K exhibits — free, official) per report. With `ENABLE_AI=true`, one cached LLM call per filing produces a structured debrief — guidance change (raised/cut/held, with the filing's numbers), one-time items, segment commentary, notable language — under decision-7 grounding rules: every claim must appear in the filing text, no numbers not present in it, stored with the source excerpt for audit. With AI off, the panel shows structured surprise data + a link to the filing. Full call *transcripts* are a paid upgrade (§18 Tier 2) — press releases carry the numbers and guidance; transcripts add Q&A tone.

API: `GET /earnings/calendar`, `GET /earnings/surprises?days=30`, `GET /earnings/reactions?days=30`, `GET /earnings/{ticker}/debrief?period=`.

### 7.14 AI Layer
Exactly two capabilities. Verbatim prompts in §17.3.

**(a) Daily Capital Flow Brief** — worker task 8:30 AM ET weekdays. Two composition modes sharing one pipeline: with `ENABLE_AI=false` (Zero-Cost default) the brief is assembled deterministically by concatenating each module's template sentences under the fixed section headers — free, always available; with `ENABLE_AI=true` the same context JSON is sent to the LLM for narrative composition. Either way, `ai/context_builder.py` assembles ONE compact JSON (validated against a JSON Schema, ≤ ~5K tokens): top/bottom rotation scores + new rotation events; breadth + sentiment composite + divergence flags; economy deltas + any credit_stress; smart-money clusters + top disclosures; theme movers (P2); expectation shifts (P2); crypto stances (P2); earnings aggregate (P2); day-over-day deltas throughout. The context builder also runs a deterministic **tension detector**: a config matrix of signal pairs expected to agree (e.g., equity regime vs credit-spread direction; rotation risk-on vs breadth; crypto stance vs sentiment composite) is checked for active disagreements, and any tensions are placed in the context as their own key — so the brief's conflict reporting is driven by computed facts, not left to the LLM to notice. One LLM call produces a brief with fixed sections: **Dominant Theme · Markets · Economy & Credit · Smart Money · Expectations · Tensions · Watch Items** (this satisfies the "multi-agent" idea with one call). Stored in `ai_briefs` WITH the exact context JSON. Auditability is enforced at runtime via **structured claims**: the LLM must return JSON — per section, a list of claims each carrying `text` and `evidence_paths` (JSONPath pointers into the context, e.g. `$.rotation.top[0].score`). A validator checks every claim before rendering: evidence paths exist, entity names in the text match the referenced objects, units match, directional words match signs, fields marked stale are described as stale, and all required sections (including Tensions) are present. Validated claims render to prose; any failed claim discards the LLM brief for the deterministic template, logging the offending claim. This is stronger than numeral-matching — it grounds *meaning*, not just digits — and simpler, because the LLM's job becomes citation rather than free prose. Decision 7, made mechanical. Served cached all day. If any critical feed is stale, the task still runs but the context marks sections `"stale": true` and the prompt instructs the model to flag them.

**(b) Ask-the-Market** — `POST /ai/ask`, streaming. Same context builder + question. Redis cache key = (question hash, context version). Rate limit 10/hour/IP. If the answer requires data outside the context, the model must say so — refusal instruction is in the system prompt.

**(c) Explicitly forbidden:** price prediction, buy/sell recommendations, portfolio advice, numbers not present in context. Enforced in system prompts and spot-checked in tests.

### 7.15 Risk Lens — Scenario Replay & Signal Crowding (Phase 2b)
FlowDesk detects opportunity everywhere and risk nowhere — a structural blind spot corrected here, adopting the *question* institutional risk systems (Aladdin-class) ask, in FlowDesk's descriptive, zero-cost form. Both features are historical measurement, never forecast.

**(a) Scenario Replay.** A config-defined library of historical stress episodes (2018 Q4 selloff, Mar 2020, 2022 rate shock, Aug 2024 unwind, any user-added date range) plus condition-defined scenarios computed from `macro_metrics` (e.g. "HY OAS widens ≥ 100 bps in 21 sessions," "10Y yield +75 bps," "VIX doubles"). For any theme, bottleneck segment, rotation leader, or pair, the panel shows how that entity actually behaved during each matching historical window — drawdown, recovery time, correlation-to-SPY shift. Template line: "AI Infrastructure fell 31% over the 2022 rate-shock window and took 14 months to recover; during Aug 2024 it fell 12% and recovered in 9 sessions." **Framing rule (grep-enforced):** scenario output is labeled *how this behaved before*, never *what will happen*; no probabilities are attached to scenarios, and reports state that regime composition changes (today's AI Infrastructure basket is not 2022's).

**(b) Signal Crowding Detection.** The counterpart to Aladdin's concentration analytics, pointed at FlowDesk's own output: compute the pairwise 90-day return correlation of the *entities* named by all currently-active events (rotation winners, bottleneck segments, theme movers, sympathy laggards, scanner names). When active events cluster above a correlation threshold (default 0.7), emit `signal_crowding`: "5 active events name entities with mean pairwise correlation 0.81 — this is one bet expressed five ways, not five confirmations." This is the most FlowDesk-native risk idea in the doc: a self-grading platform must also tell the user when its own signals are redundant, because apparent confluence across correlated signals is the classic path to unintended concentration.

**(b2) Signal interaction graph (extends crowding from correlation to structure).** Beyond price correlation, FlowDesk measures relationships among its own signal *types* over accumulated event history: co-occurrence rates, conditional hit rates (does a rotation event following a bottleneck confirmation outperform a standalone one?), mutual information between signal families, and **lead-lag** (which type tends to fire first for the same entity). Rendered as a dependency graph on /signals and surfaced in scorecards as conditional bands — turning "confluence" from a feeling into a measured quantity. Only possible because every event carries lineage and outcomes.

**(c) Exposure decomposition (extends the §7.18 portfolio note):** when manual positions exist, decompose them by sector, theme, and bottleneck-segment membership, with the same correlation and crowding treatment plus scenario replay applied to the actual holdings. Phase 3, gated on auth.

API: `GET /risk/scenarios?entity=`, `GET /risk/crowding`. UI: scenario strip on theme/segment/pair detail pages; crowding badge on the Command Center whenever active.

### 7.16 Alert Engine & Signal Outcome Tracking
`alert_rules(type, condition JSONB, channels)`; evaluator task chained after every compute task. Built-in rule types: `rotation_event`, `breadth_warning`, `credit_stress`, `congress_ticker`, `congress_cluster`, `insider_cluster`, `conviction_overlap`, `premium_event`, `pair_zscore`, `regime_change`, `sympathy_event`, `bottleneck_event`, `earnings_divergence`, `signal_crowding`, `positioning_divergence`, `theme_event`, `expectation_shift`, `scanner_breakout`, `crypto_flow`, `macro_move`. Delivery: in-app + Discord webhook (P1, both free) + WS push (P2); email (optional paid upgrade). **Dedup fingerprint** = sha1(rule_id + entity + date): one fire per day per entity per rule.

**Signal Outcome Tracking (the reflection loop, made deterministic).** Every emitted event — `rotation_event`, `bottleneck_event`, `sympathy_event`, `premium_event`, `pair_zscore`, `theme_event`, smart-money clusters — is automatically scored after the fact: a daily `score_event_outcomes` task records the forward return of the event's relevant instrument(s) at t+5 and t+21 sessions, both absolute and relative to the event's natural benchmark (rotation winner vs loser spread; bottleneck segment vs its theme; sympathy laggards vs the mover; premiums vs their own subsequent premium; clusters' tickers vs SPY). Results land in `event_outcomes`, and each event type gets a rolling scorecard: count, hit rate, mean/median forward return, and a 6-month trend (signal decay detection). The scorecard renders on a `/signals` page and as a small badge next to every live event ("rotation events, trailing 12 mo: 61% positive 21-session spread, n=34") — so the platform continuously grades itself in public and the user always knows how much weight each signal type has historically deserved. This is the live-side complement to the §7.16 simulator: the backtest evaluates strategies on history; outcome tracking evaluates the *actual emitted signals* going forward, catching decay, regime sensitivity, and bugs that backtests can't. Scorecards state `n` alongside every rate — a 70% hit rate on n=7 is displayed, never celebrated (small-sample tags below a config threshold, default n=20).

**Scorecard integrity (protects the moat):** every emitted event is stamped with the `config_version` (a hash of the signal thresholds/weights active at emission). Scorecards aggregate within a config_version by default; when thresholds change, the scorecard shows a segmented view ("current config: 58%, n=12 · prior config: 61%, n=34") rather than silently blending two different signals — untracked threshold tuning is accidental p-hacking, and a self-grading product that p-hacks itself has no product. Threshold changes are logged with date, **hypothesis, and evaluation window** in `docs/CONFIG_LOG.md` — tuning without a stated hypothesis is p-hacking with extra steps.

**Input confidence tiers and score bands (right-sized uncertainty propagation).** Every signal input carries a confidence tier derived from properties FlowDesk already tracks — **high** (exchange prices, official FRED releases at final vintage), **medium** (revision-prone macro vintages, delayed option chains, bar-approximated volume profile), **low** (range-valued disclosure amounts, lagged filings, session-mismatched foreign closes, any quarantine-repaired series). A score computed from any low-tier input displays a confidence chip naming the weakest input, and the daily brief must describe such claims with hedged wording (enforced by the §7.14 claim validator's freshness/qualification check). Numeric uncertainty comes from the calibration tables rather than analytic error propagation: scores render with their empirical band ("BottleneckScore 87, historical 85–90 band: +4.2% median, 25th–75th −0.8% to +9.6%, n=44") — honest dispersion from observed outcomes, no fabricated ± from assumed distributions. Full parametric uncertainty propagation is explicitly out of scope (decision 26's right-sizing principle).

**Score calibration (what does a score of 87 actually mean?):** for every continuous score FlowDesk emits (RotationScore, ThemeScore, BottleneckScore, BreakoutScore, premium z), outcome tracking maintains **decile/bucket calibration tables** — historical forward-return distribution and hit rate per score band, with n. The UI shows the band's history beside the live number ("BottleneckScore 87; historically 85–90 → median 21-session excess +4.2%, 61% positive, n=44"), which converts an arbitrary index into a calibrated statement. Calibration is computed within signal version and regime, and bands with n below the small-sample floor display the n instead of a rate.

**Multiplicity across signals:** grading ~15 event types simultaneously is 15 concurrent hypotheses; scorecard significance uses a false-discovery-rate correction across the active signal family so the best-looking signal is not simply the luckiest one.

**Episodes (sample independence):** repeated firings of one condition are one observation. An episode opens when a condition first activates and closes when it clears; intra-episode events share an `episode_id` and count once. Scorecards display raw event count AND independent episode count, hit rate, mean/median, a bootstrap confidence interval, overlapping-horizon %, a by-regime breakdown, and a breakdown by **implied-correlation regime** (§6.3) — high implied correlation means a macro-driven tape in which sector-rotation signals should mechanically work worse, so reporting rotation hit rate conditioned on it tests one of the platform's own load-bearing assumptions — a premium at z>2 for five straight days is one episode, and a scorecard that counts it as five is lying with its own n.

**Live vs replay (never merged):** live scorecards contain only events actually emitted at the time, frozen per signal version, and are never rewritten by historical replay; current-rule retrospective replays are a separate, clearly labeled view. Every event additionally stamps `signal_version`, `code_commit_sha`, and `snapshot_id` alongside `config_version`, since signals change through code, data corrections, and universe edits — not thresholds alone.

### 7.17 Backtest & Strategy Simulator (Phase 2)
`backend/backtest/replay.py` + `backend/backtest/simulate.py`: replay historical daily bars through the SAME pure functions in `signals/` (this is why they must be pure). Two levels:

**(a) Signal replay** (as before): rotation events + forward 10/21-day relative returns of flagged pairs; theme_event crossings + forward basket returns; scanner alerts + forward 5-day returns; pair_zscore extremes + forward ratio behavior.

**(b) Strategy simulation** — evaluates what systematically acting on FlowDesk's signals *would have* produced, using strategy templates borrowed from hedge fund structure (long/short equity, market neutral, momentum) as **evaluation frameworks, not products**:
1. `rotation_long` — hold the top-N RotationScore sector ETFs, rebalanced monthly (long-only momentum)
2. `rotation_ls_neutral` — long top-N, short bottom-N, equal dollar (market-neutral: isolates whether the *ranking* has value independent of market direction)
3. `pair_reversion` — for PAIR_WATCHLIST pairs: enter the spread when |ratio_z| > 2, exit at z = 0, max holding 60 sessions (relative-value mean reversion)
4. `rotation_long_gated` — same as `rotation_long` but moves to cash whenever the §7.1 Regime Badge is Risk-Off (tests whether the regime gate adds value over the ungated version — report both side by side)
5. `disclosure_copy` — equal-weight long positions opened at the **disclosure date** (never the transaction date — entering at transaction date is look-ahead bias, since the information didn't exist publicly yet) of disclosed buys by a configurable filer set (default: all congress; executive filers only via their §7.3 aggregate sector flows), held 60 sessions. This is the honest test of every "copy the politicians" claim: report it against `spy_hold` and let the number speak.
6. `bottleneck_hold` — equal-weight the members of any segment while its §7.10 state is Emerging or Confirmed; exit on Easing. Run it over the 2025–26 window specifically: the memory and photonics segments are the natural validation case, and the report should show how much of those runs the state machine would have captured and how much it missed at the start.
7. `spy_hold` — buy-and-hold SPY benchmark (every report includes it)

**Challenger interface:** the simulator also accepts external signal generators through a minimal contract — `SignalGenerator.rank(bars_window) -> ordered symbol scores` — evaluated under the exact same costs, calendar, and metrics as the deterministic templates. This is how ML models (§7.17 ML Challenger Harness) plug in without touching live code paths. Deterministic templates are the incumbents; anything else is a challenger that must beat them *and* `spy_hold` to matter.

**Factor attribution (mandatory — no report is valid without it).** Every strategy's daily returns are regressed against a factor set (market, size, value, momentum, quality, low-vol; sector-ETF returns as an additional control for thematic strategies), using freely available factor return series. The report leads with **annualized alpha, its t-statistic, and the factor loadings** — not with total return. Interpretation rule printed on every report: if alpha is not statistically distinguishable from zero after factors, the strategy is a factor replicator and must be described as such, since the same exposure is purchasable in an ETF for a few basis points. A momentum-loaded rotation strategy beating SPY is not evidence of edge; it is evidence of momentum.

**Benchmark panel (not a single benchmark):** every report shows the strategy against SPY, the relevant sector/thematic ETF, an equal-weight comparator (RSP), a market-neutral variant where applicable, and a **volatility-normalized** comparison — because a higher-beta strategy in a bull market outperforms by construction.

**Capacity & market impact (required before any strategy is described as deployable).** Costs are modeled as commission + half-spread + slippage + **market impact** via a square-root law scaled to order size relative to ADV, replacing flat-bps assumptions for anything below large-cap liquidity. Each report states a **capacity ceiling** — the notional at which modeled impact consumes the edge — computed per strategy from its universe's ADV distribution. Capacity is reported as a feature, not an embarrassment: a strategy that works to $2M and no further is precisely the kind of edge institutions cannot compete for (§19.4).

**Entry-timing convention (declared per strategy, tested as specified).** Every template states signal-computation time and execution point explicitly — default for daily-signal strategies: signal computed after the 16:30 close, entered at the **next session's open**, with next-close and next-day-VWAP as alternates. The convention is part of the strategy definition and any change creates a new signal version; the decision-to-execution gap is where paper edges quietly die.

**Reproducibility manifest (emitted with every report and every live scorecard snapshot).** A machine-readable header serializing the full lineage: strategy name and signal_definition_version, dataset snapshot id and cutoff, code commit SHA, config version, theme/universe version, factor model used, execution convention, cost model, trial count N from the registry, and a result hash. Any report without a complete manifest is treated as unpublishable. This is the difference between "I got 17% once" and a result a stranger — or you in three years — can regenerate exactly.

**Metrics per run:** CAGR, annualized volatility, Sharpe ratio (risk-free = FRED `DTB3` 3-month T-bill series), max drawdown, beta vs SPY (for the market-neutral template, |beta| < 0.2 is the pass/fail neutrality check), hit rate, payout ratio (avg win ÷ avg loss), turnover — and, as a **descriptive statistic only**, the full-Kelly fraction implied by that hit rate and payout ratio, printed with a mandatory caveat block: Kelly assumes the true probabilities are *known*, backtest estimates carry error that makes full Kelly systematically over-aggressive, practitioners who use it at all use a fraction (commonly ~quarter-Kelly), and per decision 17 this number describes the historical strategy record — FlowDesk does not size positions for the user, ever. Output: CSV + a markdown report table.

**Strategy Research Protocol (binding for ANY multi-strategy search, on either plane):** mass testing without multiple-testing discipline manufactures false discoveries — at a 5% significance bar, 1,000 independent trials produce ~50 "working" strategies by chance alone, and the best-looking survivors are the most likely flukes. Rules:
1. **Pre-registration:** every trial is logged in the hypothesis registry BEFORE running — hypothesis in one sentence, instrument, session, period, parameters, predicted sign, **and a required economic-rationale block: why should this edge exist (risk premium / structural constraint / behavioral bias / information asymmetry), who is on the other side and why they accept the loss, under what conditions it should stop working, and the strongest competing explanation.** A hypothesis that cannot fill this block is not tested — stating the mechanism kills a surprising share of ideas before a weekend is spent on them, and an edge without a mechanism cannot be distinguished from a mined coincidence or predicted to decay. Untested-then-logged is research; tested-then-logged-if-good is fraud against yourself.
2. **Trials are counted honestly:** every parameter combination is a separate trial. "One strategy, ten parameter sets" is ten trials. The registry maintains a running family count N.
3. **Survivors carry their N:** any reported result states "1 of N trials"; Sharpe ratios are haircut for the search (deflated-Sharpe-class correction, or at minimum the N is printed beside every metric so the reader can do it).
4. **Splits:** in-sample for exploration, validation for selection, and a final held-out period touched ONCE per strategy family, ever. A survivor that dies on the held-out set is dead.
5. **Sample and regime floors:** minimum independent-trade count per trial (default 100) AND coverage of more than one volatility regime; six months of one instrument's one session is one regime — enough n for intraday concepts, zero regime diversity, and reports must say so.
6. **Cost realism floor:** intraday futures trials price the spread + commission per round turn against the average trade PnL before anything else; most intraday edges die right here, cheaply.
7a. **Instrument-construction prerequisite:** before the first futures strategy is admitted to the Execution Plane, a continuous-contract roll methodology (calendar vs volume-triggered roll date; ratio vs difference back-adjustment) must be specified, tested, and recorded in the registry — stitched or naively back-adjusted series produce results that look clean and are wrong. This is a prerequisite on futures research, not an invitation to build a general multi-asset backtester (§1 admission test still governs).
7. **Data-resolution floor:** every registry entry declares its decision timescale, and a backtest whose data resolution is coarser than its decision resolution is rejected at registration. Matrix: seconds-scale entries/exits → tick data with bid/ask attribution and modeled latency (Execution Plane); minutes-to-hours intraday → 1-minute bars minimum with spread-haircut fill assumptions (ticks preferred); multi-day and slower → daily bars are valid for *signal* validation. One refinement even for slow strategies: before live arming, run tick-based transaction-cost analysis on a sample of intended entries — large moves cluster in sub-second bursts, spreads widen exactly when you want to trade, and constant-spread assumptions systematically understate slippage. Bars establish whether the signal is real; ticks establish what it costs to touch.
8. **The graveyard is a first-class output and a living research database:** every trial — pass or fail — persists with its hypothesis, economic rationale, data used, parameters, result, **failure classification** (no edge / edge below costs / insufficient sample / data-quality defect / implementation error / regime-dependent), researcher notes, and follow-up ideas. Negatives are searchable and never silently re-run as "new ideas." Each rejected idea may also record **retest conditions** (new data available, regime change, cost structure change, longer sample) so research debt resurfaces deliberately rather than being lost or rediscovered by accident. Survivors enter the revised-decision-15 gates (paper → arming) like any other strategy; surviving the search is admission to the pipeline, not proof of edge.

**Performance Claim Standard (binds FlowDesk's own published numbers; doubles as the lens for evaluating anyone else's).** Every performance figure FlowDesk publishes — README, scorecards, §19 marketing, or a future pitch deck — carries all of the following, or it is not published: (1) **live vs simulated** clearly labeled, never blended; (2) **benchmark and period** stated, with the same period's benchmark return shown beside the strategy's; (3) **net of costs** modeled, with the cost assumptions printed; (4) **max drawdown and Sharpe**, with the §7.16 trial count N so the reader can deflate; (5) **capital scale and capacity** — returns on small capital rarely survive size; (6) **who verified it** — self-reported, third-party administrator, or audited, named explicitly; (7) **regime coverage** — how many distinct volatility regimes the record spans; (8) the **graveyard denominator** — how many strategies were tested to produce this survivor.
Red flags this standard is designed to expose, in FlowDesk's own claims first and others' second: volume figures presented where AUM or returns belong (turnover is not profit); "independently verified" without a named verifier; single-year records in a strong bull market presented as skill rather than beta; consciousness/AGI/"knows what tomorrow looks like" language in a quantitative context; and distribution exclusively through paid press-release wires and syndication rather than audited reporting. A claim that cannot survive this checklist is marketing, and FlowDesk's entire differentiation (§19.4) is being the product that publishes what marketing omits.

**Cost hierarchy and stated limitations block (rewritten v4.9.10 — resolving a v4.8 contradiction).** Two paragraphs above, this section requires commission + half-spread + slippage + square-root market impact with a capacity ceiling; this block previously still said "flat config bps, no slippage or market impact." Both were written as if they governed the same simulator, and they cannot. The resolution is two declared research levels, and **every report header states which level produced it:**

- **Level 1 — Preliminary signal replay.** Flat config bps per trade (default 10), no slippage or impact modeling. Valid for one question only: *does the signal contain information at all?* A Level 1 report is expressly **not deployability evidence** and its header must say so.
- **Level 2 — Deployability report.** The full v4.8 stack: commission + half-spread + slippage + square-root market impact scaled to ADV, with the capacity ceiling stated. Required before any strategy is described as deployable, admitted to the decision-15 gates, or quoted outside its own file.

Limitations that apply at **both** levels, printed at the top of every report: daily closes only (no intraday fills); **no borrow cost, borrow availability, or margin modeling** — any short leg is therefore an optimistic upper bound, and a short strategy without measured borrow data may be researched but **never labeled deployable** (its report states "execution feasibility unknown"); survivorship-clean only to the extent the symbol universe is (see the M2 universe-membership requirement). Purpose is *signal evaluation and honest documentation* — publish results in the README whether they beat SPY or not; a negative result documented honestly is a feature of this project, not a failure. No live trading, no advice (§2.15 applies).

### 7.18 Phase 3 modules (design notes only — do not build early)
- **Options intelligence:** unusual-activity feed (paid provider), put/call by symbol, IV rank — plus the module's centerpiece, **volatility-gap detection** (the honest version of "finding gaps in options prices"): (a) per-symbol IV vs trailing 21-session realized volatility, with the spread's own 1-year percentile (the variance risk premium, usually positive — IV typically *exceeds* subsequent realized vol, which is why systematically buying volatility loses on average); (b) **earnings implied-move monitor** — before each earnings report, ATM-straddle cost ÷ spot = the options market's implied move %, displayed against that stock's last 8 realized earnings-day moves, flagging unusual divergence in either direction. Detection only: FlowDesk shows the gap; it never recommends buying or selling the straddle. **Framing rule (grep-enforced like §7.5):** options templates must never contain "regardless of direction," "profit either way," or equivalents — every straddle-related sentence states the breakeven ("profits only if the move exceeds the ±X% premium paid"). A **zero-cost experimental variant** is permitted before paid data: yfinance option chains (delayed, occasionally stale — acceptable at daily cadence for the earnings monitor only), flag `ENABLE_OPTIONS_EXPERIMENTAL`, every page badge-labeled "delayed/experimental data." Also in this module: a **Dealer Positioning panel** — daily aggregate gamma-exposure (GEX) estimate for index products (SPY/QQQ/SPX-adjacent) from option chains (delayed chains acceptable under the experimental flag), with a positive/negative-gamma regime tag. Rationale: market makers hedge option books in the underlying and futures, so aggregate dealer gamma shapes how index futures trade — positive-gamma regimes dampen moves (hedgers fade), negative-gamma regimes amplify them (hedgers chase). Mandatory caveats displayed: GEX is an *estimate* resting on dealer-positioning assumptions, computed from delayed open-interest data, and is a regime descriptor, not a signal. Also: **Income Strategy Analytics** — measurement support for premium-selling decisions (wheel-class: cash-secured puts, covered calls), never recommendations. Per ticker, from delayed chains under the experimental flag: annualized premium yield at standard deltas/DTE (e.g., 30-delta, 30 DTE), the same yield expressed against the stock's realized vol and IV rank ("how rich is the insurance you'd be selling — and is it rich because volatility is cheap or because the risk is real"), assignment-adjusted breakeven, and upside cap vs current price. Forbidden framings (grep-enforced): "get paid to wait," "income while doing nothing," "beat the market," "free income." Displayed alongside a fixed explainer: premium selling harvests the variance risk premium — insurance selling that collects most of the time and takes the tail; fat premiums are compensation for fat risks; the structure sells winners early (called away) and holds losers fully (assigned into declines).
**The empirical anchor (required before any synthetic backtest):** ingest CBOE's freely published **PUT (put-write) and BXM (buy-write) index** daily histories and render them against SPY total return — three-plus decades of official data on exactly the wheel-class claim, so "does systematic premium selling quietly beat the S&P" is answered by the benchmark chart, not by anyone's channel. A `wheel_synthetic` simulator template (Black–Scholes-priced legs from historical vol estimates) is permitted afterward as flagged research, with model-risk caveats printed on every report — synthetic option prices are estimates of estimates.
Slot in as `signals/options.py` + `/options` page.
- **Market Graph:** nodes = tracked symbols/themes/macro series; edges = |90-day return correlation| > 0.6 (recomputed weekly into `graph_edges`); frontend force-directed view; clicking a big mover highlights neighbors. Descriptive, not predictive — label it so.
- **Regime Analogs:** daily feature vector (VIX pct, HY spread pct, curve slope, breadth, SPY 21d momentum, DXY 21d, gold 21d); z-normalize; cosine similarity vs 15 years of history; surface top-5 analog dates + what followed, with heavy "descriptive only" labeling.
- **Sports odds:** line snapshots, movement, CLV, consensus-vs-movement divergence proxy. `ENABLE_SPORTS`.
- **ML Challenger Harness (Kronos-class):** an offline evaluation rig for time-series foundation models, plugged into the §7.16 challenger interface. Reference implementation: Kronos (MIT-licensed, open weights; `Kronos-small` 24.7M / `Kronos-base` 102M params — daily-cadence inference over a few hundred symbols is feasible on CPU or a small edge GPU, keeping Zero-Cost Mode intact; fine-tuning needs a real GPU — free Colab-class or owned hardware — and is optional). Protocol: (a) `kronos_topk` template — rank the ETF/scanner universe by model-predicted next-period return, long top-K, walk-forward with no lookahead (model context windows must end strictly before the prediction date), identical cost model to every other template; (b) report against the deterministic incumbents AND `spy_hold`; (c) publish the result either way — "an AAAI-class foundation model added/didn't add edge over my 40-line momentum rule after costs" is a first-rate research finding in both directions. Honest prior, stated in the report template: OHLCV-only forecasting of liquid US assets is the most efficient-market-hostile task in quant; the repo's own authors disclaim their backtest as a demo. Research extension note: the anomaly-detection inversion from decision 20 (model-implied distribution as a "normalcy" baseline; large realized divergence = flag) is the more FlowDesk-native use and may graduate to a live experimental signal only through the same evaluation gate.
- **Execution Plane / Research Companion (separate repo or monorepo service — revised decision 15 gates apply either way):** the consumer contemplated by decisions 15/21/23. Stage A: a ~small deterministic service that polls FlowDesk's `/api/v1` signal endpoints on a schedule, applies frozen rules from a strategy that passed the §7.16 backtest gate, routes simulated orders to a free broker paper API, and reports its equity curve vs SPY nightly. Stage B (graduation, only if Stage A behavior matches the backtest): reimplement the strategy on **NautilusTrader** (LGPL, free) — its event-driven engine supplies what FlowDesk's simulator deliberately omits (realistic fill/slippage modeling, order semantics, borrow/margin realism) for a high-fidelity re-test, and its research-to-live parity means the same strategy code later runs paper and, on a human decision, live via a broker adapter. FlowDesk provides signals and outcome scorecards; the companion provides execution truth; neither absorbs the other.
- **Orderflow & microstructure (routing note):** tick-level orderflow concepts — cumulative delta, absorption, speed of tape, footprint aggression, TPO — require bid/ask-attributed tick data that free 15-minute bars cannot honestly approximate; faking them from bars would be fabricated precision. Their correct home is the Execution Plane with paid tick data (Databento-class feeds; NautilusTrader consumes tick and order-book data natively), where they serve entry/exit research on strategies that already passed the signal gates. The one honest import into the Intelligence Plane: **bar-approximated volume profile** — distributing each historical bar's volume across its range to render composite volume-by-price (high/low-volume nodes, value area) as a chart overlay on symbol and pair pages, always labeled "approximate (bar-derived)". Location context, not tick truth. On the claim that any confirmation concept "always works": nothing always works, and in this codebase that phrase is spelled "scorecard with an n."
- **Geopolitical & Supply-Chain Context Feed (API-only integration):** an optional annotation layer sourcing OSINT-style event data — military conflict activity, shipping chokepoint disruption, export-control and sanctions actions, country instability scoring — from an external intelligence platform (reference: WorldMonitor's hosted REST/MCP API, or an equivalent; **hosted API only — its AGPL-3.0 source is barred from this tree by decision 24, while calling a hosted API triggers no copyleft obligation**). Purpose: **context annotation, never signal generation.** Events already emitted by FlowDesk (bottleneck states, theme moves, rotation events, sympathy events) may carry a geopolitical annotation when one temporally coincides with the affected theme's exposure — a Hormuz disruption annotating an Energy theme move, an export-control action annotating an AI Infrastructure segment. Hard rules: (a) annotations never create, gate, or score events on their own; (b) every annotation shows source and timestamp and is labeled *coincident, not causal*; (c) claims that news aggregation provides pre-news information are rejected outright — an aggregator is downstream of publication by construction, and any real latency edge lives in venue data, not RSS; (d) before promotion beyond annotation, the standard test applies: do events carrying geopolitical annotations show materially different outcome distributions (§7.15) than those without? If not, it is decoration and gets cut. Cost: free tier / usage-based; falls under §18 rather than Zero-Cost Mode if a key is required.
- **Portfolio Construction Layer (only once ≥2 strategies pass the §16 gates):** allocation across validated strategies rather than standalone evaluation — covariance estimation, correlation clustering of strategies, volatility targeting, risk budgeting (fractional-Kelly variants as descriptive inputs per decision 22), and maximum-exposure constraints per theme/sector/factor. Naive equal-weighting of correlated survivors is a standard blowup path; the §7.15 crowding detector is the seed of this layer, extended from signals to capital.
- **Research Knowledge Graph:** the accumulated research database (§7.17 rule 8) rendered as a linked graph — idea → hypothesis + economic rationale → signal version → backtest run → live events → outcomes → failure classification → related ideas → retest conditions. Queries it should answer after a year of use: which rationale classes (risk premium vs behavioral vs structural) produced durable results here; which assumptions failed most often; which datasets generated the strongest signals per unit of effort; which shelved ideas now meet their retest conditions. This is the meta-layer where the platform starts improving the researcher rather than only the strategy — and it is the component competitors cannot copy, because it is made of accumulated decisions, not code.
- **Decision Journal:** the user logs a decision (ticker/instrument, direction, thesis, one line) and FlowDesk auto-attaches the full signal context snapshot at that moment (regime, relevant rotation/bottleneck/pair/premium states, active tensions); the same outcome-scoring machinery as §7.15 later records realized return vs SPY, and a review view shows decisions grouped by what the context looked like — turning "what was I seeing when I made good vs bad calls" into a queryable dataset. Private, manual-entry, no advice, no execution; requires the Phase 3 auth layer only if multi-user.
- **Ticker Dossier:** one page per symbol (`/symbol/{ticker}`) aggregating everything FlowDesk already stores about it — RS ratio vs its sector ETF, theme memberships + roles, smart-money activity (congress/insider trades in the ticker), recent sympathy events touching it, scanner history, pair-watch entries, and current regime context. This is the "pre-trade research report" from discretionary playbooks reframed as data assembly: FlowDesk gathers the evidence; the user writes the conclusion. Read-side joins over existing tables only — no new ingestion.
- **Deal Spread Watch (manual entry):** the user enters announced deal terms (target ticker, cash offer per share or exchange ratio, expected close date); FlowDesk computes and tracks the deal spread % and annualized implied spread, with a persistent note that spreads price deal-break risk — wide spread = market doubt, not free return. No deal-feed ingestion (free sources are unreliable); manual entry only. Same forbidden-vocabulary rule as §7.5/§7.8.
- **Portfolio intelligence:** manual position entry first (no brokerage OAuth), exposure decomposition by sector/theme, concentration + correlation warnings. Requires real auth.

---

## 8. Data Model (PostgreSQL + TimescaleDB)

Alembic migrations. ⏱ = Timescale hypertable on `ts`.

```sql
-- Reference
symbols(id PK, ticker UNIQUE, name, asset_class, sector, role, active BOOL, meta JSONB)
providers_health(provider, feed, last_success_at, last_error_at, last_error TEXT,
                 PRIMARY KEY(provider, feed))
data_quarantine(id PK, provider, symbol, ts, reason TEXT,
                state TEXT,                    -- suspected|confirmed_bad|repaired|accepted_exception
                raw JSONB, quarantined_at)

-- Point-in-time, lineage & delivery foundation (M1–M3; decision 26)
fx_rates(base_currency, quote_currency, rate NUMERIC, observed_at, source,
         PRIMARY KEY(base_currency, quote_currency, observed_at)) ⏱
corporate_actions(id PK, symbol_id FK, action_type,            -- split|dividend|symbol_change
                  ex_date DATE, ratio NUMERIC, cash_amount NUMERIC,
                  source, source_id, available_at, ingested_at, confirmed BOOL)
ingestion_runs(id PK, provider, feed, started_at, finished_at, status,
               rows_read INT, rows_written INT, error TEXT)
source_watermarks(provider, feed, watermark_at, status, PRIMARY KEY(provider, feed))
data_snapshots(id PK, cutoff_at, status, components JSONB)     -- frozen watermark vector per feed
calculation_runs(id PK, signal_definition_id FK, snapshot_id FK,
                 config_version TEXT, code_commit_sha TEXT, ran_at)
signal_definitions(id PK, kind, version INT, definition JSONB, created_at,
                   UNIQUE(kind, version))
task_runs(id PK, task_name, scheduled_for, started_at, heartbeat_at, completed_at,
          status, attempt INT, error_class, error_message,
          input_watermark, output_watermark)
backfill_runs(id PK, feed, total_units INT, completed_units INT, current_unit TEXT,
              status, started_at, updated_at)
outbox_messages(id PK, event_id FK, created_at, dispatched_at, status)
notification_deliveries(id PK, outbox_id FK, channel,          -- discord|websocket|inapp|email
                        status,                                -- pending|delivered|retrying|permanently_failed
                        attempts INT, last_attempt_at, response TEXT)

-- Time series ⏱
bars(symbol_id FK, interval TEXT, ts TIMESTAMPTZ,
     o_raw,h_raw,l_raw,c_raw NUMERIC, v_raw BIGINT,          -- tradable prices (premiums, gaps)
     c_split_adj NUMERIC, c_total_return NUMERIC,            -- momentum / strategy returns
     v_split_adj BIGINT, adjustment_factor NUMERIC,
     dollar_volume NUMERIC, PRIMARY KEY(symbol_id, interval, ts))
macro_metrics(metric TEXT, effective_ts TIMESTAMPTZ, value NUMERIC,
              available_at TIMESTAMPTZ, revision INT DEFAULT 0, source TEXT,
              ingestion_run_id BIGINT,
              PRIMARY KEY(metric, effective_ts, revision))   -- revisions are new rows; vintages never overwritten
              -- backtests filter available_at <= simulated_clock, never effective_ts alone
etf_shares_outstanding(symbol_id, ts, shares BIGINT, PRIMARY KEY(symbol_id, ts))
breadth_metrics(metric TEXT, ts, value NUMERIC, PRIMARY KEY(metric, ts))

-- Rotation
rotation_scores(symbol_id, ts, score NUMERIC, z_mom10, z_mom21, z_vol, z_flow,
                components JSONB, PRIMARY KEY(symbol_id, ts)) ⏱
-- Unified event stream (ALL modules; see §4 rule) —
-- module specs saying "emit <kind>_event" mean an insert here
events(id PK, kind TEXT, schema_version INT,
       signal_definition_id FK, calculation_run_id FK, snapshot_id FK,
       config_version TEXT, code_commit_sha TEXT,
       occurred_at TIMESTAMPTZ, detected_at TIMESTAMPTZ,
       primary_subject_type TEXT, primary_subject_id TEXT,
       payload JSONB,                          -- validated by Pydantic model keyed (kind, schema_version)
       sentence TEXT,
       natural_key TEXT UNIQUE,                -- kind + specific subjects + trigger state + config_version
       episode_id BIGINT,                      -- §7.15 sample independence
       supersedes_event_id BIGINT NULL, status TEXT DEFAULT 'active')
event_subjects(event_id FK, subject_type TEXT, subject_id TEXT, role TEXT,
               PRIMARY KEY(event_id, subject_type, subject_id, role))

-- Smart money
politicians(id PK, name, chamber, party, state, external_ref UNIQUE,
            managed_systematic BOOL DEFAULT false)   -- set by high-volume rule (§7.3)
congress_trades(id PK, politician_id FK, ticker, side, amount_min, amount_max,
                transaction_date DATE, disclosure_date DATE,
                lag_days INT GENERATED ALWAYS AS (disclosure_date - transaction_date) STORED,
                filed_late BOOL GENERATED ALWAYS AS (disclosure_date - transaction_date > 30) STORED,
                source, external_id, raw JSONB, UNIQUE(source, external_id))
   -- congress_trades also holds executive-branch (OGE) filings via chamber='executive', source='oge'
insider_trades(id PK, insider_name, role, ticker, side, shares BIGINT, value NUMERIC,
               transaction_date DATE, filing_date DATE,
               lag_days INT GENERATED ALWAYS AS (filing_date - transaction_date) STORED,
               is_open_market BOOL, source, external_id, raw JSONB,
               UNIQUE(source, external_id))
-- smart-money clusters emit into the unified events stream (kinds: congress_cluster,
-- insider_cluster, conviction_overlap) with trade refs in payload and tickers in event_subjects

-- Cross-listing premiums (P1)
listing_pairs(id PK, slug UNIQUE, us_ticker, foreign_ticker, foreign_currency,
              adr_ratio NUMERIC,        -- ordinary shares per ADR (SKHY = 0.1)
              active BOOL DEFAULT true)
premium_snapshots(pair_id FK, ts, us_price NUMERIC, foreign_price_local NUMERIC,
                  fx_rate NUMERIC, premium_pct NUMERIC, premium_z NUMERIC,
                  us_session TEXT, foreign_session TEXT,   -- live|close
                  PRIMARY KEY(pair_id, ts)) ⏱

-- Regime (P1)
regime_history(ts PK, label TEXT,              -- risk_on|neutral|risk_off
               components JSONB)

-- Sympathy (P2)

-- Bottleneck segments (P2)
bottleneck_scores(theme_id FK, role TEXT, ts, score NUMERIC, state TEXT,
                  sessions_in_state INT, components JSONB,
                  PRIMARY KEY(theme_id, role, ts)) ⏱

-- Pair watch (P2)
pair_watchlist(id PK, slug UNIQUE, ticker_a, ticker_b, display_name, note, active BOOL DEFAULT true)
pair_snapshots(pair_id FK, ts, ratio NUMERIC, ratio_z NUMERIC, pctile_1y NUMERIC,
               corr_90d NUMERIC, PRIMARY KEY(pair_id, ts)) ⏱

-- Themes (P2)
themes(id PK, slug UNIQUE, name, description)
theme_members(theme_id FK, ref_type TEXT,      -- symbol|macro_metric
              ref TEXT, weight NUMERIC, inverse BOOL,
              role TEXT,                       -- core|supplier|network|optics|memory|foundry|power|operator
              valid_from DATE, valid_to DATE NULL,   -- effective dating: replay resolves membership
              confidence TEXT, source TEXT,          -- as of the simulated date, never today's basket
              PRIMARY KEY(theme_id, ref_type, ref, valid_from))
theme_scores(theme_id, ts, score NUMERIC, components JSONB, PRIMARY KEY(theme_id, ts)) ⏱

-- Expectations (P2)
prediction_markets(id PK, source, external_id UNIQUE, question, category, active BOOL)
prediction_snapshots(market_id FK, ts, probability NUMERIC, volume_usd NUMERIC,
                     PRIMARY KEY(market_id, ts)) ⏱

-- Scanner (P2)
scanner_universe(symbol_id PK, refreshed_at, float_shares, short_interest_pct, avg_dollar_vol,
                 amihud_illiq NUMERIC)          -- 21d mean |return| / dollar volume

-- Futures positioning (P2b, §7.3.1)
cot_positions(contract TEXT, report_date DATE,        -- Tuesday the positions reflect
              published_at DATE,                       -- Friday release = available_at
              category TEXT,                           -- commercial|non_commercial|non_reportable
              long_contracts BIGINT, short_contracts BIGINT, net_contracts BIGINT,
              net_z NUMERIC, pctile_3y NUMERIC,
              PRIMARY KEY(contract, report_date, category)) ⏱
scanner_snapshots(id PK, symbol_id, ts, score NUMERIC, rel_volume, gap_pct, components JSONB) ⏱

-- Crypto (P2)
crypto_metrics(asset TEXT, metric TEXT, ts, value NUMERIC, PRIMARY KEY(asset, metric, ts)) ⏱
crypto_etf_flows(asset, date DATE, net_flow_usd NUMERIC, source, PRIMARY KEY(asset, date))

-- Earnings (P2)
earnings_events(id PK, ticker, report_date DATE, period, eps_est, eps_actual,
                rev_est, rev_actual, surprise_pct NUMERIC,
                gap_pct NUMERIC, day_move_pct NUMERIC, implied_move_pct NUMERIC,  -- reaction layer
                divergence BOOL, extreme_reaction BOOL,
                source, UNIQUE(ticker, period))
earnings_debriefs(id PK, earnings_event_id FK UNIQUE, filing_url, guidance TEXT,
                  debrief TEXT, source_excerpts JSONB, model, created_at)

-- AI
ai_briefs(id PK, brief_date DATE UNIQUE, content TEXT, context JSONB, context_version TEXT,
          model, tokens_in INT, tokens_out INT, created_at)
ai_questions(id PK, question TEXT, answer TEXT, context_version TEXT, created_at)

-- Signal outcome tracking (P2)
event_outcomes(id PK, event_id FK REFERENCES events(id), instrument TEXT, benchmark TEXT,
               fwd_5 NUMERIC, fwd_21 NUMERIC,          -- relative to benchmark
               scored_at, UNIQUE(event_id, instrument))

-- Alerts
alert_rules(id PK, type TEXT, condition JSONB, channels TEXT[], enabled BOOL DEFAULT true)
alerts(id PK, rule_id FK, fingerprint TEXT UNIQUE, entity TEXT, payload JSONB,
       fired_at, delivered JSONB)

-- Phase 3 reserved: graph_edges, options_flow, odds_snapshots, portfolios
```

Retention: 1-min bars 90 days; 30-min bars 2 years; daily bars + macro + scores indefinite (Timescale retention policies + continuous aggregates rolling intraday→daily).

---

## 9. API Specification (FastAPI, prefix /api/v1)

Envelope for all responses: `{ "data": ..., "as_of": ISO8601, "stale": bool }`. Errors: RFC 7807 problem+json. Auth (P1): static bearer `APP_TOKEN` middleware; no user tables before Phase 3.

| Method & path | Purpose | Phase |
|---|---|---|
| GET /market/overview | All overview tiles + breadth + sentiment (cached) | 1 |
| GET /market/heatmap?scope=sectors | Heatmap payload | 1 |
| GET /market/breadth/history?days=365 | Breadth series | 1 |
| GET /rotation/scores | Ranked rotation table | 1 |
| GET /rotation/events?days=30 | Rotation events | 1 |
| GET /rotation/pair?a=&b= | RS ratio + both price series | 1 |
| GET /economy/overview | All economy/credit tiles | 1 |
| GET /economy/series/{metric}?years=2 | One macro series | 1 |
| GET /smartmoney/congress?days=&ticker=&politician= | Congress feed (paginated) | 1 |
| GET /smartmoney/insiders?days=&ticker= | Insider feed (paginated) | 1 |
| GET /smartmoney/clusters | Cluster events | 1 |
| GET /smartmoney/tickers/top?days=30 | Most-traded tickers | 1 |
| GET /smartmoney/politicians/{id} | Profile + est. performance | 1 |
| GET /premiums | All listing pairs, current premium + context | 1 |
| GET /premiums/{pair}/history?days=90 | Premium history series | 1 |
| GET /ai/brief?date=today | Cached daily brief | 1 |
| GET /alerts?since= · POST /alerts/rules · GET/DELETE /alerts/rules/{id} | Alerts | 1 |
| GET /healthz · GET /health/providers | Liveness + feed freshness | 1 |
| WS /ws | Live channel | 2 |
| GET /themes · GET /themes/{slug} | Themes index/detail | 2 |
| GET /pairs · GET /pairs/{slug}/history?days=365 | Pair Watch ratios + history | 2 |
| GET /sympathy/events?days=5 | Catalyst sympathy events | 2 |
| GET /bottlenecks · GET /bottlenecks/{theme}/{role}/history | Segment bottleneck states | 2 |
| GET /signals/scorecard | Per-event-type rolling outcome scorecards | 2 |
| GET /market/regime/history?days=365 | Regime badge history | 1 |
| GET /expectations · GET /expectations/{id}/history | Prediction markets | 2 |
| GET /scanner/top?limit=25 | Breakout leaderboard | 2 |
| GET /crypto/overview | Crypto panels | 2 |
| GET /earnings/calendar · GET /earnings/surprises?days=30 | Earnings | 2 |
| POST /ai/ask | Streaming Q&A (rate-limited) | 2 |

**Websocket:** client → `{"subscribe": ["market","rotation","alerts","themes"]}`; server → `{"channel","event","data"}`. Worker tasks publish to Redis channels; `ws.py` fans out. No other client→server commands.

---

## 10. Frontend Specification (Next.js App Router)

Pages:
- `/` **Command Center** — daily brief banner (sectioned, collapsible); market tiles + sentiment gauge; sector heatmap; breadth strip; rotation table (score-sorted, sparklines); premium badges strip (one per active listing pair); latest smart-money items (10); economy highlights strip; alerts sidebar.
- `/premiums` — per-pair panels per §7.5: premium badge + history chart + both legs + FX + ratio + the fixed "Why premiums persist" explainer.
- `/pairs` (P2) — Pair Watch per §7.8: ratio chart with ±2σ bands, both legs, correlation badge, event history.
- `/rotation` — full table; pair comparison chart (Lightweight Charts: RS ratio + both prices); events timeline.
- `/economy` — grouped tiles (Rates/Credit/Housing/Consumer/Inflation), each with sparkline + percentile + template sentence.
- `/smart-money` — tabbed Congress | Insiders | Clusters; filters; top-tickers chart; politician drawer. Lags always visible; performance figures labeled "est."
- `/themes` (P2) — ranked theme cards → `/themes/[slug]` detail (members, score history, related smart money, related expectations).
- `/expectations` (P2) — grouped probability cards with delta arrows + history charts.
- `/scanner` (P2) — leaderboard, score-breakdown popover, mini charts, prominent disclaimer.
- `/crypto` (P2) — per-asset panels, stance badges with reasons, ETF flow bars.
- `/earnings` (P2) — calendar + surprises.
- `/ask` (P2) — chat over /ai/ask, streamed; banner: "Answers are based on FlowDesk signals as of {as_of}."
- `/alerts` — history + typed rule-builder forms (no raw JSON in UI).

Cross-cutting rules: a fresh install shows a **first-run screen** (backfill progress per feed, from providers_health) instead of an empty dashboard until initial ingestion completes; every panel shows `as_of` + red stale badge when flagged; dark default, colorblind-safe direction colors; persistent footer "Informational only. Not investment advice."; TanStack Query with WS hook patching caches on push.

---

## 11. Background Jobs (Celery Beat, America/New_York)

| Task | Schedule | Phase |
|---|---|---|
| ingest_bars_intraday | */15 min 9:30–16:00 Mon–Fri in Zero-Cost Mode (*/1 min with paid provider); scanner universe same cadence in P2 | 1 |
| ingest_bars_daily (+backfill check) | 16:30 Mon–Fri | 1 |
| ingest_foreign_bars | */15 min during each configured foreign exchange's hours + at its close | 1 |
| ingest_fx | */15 min while any market open; daily ECB reference at 11:00 | 1 |
| compute_premiums | after each foreign/US/fx ingest tick | 1 |
| ingest_fred | 08:00 daily (FRED updates overnight) | 1 |
| ingest_cboe_pcr (+ VIX term structure, SKEW, implied correlation) | 18:30 Mon–Fri | 1 |
| ingest_cot | Fri 16:00 + Sat catch-up retry | 2 |
| ingest_etf_shares | 18:00 Mon–Fri | 1 |
| ingest_congress | every 2 h | 1 |
| ingest_insider | every 2 h | 1 |
| ingest_executive (OGE) | daily 07:30 | 2 |
| compute_breadth | after daily bars; hourly intraday | 1 |
| compute_regime | after each compute_breadth run | 1 |
| compute_rotation | after daily bars; every 30 min intraday | 1 |
| compute_smartmoney_clusters | after each smart-money ingest | 1 |
| generate_ai_brief | 08:30 Mon–Fri | 1 |
| compute_alerts | chained after every compute task | 1 |
| ingest_predictions | every 6 h | 2 |
| compute_themes | after daily bars; every 30 min intraday | 2 |
| compute_pairs | after daily bars; every 30 min intraday | 2 |
| compute_sympathy | each intraday bars tick (theme universe) | 2 |
| compute_bottlenecks | after daily bars (daily cadence is sufficient — it is a persistence signal) | 2 |
| score_event_outcomes | after daily bars (scores events whose t+5/t+21 windows completed) | 2 |
| ingest_crypto (+ ETF flows 09:00) | */15 min | 2 |
| compute_scanner | */5 min market hours | 2 |
| ingest_earnings | 07:00 daily | 2 |
| compute_earnings_reactions | after daily bars (reports from prior session) | 2 |
| ingest_8k_debrief | after each report detected (EDGAR poll; LLM call only if ENABLE_AI) | 2 |
| refresh_scanner_universe | Sun 12:00 | 2 |

Task rules: max_retries=3 exponential backoff; per-task Redis lock (no overlap); every outcome updates providers_health.
**Ops heartbeat (dead-man switch):** a `heartbeat` task posts a one-line daily status to the Discord webhook (feeds green/stale, quarantine count, last brief generated); additionally, if the daily brief has not generated by 09:00 ET or any Phase 1 feed is stale past threshold, an explicit failure alert fires. Silence must be impossible to mistake for health.

---

## 12. Configuration

Single `.env` via pydantic-settings. Required (P1, Zero-Cost defaults): `DATABASE_URL, REDIS_URL, APP_TOKEN, FRED_API_KEY (free), TZ=America/New_York`. Defaults that need no keys: `ZERO_COST_MODE=true, MARKET_DATA_PROVIDER=yfinance, FX_PROVIDER=frankfurter, SMARTMONEY_PROVIDER=stockwatcher+edgar, ENABLE_AI=false`. Optional: `DISCORD_WEBHOOK_URL` (alerts), `ANTHROPIC_API_KEY` (only if ENABLE_AI=true), paid provider keys (`POLYGON_API_KEY`, `FMP_API_KEY`, `EMAIL_API_KEY`) which flip the corresponding provider setting. Flags: `ENABLE_AI, ENABLE_THEMES, ENABLE_EXPECTATIONS, ENABLE_SCANNER, ENABLE_CRYPTO, ENABLE_EARNINGS, ENABLE_AI_ASK, ENABLE_SPORTS, ENABLE_OPTIONS`. All universes, `LISTING_PAIRS` (§6.10), `PAIR_WATCHLIST` (§7.8), theme definitions, signal weights, thresholds, FRED series list, and template sentences live in `config.py` defaults (env-overridable) — never inline in logic.

---

## 13. Deployment

docker-compose services: `frontend, api, worker, beat, postgres (timescale/timescaledb image), redis, caddy (TLS)`. **Zero-cost hosting (default):** the developer's own always-on machine, or an Oracle Cloud Always Free ARM VM (4 OCPU / 24 GB — verify the free tier still exists at build time; any equivalent free tier is acceptable). Multi-arch images (amd64+arm64) are therefore required. CI (GitHub Actions free tier): ruff + mypy + eslint + tsc + pytest + vitest → build images → SSH deploy `docker compose pull && up -d`; pin third-party Actions to commit SHAs, pin all dependency lock files, and run a dependency license audit that fails on AGPL/unknown licenses per decision 24 (cheap supply-chain and IP hygiene borrowed from mature trading-infra projects). Nightly `pg_dump` to a local disk path or free-tier object storage. Paid VPS is an optional upgrade. Documented-not-built paths: Kafka only if feed volume grows ~100×; managed Postgres if ops burden grows; K8s never for this project.

---

## 14. Security, Compliance, Cost

**Two explicit deployment modes (choose one; set by compose profile):**
- **Private mode (default):** API bound to the private network / behind Caddy basic auth; the static APP_TOKEN never reaches browser JavaScript; Postgres and Redis never exposed; mutations (alert rules, admin) allowed.
- **Public portfolio mode (required before ANY internet exposure):** anonymous routes read-only; separate authenticated admin routes with HTTP-only secure session cookie + CSRF protection; strict CORS; WebSocket origin validation; request-size limits and per-route rate limits; admin audit log; alert-rule mutation disabled for anonymous users; /ai/ask disabled or heavily restricted. A static bearer token in a public browser app is a published credential — never ship it.

- All provider keys server-side only; the browser never calls data providers.
- **Budget: $0/month in default configuration.** The only possible spend is the optional LLM (`ENABLE_AI=true`), controlled by a small model class, one brief/day, cached Q&A, and a Redis daily token-budget counter that hard-stops LLM calls when exceeded (default cap set to roughly cents/day).
- Respect provider ToS on redistribution — personal/portfolio use; check terms before any public deployment.
- Disclaimers: persistent footer, scanner page, AI system prompts ("informational only, not investment advice").
- Prediction-market data presented as market expectations, not gambling content. Sports module requires legal review before public enablement.

---

## 15. Testing & Acceptance

**Unit (pytest):** every `signals/` function against hand-computed fixtures — e.g., synthetic bars where SMH falls 5% and IGV rises 2% over 10 sessions MUST yield a rotation event with spread ≈ 7%; a synthetic breadth set with SPY up and 30% above 50DMA MUST yield breadth_warning; HY OAS series rising 60 bps in 21 sessions MUST yield credit_stress; the §7.5 worked premium example (₩2,186,000 / 1,390 KRW/USD / ratio 0.1 / ADR $152 → −3.3%) MUST reproduce exactly, plus a non-0.1-ratio fixture; a template-grep test MUST fail if any premium-module template contains "arbitrage opportunity," "free money," "guaranteed," or "riskless." Normalizers tested against recorded raw payloads (vcr/respx cassettes). Fingerprint dedup tested.
**Property & golden tests:** property-based tests (hypothesis-class) on all `signals/` functions — scores stay within declared bounds, z-scores are shift/scale sane, no NaN propagation on gappy input; plus golden-snapshot fixtures (frozen real bar windows with committed expected outputs) so numerical drift in refactors fails loudly. Each §6.12 bar-validation rule gets a fixture that MUST quarantine.
**Integration:** compose test profile; ingest→compute→API round trips with mocked providers; WS event published by a worker reaches a test client.
**AI:** context builder output validated against JSON Schema and ≤ token budget; with a mocked LLM, assert the prompt contains no raw bar arrays; regression test that every numeral in a sample brief exists in its context JSON.
**MVP acceptance checklist:**
1. Fresh clone + `.env` + `docker compose up` → populated dashboard within 30 min, zero manual DB steps.
2. Rotation table shows the full universe with scores; pair chart renders.
3. Economy dashboard shows all §6.4 series with correct latest values vs FRED website spot-check.
4. Congress + insider feeds show real filings with visible lags.
5. Premium panel shows every configured pair with FX-adjusted premium, correct session labels, and the persistence explainer; SKHY pair spot-checks against manual calculation.
6. Daily brief generates on schedule with `ENABLE_AI=false` (template mode); stored context contains every number stated; flipping `ENABLE_AI=true` swaps composition without other changes.
7. Killing all provider access → app serves last-known data with stale badges, no 500s.
7b. A `pg_dump` backup restores cleanly to a fresh container and the app serves from it (restore drill — an untested backup is a hope, not a backup).
7c. A synthetic 2:1 split inserted into fixtures is detected, history recomputed, and the dilution flag does NOT fire.
8. `congress_ticker=NVDA` rule fires at most once per qualifying day, delivered in-app + Discord.
9. Running in default configuration incurs $0 in provider, hosting, and AI costs.

---

## 16. Implementation Plan & Release Gates (the single normative sequence — §3 is the strategic map; where they appear to differ, this section governs)

**Operating principles:** build one trustworthy end-to-end vertical slice before expanding breadth; historical replay and live calculation consume the same point-in-time snapshot interface; every computed artifact traces to code SHA, config version, and data snapshot; a feature is complete when ingestion, validation, storage, calculation, API, UI, observability, recovery, and tests all pass — not when its page renders; Postgres owns all durable state (Redis is never the source of truth); each milestone ends with a running deployment and an explicit exit gate; estimates are effort budgets — a milestone exceeding twice its budget triggers scope cuts (§3 cut order), never integrity cuts. Each milestone should also put something visible on screen — solo projects die in invisible plumbing.

**Releases:** v0.0 Trusted Data Foundation → v0.1 Core MVP (Overview, Economy, Rotation, one premium pair, deterministic brief, alerts, live outcome tracking) → v0.2 Full Phase 1 (smart money, all pairs, optional LLM) → v0.3 Research Foundation (themes, pair watch, simulator).

**M0 — Architecture freeze & proof spikes (1 weekend).** Write short ADRs for: raw-vs-adjusted price policy; point-in-time observation model; FX base/quote convention; snapshot/watermark policy; event natural keys & episodes; delivery semantics; private-vs-public deployment mode. Executable draft migrations (not pseudocode). Proof scripts: one yfinance daily batch, one FRED series with revision check, one foreign listing + typed FX conversion, one EDGAR request — verifying payload shapes, timestamps, adjustment fields, rate budgets. *Gate:* ADRs written; schema carries source/effective/available/ingested/revision where required; the §7.5 worked FX example passes through the typed convert(); zero ambiguity remains on raw vs adjusted.
*Status:* **Spikes 1–2 COMPLETE, 2026-07-31 — both passed with findings. Spike 1 (foreign listing + FX):** All three fetches succeeded; FX direction verified and recorded in §6.10; session-mismatch discovered, quantified, and resolved by the pairing rule in §7.5; premium arithmetic independently reproduced. Open item carried forward: confirm the 0.1 ADR ratio against the depositary bank's own documentation before the M7 slice. **Spike 2 (yfinance price basis):** falsified the assumption that `auto_adjust=False` yields raw prices — it does not; true as-traded prices require split-factor reconstruction, total-return columns are unstable across re-fetches (idempotency now scoped to raw/split-adjusted only), column sets vary by security type, and unknown/delisted tickers fail silently with an empty frame. All recorded in §6.12. **Spike 3 (FRED vintages):** confirmed that point-in-time replay is implementable — revisions are real, the observation set itself grows over time, and both the as-of-date query and per-observation publication dates are retrievable (with a widened real-time window). Recorded in §6.4. **Spike 4 (EDGAR):** access confirmed — contact User-Agent mandatory (403 without), CIK is the durable identity, the `recent` batch is capped and must be paged for full history, disclosure lag computable from the index (Form 4: 4–5 days; 8-K: 3–6 days), and Form 4 transaction dates require XML parsing. Recorded in §6.5.

**M0 COMPLETE (2026-07-31): all four proof spikes executed, three architecture corrections issued (v4.9.1–v4.9.5), zero blocking failures.** Every provider the Phase 1 design depends on is reachable and behaves as now documented. Remaining M0 deliverables: write the seven ADRs from this evidence, and record the hand-computed oracle fixtures. Then M1.

**Status note (v4.9.9): M0 is NOT closed.** The four spikes passed on 2026-07-31; the two remaining deliverables have been outstanding since. Both are transcription from evidence already gathered, not research — the ADRs restate decisions the spikes settled, and the oracle fixtures record numbers already hand-computed in §7.5. Stating the cost plainly, because the freeze exists to prevent exactly this drift: §3 names Signal Outcome Tracking as the moat and notes that *every week of delay is unrecoverable*, and the interim practice below exists because that clock is running whether or not M5 has shipped. **Nothing in this document is blocked on further design work. The remaining risk is entirely execution risk, as the freeze declaration states.**

**M1 — Platform & ops foundation (2 weekends).** Monorepo + compose stack; Alembic baseline + migration smoke test; structured logging with correlation IDs; /health/live·ready·data·providers; task_runs, ingestion_runs, backfill_runs, providers_health; private/public deployment profiles; full CI (lint, types, tests, migration smoke, vulnerability scan, license audit); backup/restore scripts; **external** dead-man heartbeat (free ping-monitor service — the monitor must live outside the monitored box). *Gate:* fresh clone starts with one command; migrations apply clean; backup restores clean; killing Redis/worker/provider produces the documented health state; Postgres/Redis never externally exposed; public profile exposes read-only routes only. First visible output: the health page.

**M2 — Point-in-time ingestion & data quality (3 weekends).** Reference tables (symbols, exchanges, currencies, provider mappings, trading calendars); canonical observations with provenance (effective_at, available_at, ingested_at, revision, source, ingestion_run_id) on revision-prone feeds (macro, disclosures); raw + split-adjusted + total-return price fields; corporate_actions with confirmed-action workflow (detector flags → human/authoritative confirmation → versioned canonical rewrite, raw history immutable); quarantine states (suspected/confirmed-bad/repaired/accepted-exception) over the full §6.12 validation list; calendar-aware session math; rate-budget enforcement; first-run progress API + screen from backfill_runs. *Gate:* re-ingesting identical payloads yields identical canonical rows; synthetic split ≠ dilution; extreme move quarantines, never silently accepts; confirmed split creates a new data version without deleting raw; holiday/half-day fixtures pass; backfill progress survives worker restart.

**Effective-dated universe membership (added v4.9.10 — closes a look-ahead hole, same class as decision 26).** Delisting handling preserves the *bars* of dead symbols, but a backtest that asks "S&P 500 breadth on 2020-03-10" while reading **today's** constituent list still leaks survivorship through the membership itself — over perfectly clean bars. Any universe used historically (index constituents for breadth, scanner universes, theme baskets, ETF holdings where used) is stored as membership intervals — `universe_membership(entity_id, universe_id, valid_from, valid_to, source, available_at)` — and every historical query resolves membership **as of the queried date**, availability-filtered like any other revision-prone feed. Theme membership is already effective-dated (v4.8h); this extends the same treatment to every universe. Free-source constituent history is incomplete, so until a proper archive exists, membership-dependent backtests print the same upper-bound warning the scanner already carries — the prospective fix is §7.15 outcome tracking, which is survivorship-free by construction. **Same milestone, one provenance line:** every market observation also records `provider / feed / venue_scope` (e.g. consolidated vs single-venue), so a strategy evaluated on one market view can never be silently compared against another.

**M3 — Snapshot, lineage & event infrastructure (2 weekends).** source_watermarks; data_snapshots (frozen watermark vector; brief and every compute run reference one); calculation_runs (code SHA + config_version + snapshot_id); signal_definitions; unified versioned events + event_subjects + payload validation by (kind, schema_version); natural-key + episode dedup framework; transactional outbox_messages + notification_deliveries. *Gate:* one Postgres transaction creates result + event + outbox row; killing Redis after commit loses nothing; dispatcher restart delivers pending exactly once; every event names its snapshot, calculation, SHA, config; invalid payloads fail before insert.

**M4 — First vertical slice: Rotation (2 weekends).** ETF/SPY canonical history → pure RotationScore → calculation service on snapshots → events → API + page → template sentence → alert via outbox → outcome-tracking rows from the first event → minimal replay using the same signal function. Tests: hand-computed fixture, property, golden window, missing-bar, split boundary, holiday boundary, idempotent re-run, no-look-ahead replay. *Gate:* live and replay match on the same snapshot; every displayed score links to its calculation run; one event appears in API, alert history, and outcome queue; reprocessing creates no duplicates; a threshold or code change creates a new signal version.

**M5 — Outcome tracking & minimal research harness (1–2 weekends).** Flexible horizons with explicit entry/exit timestamp conventions; independent **episodes** (condition activates → clears; intra-episode repeats are not new observations); absolute + benchmark-relative returns; scorecards showing raw count, episode count, hit rate, mean/median, bootstrap CI, overlap %, regime breakdown; live scorecards frozen per signal version and **never rewritten by replay**. *Gate:* synthetic event produces exact returns; overlapping events group into one episode; current/prior configs display separately; replay never touches the live scorecard; reports state survivorship limits.

**Interim outcome logging before M5 (added v4.9.9 — a practice, not a feature; requires no code and changes no spec).** M5 is where FlowDesk begins accumulating its own evidence, and until it ships the evidence clock is stopped. The owner's own trades are the one data source available now that no delay can recover later. Keep a plain dated file — one row per decision, recording: date/time, instrument, thesis in one sentence, **idea source** (matching the §7.11c source-tagging vocabulary so rows import cleanly later), the invalidation level chosen *before* entry, position size, exit, and outcome. The §7.11c fields are the schema; a spreadsheet is an acceptable implementation of it. Two rules make the rows worth keeping: **write the thesis and invalidation before the outcome is known** (a thesis recorded after the fact is a memory, not an observation), and **log the trades that went wrong at the same rate as the ones that went right** — the §7.16 graveyard principle applied to the human. This is the Phase 3 Decision Journal (§7.17) done by hand; when that module ships it ingests these rows rather than starting from zero.

**M5.5 — Swing Desk (2 weekends; promoted at owner's direction, v4.9.6).** Scanner universe
build + swing scoring extension (§7.11d), setup geometry (§7.11a) with the forbidden-vocabulary
grep test, risk calculator (§7.11b) that computes only from user-supplied inputs, idea-source
tagging wired into the §7.16 outcome pipeline (§7.11c), red-flag panel, and a `/swing` page.
*Gate:* every displayed geometric measure reproduces a hand-computed fixture; the risk
calculator refuses to output when invalidation or risk budget is absent; a logged idea appears
in the outcome queue tagged with its source; no template contains recommendation vocabulary.

**M6 — Economy & Market Overview (2 weekends).** FRED with first-seen + revision vintages (revisions never overwrite earlier vintages); series metadata (unit, frequency, cadence); Economy dashboard; daily constituent breadth + sector-level intraday; Regime Badge; sentiment composite; partial-snapshot rendering; breadth_warning/credit_stress/regime_change events. *Gate:* values match source spot checks; replay sees only what was available_at the simulated time; degraded feeds degrade only their components.

**M7 — Premium Monitor slice (2 weekends).** ONE pair (SKHY/KRX); effective-dated ADR-ratio records; typed FX conversion; per-input timestamps (US price, foreign price, FX) each displayed; **raw-price** premium math; session-mismatch labels; history + events; persistence explainer; manual reconciliation. *Gate:* worked example reproduces exactly; FX inversion tests pass; an ADR-ratio change never corrupts prior history; the system refuses to compute when currency/ratio metadata is ambiguous.

**M8 — Brief & alert product layer (2 weekends).** Snapshot-based context builder with deterministic tension detection; deterministic brief default; optional LLM output as **structured claims with evidence paths** into the context JSON, validated for path-existence, entity, unit, sign/direction, and freshness before rendering to prose (any failure → template brief + logged claim); required Tensions section (prompt in §17.3 updated to match); typed alert rules; in-app + Discord via outbox with per-channel delivery states and a retry view; brief dead-man alert. *Gate:* every claim resolves to evidence; invalid LLM output falls back cleanly; killing Discord loses nothing; retries never duplicate in-app alerts; partial snapshots are disclosed in the brief. **Tag v0.1-core-mvp.**

**M9 — Soak (14 consecutive days, unattended).** Targets: no manual DB repair, no unexplained signal revisions, no lost scheduled tasks, no duplicate external notifications, every failure visible in health, every feed fresh-or-explicitly-degraded, one successful restore drill, quarantine reviewed via documented workflow. Any failure remediates and resets the affected soak clock.

**M10 — Smart Money & remaining Phase 1 (2–3 weekends).** Congress + Form 4 with public-availability timestamps; symbol-resolution workflow (unresolved tickers quarantine, never guessed); open-market vs plan classification; clusters via the unified stream; disclosure-date-based outcome tracking (backtests enter no earlier than public availability); all verified listing pairs; optional LLM composition behind budget + grounding gates. *Gate:* both dates preserved everywhere; high-volume filer policy enforced; cluster payloads schema-validate. **Tag v0.2-full-phase-1.**

**Phase 2a (Research Foundation → v0.3), in order: Themes → Pair Watch → full Strategy Simulator → WebSockets** (delivery polish never precedes research correctness). Gate: replay reports for themes/pairs; simulator runs on point-in-time snapshots with versioned universes; another 14-day soak.

**Phase 2b, strictly one module at a time:** Bottleneck → Earnings → Sympathy → **Futures Positioning (COT, §7.3.1)** → Executive disclosures. (Scanner has moved to M5.5 as the Swing Desk.) Each ships with: signal definition, event schema, replay test, live scorecard, outcome convention, failure modes, data-quality checks, user-facing limitations. No second module starts before the first has emitted and scored real live events.

**Phase 2c (only under regular personal use):** Expectations → Crypto → Ask-the-Market.

**Cut order under time pressure:** Ask-the-Market → Crypto → Expectations → Executive disclosures → Scanner. **Never cut:** point-in-time data, snapshots, lineage, outcome tracking, replay correctness, security, recovery, data-quality enforcement.

**Honest effort totals (solo, 8–12 hrs/week):** v0.0 foundation 6–8 weekends · v0.1 Core MVP +7–9 weekends · soak 2 weeks · v0.2 +2–4 weekends → **≈ 4–5 months to a credible Full Phase 1.** Release the Core MVP first; never hold usable output hostage to disclosure ingestion.

---

## 17. Agent Guardrails (for smaller/weaker implementing agents — follow literally)

### 17.1 Hard rules
- Do not reorder the build order. Do not start a step before the previous step's tests pass.
- Do not add: Kafka, Kubernetes, microservices, GraphQL, user accounts (pre-Phase 3), social scraping, an agent framework, or any feature failing the §1 admission test.
- `signals/` = pure functions only. If you are about to import a DB client or HTTP library inside `signals/`, stop — that code belongs in `ingest/` or `services/`.
- If a provider endpoint no longer matches this doc, keep the interface and DB schema fixed and adapt only the client internals. If a provider is entirely unavailable, use the documented fallback; if none exists, stub the ingest, mark the feed stale, and continue — never block the build.
- Every number shown in UI or brief must exist in Postgres first. No computing values ad hoc in API handlers.
- Never integrate a broker API, order routing, or any trade-execution code, in any phase, regardless of user-story framing (§2.15).
- Never emit the phrases "arbitrage opportunity," "free money," "guaranteed," or "riskless" in any UI text, template, or prompt. The Premium Monitor describes dislocations and their persistence factors only (§2.14, §7.5).
- Never add a dependency, provider, or service with a mandatory recurring cost while `ZERO_COST_MODE=true` (§2.13). If a desired feature can't be built free, stub it behind a flag and document the paid path.
- When ambiguous, choose the simpler option and leave a `# DECISION:` comment.

### 17.2 Reference pseudocode — RotationScore (normative)

```python
def rotation_score(closes: dict[str, np.ndarray],      # daily closes incl. benchmark, len >= 260
                   dollar_vol: np.ndarray,              # ETF daily dollar volume
                   shares_out: np.ndarray | None,       # ETF shares outstanding (daily), optional
                   cfg: RotationCfg) -> RotationResult:
    rs = closes["etf"] / closes["SPY"]
    log_rs = np.log(rs)
    slope10 = ols_slope(log_rs[-10:]); slope21 = ols_slope(log_rs[-21:])
    hist10 = rolling_ols_slopes(log_rs, 10)[-252:]      # trailing year of 10d slopes
    hist21 = rolling_ols_slopes(log_rs, 21)[-252:]
    z10, z21 = zscore(slope10, hist10), zscore(slope21, hist21)
    vol_ratio_series = rolling_mean(dollar_vol, 10) / rolling_mean(dollar_vol, 63)
    z_vol = zscore(vol_ratio_series[-1], vol_ratio_series[-252:])
    if shares_out is not None:
        flow_series = pct_change(shares_out, periods=5)
        z_flow = zscore(flow_series[-1], flow_series[-252:])
        w = cfg.weights                                  # (.40,.25,.20,.15)
    else:
        z_flow, w = 0.0, renormalize(cfg.weights, drop="flow")
    raw = w.mom10*z10 + w.mom21*z21 + w.vol*z_vol + w.flow*z_flow
    return RotationResult(score=100*np.tanh(raw), z_mom10=z10, z_mom21=z21,
                          z_vol=z_vol, z_flow=z_flow)
```

Rotation event check (run over all ordered pairs after scoring):
```python
spread = ret(winner, 10) - ret(loser, 10)
if spread > cfg.min_spread and score[winner] > cfg.win_min and score[loser] < cfg.lose_max:
    emit(rotation_event(loser, winner, spread, sentence=template(loser, winner, spread)))
```

### 17.3 Verbatim LLM prompts (do not paraphrase)

**Daily brief — system prompt:**
```
You are FlowDesk's market flow analyst. You will receive a JSON object of
pre-computed market signals. Write a daily brief with exactly these sections:
Dominant Theme, Markets, Economy & Credit, Smart Money, Expectations, Tensions, Watch Items.
Return structured JSON claims with evidence_paths into the provided context (see
context_schema.json); prose is rendered only from validated claims.
Rules:
- Use ONLY facts and numbers present in the JSON. Never invent, estimate, or
  extrapolate a number. If a section's data is marked "stale": true, say so.
- 250-400 words total. Lead with the single most important cross-market theme.
- Plain English. No hedging boilerplate. No investment advice, price targets,
  or buy/sell language. Congressional and insider data reflects past trades
  disclosed with a delay; describe it that way.
- If two signals conflict (e.g., equities risk-on but credit spreads widening),
  say so explicitly - conflicts are the most valuable content.
```
**User message:** `Today's signal context:\n<context>{CONTEXT_JSON}</context>\nWrite today's brief.`

**Ask-the-Market — system prompt:** same rules, plus:
```
Answer the user's question using ONLY the JSON context. If the question needs
data outside the context (individual stock fundamentals, future prices, news
events, anything not present), reply that FlowDesk does not track that signal
and name the nearest signal it does track. Maximum 200 words.
```

### 17.4 Context JSON example (abbreviated; full JSON Schema lives at `backend/app/ai/context_schema.json`)

```json
{
  "context_version": "2026-07-07T13:30Z",
  "market": {"spy_day_pct": 0.42, "vix": 14.8, "put_call": 0.87, "regime": "neutral",
             "sentiment_composite": 63,
             "breadth": {"pct_above_50dma": 38.5, "warning": true}},
  "rotation": {"top": [{"ticker": "IGV", "score": 41}, {"ticker": "XLU", "score": 28}],
               "bottom": [{"ticker": "SMH", "score": -34}],
               "events": [{"sentence": "Capital rotation detected: out of Semiconductors (SMH, -34) into Software (IGV, +41). IGV has outperformed SMH by 4.2% over 10 sessions on above-average volume."}]},
  "economy": {"hy_oas_bps": 312, "hy_oas_21d_delta_bps": 18, "credit_stress": false,
              "mortgage_30y": 6.12, "mortgage_21d_delta_bps": -18, "t10y2y": 0.34},
  "smart_money": {"clusters": [{"kind": "congress_cluster", "entity": "Information Technology",
                                "side": "buy", "actor_count": 4}],
                  "top_disclosures": [{"who": "Rep. X", "ticker": "PANW", "side": "buy",
                                       "amount_range": "15001-50000", "lag_days": 31}]},
  "premiums": {"pairs": [{"slug": "skhy-krx", "premium_pct": 14.2, "premium_z": 2.3,
                          "delta_5d": 6.1, "foreign_leg": "at_close"}]},
  "expectations": {"stale": false,
                   "moves": [{"question": "Fed cuts rates at September meeting",
                              "probability": 0.68, "delta_7d": 0.12}]},
  "themes": {"movers": [{"slug": "cybersecurity", "score": 44, "delta_5d": 27}]},
  "crypto": {"stale": true}
}
```

### 17.5 Glossary (so weaker agents don't misinterpret domain terms)
- **RS (relative strength):** price ratio of an asset vs a benchmark; rising RS = outperformance. Not RSI.
- **OAS (option-adjusted spread):** corporate bond yield premium over Treasuries; widening = rising credit stress.
- **Creation/redemption (ETF):** shares outstanding rise when money flows in; a legitimate flow proxy.
- **Form 4:** SEC filing insiders submit within 2 business days of trading their own company's stock.
- **STOCK Act lag:** members of Congress may disclose up to 45 days after a trade — hence "disclosure tracker," never "real-time."
- **Breadth:** how many stocks participate in a move; narrow breadth = fragile rally.
- **CLV (closing line value):** sports-betting metric; only relevant if Phase 3 sports is built.
- **Basis points (bps):** 1 bp = 0.01%.
- **ADR (American Depositary Receipt):** a US-traded certificate representing foreign shares held by a depositary bank. The **ADR ratio** is ordinary shares per ADR (SKHY: 10 ADRs = 1 Korean share, ratio 0.1).
- **Cross-listing premium:** the FX-adjusted price gap between two listings of the same company. NOT automatically an arbitrage: conversion limits (fungibility), borrow cost/availability, home-market short-sale rules, FX risk, and index flows can keep it open indefinitely or widen it before it narrows.
- **Fungibility:** whether ADRs and home-market shares can actually be converted into each other. Limited fungibility = weak convergence mechanism = premiums persist.
- **Borrow cost:** the fee to borrow shares for shorting; on hot new listings it can be large enough to consume an apparent premium entirely.
- **Market neutral:** a long/short construction with equal dollar long and short exposure, so returns come from *ranking* skill rather than market direction; verified in the simulator by |beta vs SPY| < 0.2.
- **Sharpe ratio:** (strategy return − risk-free rate) ÷ return volatility; the standard risk-adjusted return metric. Risk-free rate from FRED `DTB3`.
- **Beta:** sensitivity of a strategy's returns to the benchmark's returns (regression slope vs SPY daily returns).
- **First-order / second-order beneficiary:** on a catalyst, the first-order name is the subject of the headline; second-order names are its suppliers, networkers, foundries, power providers, and hosts. FlowDesk surfaces the *least-moved* second-order names as open repricing questions.
- **Regime (Risk-On/Neutral/Risk-Off):** a deterministic label over breadth, VIX percentile, credit-spread delta, and sector participation. Descriptive context, not an instruction.
- **OGE PTR / Form 278e:** executive-branch financial disclosures (periodic transaction reports and annual filings) — range-banded values, often late, often silent on security type; the executive analogue of congressional STOCK Act filings.
- **Direct indexing:** owning an index as individual stocks so a manager can harvest tax losses; produces enormous mechanical trade counts that carry no stock-picking signal.
- **Look-ahead bias:** using information at a point in time when it wasn't yet public (e.g., simulating entry at a trade's transaction date instead of its disclosure date); it silently inflates backtest results.
- **Supply bottleneck (economic):** a link in a supply chain where demand exceeds capacity, giving every producer in that link pricing power simultaneously — which is why genuine bottlenecks reprice a whole segment (high breadth, low dispersion) rather than one stock.
- **Supply response:** new capacity arriving to relieve a bottleneck (fabs, fiber plants, transformer factories); it ends scarcity repricing and is what the `Easing` state watches for.
- **Dispersion (cross-member):** the spread of returns across a segment's members; low dispersion during outperformance = broad repricing, high dispersion = single-winner story.
- **TSFM (time-series foundation model):** a large model pre-trained on many time series (Kronos: OHLCV "K-lines" from 45+ exchanges) intended to transfer to forecasting tasks. In FlowDesk it is a challenger to be evaluated, never a displayed oracle.
- **Walk-forward validation:** evaluating a model only on data strictly after its input window, rolling forward through history — the minimum standard for honest forecast evaluation; anything else leaks the future.
- **Signal decay:** a signal's historical edge shrinking over time as it becomes crowded or the regime changes; detected by trending outcome scorecards, not by faith.
- **Attribution:** knowing exactly which rule produced a result. Deterministic signals have it; LLM-agent decisions don't — which is why FlowDesk evaluates the former and rejects the latter as decision-makers.
- **Straddle / implied move:** buying the ATM call and put at one strike; total premium ÷ spot ≈ the move the options market expects. Breakeven = strike ± total premium — the position profits only if realized movement exceeds what was paid for, making it a bet that realized volatility beats implied, not a direction-proof trade.
- **Variance risk premium (VRP):** the persistent tendency of implied volatility to exceed subsequently realized volatility — option buyers pay an insurance premium on average, option sellers collect it (with tail risk).
- **IV crush:** the collapse of implied volatility immediately after a known event (earnings) resolves; a straddle can lose money even when the stock moves, if the move is smaller than implied.
- **Kelly criterion:** the bet fraction that maximizes long-run log growth given a known win rate and payout ratio. With *estimated* probabilities, full Kelly systematically overbets; fractional Kelly is the practitioner norm. In FlowDesk it is a backtest report statistic, never a sizing instruction.
- **Research–live parity:** the property that identical code produces signals (or trades) in research and production, eliminating reimplementation drift between backtest and reality. FlowDesk achieves it for signals via pure functions; NautilusTrader-class engines achieve it for execution.
- **Event-driven vs vectorized backtesting:** vectorized backtests compute over whole arrays at daily/bar granularity (FlowDesk's simulator — right for signal evaluation); event-driven engines replay individual market events with realistic order matching (execution engines — right for strategies where fills, queues, and microstructure matter).
- **PEAD (post-earnings announcement drift):** the well-documented tendency of prices to keep drifting in the direction of an earnings surprise for weeks after the report — the empirical strike against reflexively buying post-miss crashes.
- **Surprise–reaction divergence:** price moving against the surprise (beat-but-fell, miss-but-rallied); reveals that positioning and expectations differed from published estimates.
- **Positive skew (lottery-ticket distribution):** a return distribution where most outcomes are poor and a few huge winners carry the average — the small-cap regime. Remembered examples are drawn from the winning tail; medians and hit rates describe the actual bet.
- **Dilution:** the company issuing new shares (offerings, shelf registrations, expanded authorizations), shrinking each existing share's claim — commonly executed *into* small-cap rallies and therefore a mandatory context flag on any breakout tool.
- **Gamma exposure (GEX) / dealer hedging:** option market makers hedge their books in the underlying and futures; aggregate dealer gamma estimates whether that hedging dampens (positive gamma) or amplifies (negative gamma) index moves — a regime descriptor derived with assumptions, not a trade signal.
- **Volume profile / HVN / LVN:** volume distributed by price rather than time; high-volume nodes mark accepted value (magnets/support), low-volume nodes mark rejection (fast-travel zones). Bar-approximated in FlowDesk; tick-accurate only with paid data.
- **Cumulative delta / absorption:** tick-data concepts — net aggressive buys minus sells, and large passive interest soaking up aggression without price movement. Execution-Plane territory; not computable from free bars.
- **Wheel strategy (CSP → covered call):** sell a cash-secured put (obligated to buy at the strike, collateral posted); if assigned, own the shares and sell covered calls against them (upside capped at the call strike). Both legs harvest the variance risk premium; the structural bias is selling winners early and holding losers fully.
- **Assignment:** the option seller's obligation being exercised — the put seller buys the shares at strike, the covered-call seller delivers them.
- **PUT / BXM indexes:** CBOE's official benchmark indexes for systematic put-writing and buy-writing on the S&P 500 — free daily history, the authoritative answer to wheel-class performance claims.
- **Multiple-testing / data-mining bias:** searching many strategies guarantees impressive-looking flukes; the significance bar must rise with the number of trials, and untracked parameter tuning is untracked trials.
- **Deflated Sharpe ratio:** a Sharpe estimate haircut for the number of trials searched and the non-normality of returns — the standard correction for "we tested a thousand things."
- **Pre-registration / hypothesis registry:** logging each hypothesis and its parameters before testing, so selection happens on predictions rather than on outcomes.
- **Held-out set:** a final data period untouched during all exploration and selection, used exactly once as the last gate — the only backtest number that deserves partial trust.
- **Time & sales (tape):** the sequence of executed trades with price, size, and aggressor side (which side crossed the spread) — the raw material of orderflow concepts; requires tick-level data with bid/ask attribution.
- **Transaction-cost analysis (TCA):** measuring what execution actually costs — spread paid, slippage vs decision price, timing — using tick data; valuable even for slow strategies because costs concentrate in the volatile moments strategies tend to trade.
- **Fill model:** the backtest's assumption about where orders execute within a bar or tick sequence; optimistic fill models are the most common way garbage backtests look brilliant.
- **OSINT (open-source intelligence):** analysis assembled from publicly available sources — news, filings, shipping and flight data, official statements. Public by definition, so it confers context, not informational advantage.
- **Coincident vs causal:** two things occurring in the same window is coincidence until tested; FlowDesk labels geopolitical annotations coincident and lets scorecards decide whether they carry information.
- **Deal spread:** in an announced acquisition, the gap between the target's market price and the offer value; it prices deal-break risk and time value — a wide spread signals market doubt, not free return.

---

## 18. Future Work — Paid Upgrade Path

Zero-Cost Mode (§6.0) is the permanent base configuration, and nothing in this section changes that or any binding decision in §2 — money upgrades **data quality and signal coverage**, never the product's nature (still no execution, no advice, same honesty rules and forbidden vocabulary at every tier). Because every provider sits behind a swappable interface (§4), each upgrade below is an env-var + client change, not an architecture change. Prices are 2026 ballparks per month — verify at purchase time.

**The budget rule (binding):** a paid feed is justified only after the free version of the signal it improves has proven useful — in the simulator (§7.16) or in lived use. The simulator earns the budget; enthusiasm doesn't. Record each upgrade decision and what evidence justified it in `docs/UPGRADE_LOG.md`.

### 18.0 Value-per-dollar ranking (added v4.9.8 — read this before the capability tiers below)

The tiers further down are organised by *capability*. This section ranks the same items by
**how much of FlowDesk improves per dollar**, which is the axis that actually decides a purchase.
Scoring metric: roughly how many modules get meaningfully better, divided by monthly cost.
Prices are US dollars and were spot-checked July 2026 — **verify at purchase; they move, and
vendors rebrand** (Polygon.io now bills as Massive.com; the API and keys are unchanged). For a
Canadian buyer add ~35–40% FX, so a $29 subscription is roughly C$40/month — about C$480/year
against a C$11.6k portfolio, which is the comparison that matters.

**TIER 0 — Free, and strictly better than what you have. Do these before spending anything.**
| Item | Cost | Why it ranks above every paid item |
|---|---|---|
| **Polygon/Massive free tier as a cross-check source** (5 calls/min, end-of-day + 15-min delayed, no card) | $0 | Directly fixes the worst finding in ADR-0005: yfinance fails **silently**, returning an empty frame for a bad ticker, a delisted symbol, or an outage alike. A second independent source turns silence into a detectable disagreement — two sources disagreeing on a close is a quarantine event, not a mystery. This is the single highest-value change available at any price, and it is free. |
| CFTC COT, CBOE volatility structure, FRED additions, EDGAR | $0 | Already specified in v4.9.7. Institutional-grade positioning and rates data at no cost. |

**S TIER — best value per dollar of anything paid**
| Item | Cost | What improves |
|---|---|---|
| **Official equity feed — Polygon/Massive "Stocks Starter"** (15-min delayed, **unlimited** API calls) | **~$29/mo** | Nearly every module. Kills yfinance fragility outright; unlimited calls dissolve the §6.12 rate budget, which is what currently forces constituent breadth down to daily-only and caps intraday cadence. **Pricing corrected v4.9.10 (verified against the vendor's page 2026-08-14):** the "~$79 real-time tier" this row previously warned against does not exist — $79 (Developer) is **also 15-minute delayed** (it adds history depth and trades), and consolidated real-time is the **$199** Advanced tier. The correction strengthens the conclusion: real-time costs ~7× the S-tier buy, not ~3×, for precision FlowDesk's signals cannot use (§6.0), and it is purchasable only through Gate R (§18.0a). **Rule, binding on this section:** capability requirements ("consolidated real-time L1 trades/quotes"), never vendor plan names or dollar figures, are what gates and configs reference — plan names, tier contents, and prices are vendor marketing state, re-verified at purchase, recorded in `docs/UPGRADE_LOG.md` at purchase time only. |

**A TIER — strong, justifiable once the relevant module exists**
| Item | Cost | What improves |
|---|---|---|
| Fundamentals + disclosure feed (FMP-class) | ~$20–70/mo | Earnings calendar/surprises (§7.13), company fundamentals giving the Bottleneck Detector its first margin-expansion input (§7.10), and reliable congress/insider ingestion replacing GitHub dumps (§7.3). Three modules for one subscription. |
| Historical tick data (Databento-class) | **usage-based**, ~$25–125 for a research window; signup credit typically covers the first pull | Not a subscription — pay once per research question. Unlocks the §7.17 tick-TCA step and all Execution-Plane orderflow research. Ranks high purely because you can stop paying when you stop researching. |

**B TIER — good but narrow**
| Item | Cost | What improves |
|---|---|---|
| Borrow cost & short-interest (Ortex-class) | ~$50–150/mo (verify) | Two things, both real: turns §7.5's "premiums persist because of borrow constraints" from a static explainer into a measured number, and gives the IDEA-005 squeeze composite a live input instead of a three-week-stale one. Narrow, but it is the only way to price the borrow leg of a convergence trade. |
| Earnings-call transcripts | ~$20–70/mo | Upgrades the §7.13 debrief from press-release-only to full call + Q&A. |
| Crypto depth (CoinGlass-class) | ~$30–70/mo | Only Crypto Flows, which is last in the §3 cut order. Low priority by construction. |

**C TIER — expensive relative to what they add here**
| Item | Cost | Note |
|---|---|---|
| Options data | separate per-asset-class subscription (~$29–199/mo depending on latency tier) | Unlocks the whole §7.18 options module — but that module is Phase 3, and the delayed-chain experimental variant already covers the earnings implied-move and vol-gap work at $0. Do not buy this until the free version has proven it earns attention. |
| Real-time news API (Benzinga/Polygon-News-class) | ~$100–250/mo | Completes the one piece Zero-Cost Mode cut from the Sympathy Scanner (automated same-morning news on laggards). Genuinely useful, genuinely expensive, and improves exactly one module. |

**D TIER — not justifiable at this portfolio scale, regardless of appeal**
| Item | Cost | Note |
|---|---|---|
| Component/commodity pricing research (TrendForce-class DRAM/HBM contract prices) | $100s–$1,000s/mo | Analytically the *best* upgrade in the document — genuinely leading rather than coincident for bottleneck detection. Also costs more per year than the portfolio it would inform. Revisit only at a materially different account size. |
| Institutional flow / 13F platforms (WhaleWisdom, EPFR-class) | ~$40 to $1,000s/mo | Quarterly, 45-day-lagged data. The lag alone caps its value for anything but context. |
| Terminal-class (Bloomberg/LSEG) | $2,000+/mo | Reference class only. At that spend, use the terminal instead of building one. |

**Purchase sequence, with the §18 budget rule enforced**
1. **Now, $0:** add the free-tier cross-check source. It fixes a data-integrity hole rather than adding a feature, so it is exempt from the "prove it first" rule.
2. **After M2 ships and you have personally watched yfinance return something wrong:** the ~$29 equity feed. That experience is the evidence; buying before it is speculation.
3. **When the module that consumes it exists and its scorecard shows the signal is worth improving:** A tier, one item at a time.
4. **Never on the strength of a good idea alone.** Every purchase gets a line in `docs/UPGRADE_LOG.md` naming the evidence that justified it — which, for a student-sized account, is the difference between a research budget and a subscription pile.

#### 18.0a The real-time evidence gate (added v4.9.9 — clarification of the binding budget rule, not a new rule)

The real-time data tier is the one upgrade that keeps re-proposing itself, so the budget rule is
made specific for it here. Three findings already in this document say don't buy it — §6.0 (every
FlowDesk signal is a swing/positioning signal, so 15-minute snapshots lose nothing analytically),
§18.0 S TIER (the real-time tier is "buying precision the product cannot use"), and decision 17
(intraday tactics are permanently excluded, so there is no consumer for sub-15-minute data on the
Intelligence Plane). Appetite is not evidence against any of those. **What would be evidence is a
measurement, and this is the gate:**

> **Gate R (report format refined v4.9.10).** The consolidated real-time tier may be purchased only
> when a strategy that has already passed the §7.16 protocol — pre-registered, cost-realistic,
> survived the held-out set — shows material P&L decay on its **edge-decay curve**: the same
> strategy re-run under identical cost assumptions entering at signal time, +1 min, +5 min,
> +15 min, +30 min, and next open. A curve gives the edge an approximate **half-life**, which is
> what the decision actually needs: an edge that decays over days needs no feed upgrade at any
> price; material decay inside the first 15 minutes is the purchase justification, recorded in
> `docs/UPGRADE_LOG.md` with the report; and an edge gone within seconds identifies a strategy to
> kill, not fund (first consequence below). Absence of decay is a finding too, and closes the
> question rather than deferring it.

Two consequences worth stating, because they change what the gate is actually deciding:

- **A strategy that only survives at signal-time entry is a latency-competitive strategy, and it
  should be killed, not funded.** Latency competition is a capital-and-colocation race; a retail
  participant with a delayed retail feed loses it structurally, not by a margin that a subscription
  closes. Under §7.16 that result classifies as *edge below costs* and goes to the graveyard with
  its retest condition, not to the upgrade log.
- **"Real-time" and "paid data feed" are not the same purchase.** The Execution Plane's paper stage
  (decision 15, gate 2 — 60 sessions) runs through a broker paper API, and broker APIs supply live
  quotes to their own paper accounts at no charge [verify current terms at signup]. So the live-data
  experience the owner is reaching for is already available on the plane that can use it, at $0,
  without touching the Intelligence Plane's data budget. The Intelligence Plane never needed
  real-time; the Execution Plane gets it from the broker.

**Paper-gate clarification (added v4.9.10, interpreting decision 15 gate 2 — the gate itself is
unchanged).** Paper trading validates **software behavior** — order lifecycle, scheduling,
recovery, logging, live-vs-paper divergence within tolerance. It does **not** validate fill
economics: broker paper simulators model neither market impact, queue position, information
leakage, nor displayed-liquidity limits (simulated fills can exceed quoted size), and broker
paper data feeds may be **single-venue rather than consolidated** [verify the specific broker's
terms at signup — this varies and changes]. Two consequences: the M2 `venue_scope` provenance
line applies to paper-session data like any other observation, and the decision-15 gate 3 arming
judgment weighs reliability, divergence, and the §7.16 rule-7 tick-TCA — **never the paper
equity curve alone**. A beautiful paper P&L over a paper venue's fills is a statement about the
simulator, not the market.

**Registry note (IDEA-006).** The edge-decay curve Gate R depends on is a new
simulator metric, which the feature freeze forbids adding here. It is registered in
`docs/IDEAS.md` as IDEA-006 and enters §7.16 when the simulator is built — alongside the declared
entry-timing conventions already specified there (v4.8), which is where it naturally belongs.
Until that report exists, Gate R is unsatisfiable by construction, which is the intended state.

### Tier 1 — Reliability & speed (~$40–120/mo total)
| Spend | ~Cost | What changes |
|---|---|---|
| Official equity feed (Polygon.io Starter or Alpaca paid) | $30–100 | yfinance fragility gone; 15-min cadence → 1-min bars. Sympathy Scanner catches movers ~14 min earlier; Premium Monitor and Pair Watch tighten; intraday rotation refresh becomes meaningful. Single highest-value upgrade in the stack. |
| FMP paid tier | $20–70 | Reliable congress/insider/earnings ingestion (no GitHub-dump lag) + company fundamentals (revenue growth, margins) — Bottleneck Detector gains a margin-expansion component, its first fundamental input. |
| Small VPS (if not already on a free tier) | $5–20 | Uptime independence from the home machine. |
| `ENABLE_AI=true` (Haiku-class) | ~$1–5 | LLM-composed brief + Ask-the-Market. |

### Tier 2 — New signal classes (~$150–400/mo total)
| Spend | ~Cost | What changes |
|---|---|---|
| Short-interest / borrow data (Ortex-class) | $50–120 | Premium Monitor gains live borrow cost & availability on the expensive leg — turning "premiums persist due to borrow constraints" from a static explainer into a measured number (directly relevant to cross-listing setups like SKHY/KRX). Scanner gains real squeeze inputs. |
| Real-time news API (Benzinga/Polygon News-class) | $100–250 | Unlocks the piece Zero-Cost Mode cut from the Sympathy Scanner: automated same-morning news detection on laggards — the highest-conviction pattern in the §2.17 playbook, currently a manual link-out. |
| Options data (Polygon options-class) | $100–200 | Unlocks the Phase 3 options module (§7.17): per-symbol put/call, IV rank, unusual-activity detection. A genuinely new signal class — options often reprice before shares. |
| CoinGlass-class crypto tier | $30–70 | Deeper liquidation/OI granularity for Crypto Flows. |
| Earnings-call transcripts (FMP-class) | $20–70 | Upgrades the §7.13 debrief from press-release-only to full call + Q&A; same grounding rules apply. |
| Historical tick data (Databento-class, usage-based) | pay-per-use; signup credit | Execution-Plane budget, not FlowDesk's: trades-schema tick history for one futures instrument's research window costs tens of dollars, not the legacy per-gigabyte horror stories; book-depth (MBP/MBO) schemas cost more. Unlocks the §7.16 rule-7 tick-TCA step, orderflow strategy research, and NautilusTrader high-fidelity replays. Budget rule applies: bars prove the signal first, ticks are bought to price the execution. |

### Tier 3 — Fundamental early detection (~$500+/mo, only with sustained use)
| Spend | ~Cost | What changes |
|---|---|---|
| Component/commodity pricing research (TrendForce/DRAMeXchange-class memory spot & contract prices; equivalent per industry) | $100s–$1000s | The Bottleneck Detector's biggest possible upgrade: DRAM/NAND/HBM spot and contract prices are the *leading* indicator of memory scarcity — this moves detection from "market is repricing the segment" toward "the input costs moved first," genuinely ahead of the equity move. Same pattern per future industry (e.g., cryocooler lead times for quantum). |
| Institutional holdings/flow platforms (WhaleWisdom-class 13F aggregation; EPFR-class fund flows) | $50–$1000s | Smart Money gains a true institutional layer (13F position changes, quarterly with 45-day lag — displayed, as always); ETF-flow proxies replaced with measured fund flows. |
| Terminal-class data (Bloomberg/LSEG) | $2,000+ | Listed as the reference class only. Out of scope: at that spend, use the terminal, not FlowDesk. |

### What never gets paid for (any tier)
Social-sentiment scrapers, copy-trading/signal-seller subscriptions, "AI stock-picker" services, and paid trade-alert rooms — these fail the §1 admission test (they sell conclusions, not capital-flow data) and most fail decision 18's honesty bar. Execution infrastructure (broker API plans, order routing) stays excluded at every tier per decision 15.

### Suggested sequencing for this user's actual use
1. Official equity feed (everything improves at once), 2. borrow data (live convergence-trade context), 3. news API (completes the sympathy playbook), 4. memory/component pricing (bottleneck early detection), 5. options. Each step only after the budget rule above is satisfied.

---

## 19. Commercialization Notes — Differentiation (Future Work, non-binding)

**Scope note for implementing agents:** this section is strategy context for the human owner only. Build nothing from it — no billing, no auth tiers, no marketing pages — unless a future version of this document promotes specific items into the roadmap. The gate for even considering commercialization: the MVP exists, the owner has used it daily for months, and the outcome scorecards (§7.15) show the signals are worth paying attention to.

### 19.1 The investor's first question
Any investor or user will benchmark FlowDesk against the incumbents. What each actually competes on:

| Product | What it wins on |
|---|---|
| Bloomberg Terminal | Everything, for professionals, at $25k+/yr — breadth + exclusive data + chat network |
| TradingView | Charts + social community |
| Koyfin / YCharts | Fundamentals dashboards |
| Finviz / Barchart | Screeners |
| Quartr / Fiscal.ai / FinChat | AI over filings, transcripts, fundamentals |
| AlphaSense | Enterprise document search |
| BlackRock Aladdin | Institutional portfolio/risk operating system — inward-facing exposure, stress testing, OMS, compliance. Answers "what risk am I holding?"; FlowDesk answers "where is capital dislocating?" and grades its own calls. Not a competitor; a different category |
| Unusual Whales | Options flow + politician trades, retail-priced |
| FinceptTerminal-class clones | Open-source breadth (100+ connectors, dozens of AI agents) |

The lesson of the last row is the important one: **breadth is not a moat.** Competing on feature count against Bloomberg is a race the incumbent won decades ago; every "everything terminal" clone rediscovers this. FlowDesk must never pitch its module count.

### 19.2 Why would anyone switch — the honest answer
FlowDesk cannot win on breadth, charts, fundamentals depth, or data exclusivity (all inputs are public). Its genuine differentiators are things none of the table above does:

1. **Cross-market dislocation focus.** Premiums, pair extremes, vol gaps, bottleneck segments, sympathy laggards — one product organized around spreads and dislocations across asset classes, rather than around asset-class tabs. No mainstream retail product owns this frame.
2. **Self-grading signals.** The §7.15 outcome scorecards — every signal type's live hit rate and sample size, displayed next to every event. No competitor shows users the track record of its own alerts. "The terminal that grades itself" is a one-line pitch, a trust product, and — because the scorecard's value compounds with accumulated history — the closest thing this space has to a data moat built from public inputs.
3. **Auditability.** Every number traces to stored context; deterministic templates; published negative results. The anti-AI-washing position, at the exact moment competitors are bolting uncalibrated LLM "agents" onto everything.
4. **Detection-not-advice honesty** — which is simultaneously the ethical stance, the differentiation, and the regulatory posture (see 19.4).

Candidate one-sentence answer to "why switch": *"FlowDesk shows you where capital is moving across every market — and shows you the live track record of every signal it has ever given you."*

### 19.3 Beachhead and model (sketch only)
Do not launch to "everyone." The natural first customers are the people already paying $40–80/mo for Unusual Whales or Finviz Elite: active retail traders who want cross-market context and are tired of unaccountable alert services. Plausible model: open-core (MIT/Apache core per decision 24 — self-hosters become contributors and credibility) + a hosted convenience tier priced under the incumbents, with the paid data tiers of §18 folded into hosted pricing. Moats, honestly ranked: accumulated outcome history and research knowledge graph (grow daily, cannot be copied retroactively) > published-methodology trust > community theme/role taxonomies > everything else (copyable).

**Opportunity ranking (revised after external review):** (1) **career leverage** — highest certainty, and the same work either way; (2) **research infrastructure / "research OS"** — the durable differentiator, since retail builds indicators while almost nobody builds pre-registration, lineage, graveyards, calibration, and factor attribution as a system; (3) **data products** — clean timestamped histories that exist nowhere else (cross-listing premiums, disclosure-lag-adjusted trades, bottleneck-segment states, event-outcome datasets), valuable precisely because they accumulate; (4) **subscription dashboard — ranked last**, because dashboards are the most copyable layer of the stack. Sell the process and its accumulated memory, not the screens.

### 19.4 The novelty thesis (the version an investor remembers)
Feature novelty is not defensible — every FlowDesk module, individually, exists somewhere and can be cloned in a quarter. The defensible novelty is one mechanism plus one category:
- **Counter-positioning via self-grading.** No retail product publishes the live hit rate of its own alerts, and the incumbents structurally *can't* copy this: their businesses depend on unaccountable signal volume, so imitating FlowDesk means indicting their own product. Their rational response to a self-grading competitor is inaction — the definition of counter-positioning. The accumulated scorecard history additionally cannot be copied retroactively; it is the only asset here that compounds daily and is unavailable to a fast-follower at any price.
- **Category ownership: "dislocation intelligence."** No product's organizing principle is cross-market mispricing detection; naming the category avoids feature-table comparison against Bloomberg entirely.
- **Why-now:** as AI-generated financial content floods the market with uncalibrated confidence, provable auditability (every number traces to stored context; hype fails the build) shifts from engineering hygiene to market position.
- **Distribution reality:** consumer fintech is usually won on distribution, not technology (Unusual Whales' growth was an audience, not an algorithm). FlowDesk's research outputs — backtest reports, scorecard summaries, published negative results — are natively shareable content no competitor will match, because publishing negative results is exactly what their models forbid. The loop: research → published honestly → audience → trust in scorecards → moat.

### 19.5 Regulatory line (non-negotiable)
FlowDesk's decisions 14–22 — no recommendations, no sizing, no execution, no "buy X" — are also what keeps a hosted product on the information-publisher side of investment-adviser regulation rather than the personalized-advice side. Commercialization must never soften those decisions to "increase engagement"; they are the product. Under revised decision 15, this is now explicitly a plane boundary: any hosted, public, or paid deployment ships the Intelligence Plane ONLY — the Execution Plane is profile-excluded from public builds, and offering execution or personalized signals to third parties would change the regulatory category entirely. Formal legal review is required before charging anyone.

---

*End of document. An implementing agent should now proceed to §16, Milestone 0.*
