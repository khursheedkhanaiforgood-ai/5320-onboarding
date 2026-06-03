# Changelog — Journey to the Digital Twin

Living strategy record for the Extreme AP / EP1 parameter twin.
Format follows [Keep a Changelog](https://keepachangelog.com); newest version on top. Append-only — older entries are not rewritten.

---

## [1.1] — 2026-06-03 (PM)

Revised after a Claude Code review of the AM strategy doc and the ns-3 starter files.
**Core change: ns-3 deferred; Monte Carlo + optimizer lead; stadium first.**

### Changed
- **§07 Execution** restructured from a single "ns-3 / Sionna Ladder" into **two tracks**:
  - **Track A — SHIPS NOW** (on the existing JS model): A1 archetype-matrix HTML → A2 Monte Carlo tab → A3 optimizer tab.
  - **Track B — DEFERRED**: the original 7-rung ns-3/Sionna ladder, content unchanged, reframed as the medium-term research track. Starter files (`stadium-wifi.cc`, `qoe_harness.py`) noted as written-and-parked.
- **ns-3 rung 1 badge** changed from `START HERE` → deferred note; `START HERE` moved to the archetype matrix (A1).
- **§09 Fidelity Ladder** — added an "Immediate target · Track A" note: the near-term work reaches a partial **rung 5** (robustness via the chance-constrained objective) and motivates **rung 2**, all without ns-3.
- **Footer version** `v1` → `v1.1 · rev 3 June 2026 (PM)`.

### Added
- **§03 Stadium QoE Objective** — new **Robust form** box: the chance-constrained objective
  `maximize P50[U] − k·(P95 − P5)  s.t.  P(POS ≤ 10 ms) ≥ 99%`, tying the optimizer to Monte Carlo.
- **§07 sharpening note** — Monte Carlo *does* move the optimum on the current model, because it already contains a CWmin load-cliff (`cwBe=7 & u>0.7`), a TXOP-BE × rTWT interaction, and the POS SLA. "Both" (MC + optimizer) is therefore necessary, not optional.
- **§10 Log — Entry 002 (PM)** recording the sequencing revision and accepted refinements:
  optimizer searches only the ~15 non-trivial knobs (monotone/gate knobs locked); bands labelled **model** (not measurement) uncertainty; archetype selection **locked before** any recommendation is shown; convention-center hedge tightened to exhibit/breakout/plenary sub-modes; Morris cost `r×(k+1) ≈ 610 runs` carried to Track B.

### Fixed (governance)
- **§10 Log — Entry 001** corrected via an append-only banner (body left **unedited** for provenance), noting its "Next actions" (ns-3 ladder as the immediate path) were superseded same-day by Entry 002. No silent rewrite.

### Integrity
- Tag balance verified: 91/91 `<div>`, 11/11 `<section>`, 4/4 `<ol>`; exactly one end-of-log marker.

---

## [1.0] — 2026-06-03 (AM)

Initial strategy record established.

### Added
- §00 Premise / North Star — scope locked to an EP1 parameter-tuning + calibration engine; unit is the archetype, not the site.
- §01 Dimensionality — three collapsing forces + effect-type taxonomy (multiplicative / gate / threshold / interaction / context-modulated).
- §02 The seven regime parameters + the v0 archetype matrix (5 archetypes).
- §03 Stadium QoE objective (point form) — proportional-fairness utility under hard POS/SLA constraints.
- §04 Optimization vs calibration — two verbs, two evidence trails.
- §05 Driver discovery — Tier 0 prior → Morris → Sobol, per archetype.
- §06 Governance — two registers (internal / external), with pre-registered falsification.
- §07 ns-3 / Sionna 7-rung execution ladder.
- §08 Six-agent Claude framework with incremental rollout.
- §09 Fidelity ladder (rungs 1–6, "you are here").
- §10 Log — Entry 001.

### Companion artifacts delivered same day (separate files)
- `stadium-wifi.cc` — minimal ns-3 802.11ax scratch sim (FlowMonitor → CSV).
- `qoe_harness.py` — parameter→QoE harness implementing the stadium objective.
- `README_ns3_starter.md` — install/build/run + how Claude Code drives the loop.
