# Project Backlog

> **Session start rule:** Read this file first, then brief the user in 3–5 bullet points on what's pending.
> Mark items `[x]` done as work completes. Add new items at the top of the relevant section.
> Last reviewed: 2026-05-24

---

## Sprint: Index Parity + EOD Cleanup

### Immediate — blocked on nothing, do now

- [ ] Add **May 22 EOD card** to `index-nyt.html` (file: `docs/session_summary_20260522.html`)
- [ ] Add **May 22 EOD card** to `index-harpers.html`
- [ ] Add **May 22 EOD card** to `index.html`
- [ ] Add **May 11 EOD card** to `index-harpers.html` (only in index-nyt.html today)
- [ ] Add **May 11 EOD card** to `index.html`

Card details for May 22:
- Label: `EOD · May 22 ★ Sprint B`
- Title: Sprint B Complete — 196 Fields, 31 Bins, Live Search
- Body: XIQ Classic fully scraped · G-01–G-07 CLI safety rules · Node 11 cross-val schema · 51 screenshots embedded
- Border accent: `#7c3aed` (violet — intelligence engine)
- href: `docs/session_summary_20260522.html`

Card details for May 11 (copy from index-nyt.html line 467):
- href: `docs/session_summary_20260511.html`
- Accent: `#1d4ed8`

### EOD HTMLs to create — sessions with no HTML yet

| Date | Topic | Key facts |
|------|-------|-----------|
| May 15 | XIQ→EP1 transition sprint start | XIQ GUI retired Jul 1; 6-week sprint; feature/xiq-ep1-transition branch |
| May 18/19 | PPSK study + EP1 deploy guide v2.7 | 95-page PPSK guide; commit fe227f6; xiq-ep1-arch-debate.html created |
| May 20 | EP1 full zero-CLI deploy | SW1 factory reset complete; AP1 claimed; G-01 gap logged |
| May 21 | Stage 2 EP1 deploy + Intelligence Engine architecture | 17-agent LangGraph; Sprint A 25 tests green; SW1 claimed |

### Deferred — parked, revisit when time permits

- [ ] **Copyright insignia** on all HTML pages (flagged May 21 — deferred then)
- [ ] **Bin 07 Radio Profile re-scrape** — Dojo channel-selector widget blocks headless JS click; fix with JS evaluate() by visible-index
- [ ] **Sprint C: EP1 Playwright scraper** — same traverser.py pattern, map to EP1 bins
- [ ] **Non-Anthropic model debate** — Socratic: multi-LLM red-team of engine architecture
- [ ] **Sprint E: two query modes** — fast one-shot + guided HITL (fast = default, HITL = button)
- [ ] **Apr 22 dedup** — session_summary_20260422.html appears twice on all 3 index pages (banner + EOD card). User decision Apr 28: keep both for now. Revisit.

---

## Completed (recent)

- [x] May 22 EOD HTML — `docs/session_summary_20260522.html` (NYT-style, Sprint B complete)
- [x] Sprint B scrape — 196 fields, 31/31 bins, 51 screenshots, `gui_baseline_xiq.json`
- [x] CLI safety rules G-01–G-07 — `sprint_b/scli_safety_rules.json`
- [x] Node 11 cross-validation schema — `sprint_b/cross_val_prompt.json`
- [x] Live-search report — `sprint_b/output/xiq_gui_baseline_report.html` (4.8 MB, self-contained)
- [x] May 11 EOD HTML — `docs/session_summary_20260511.html`
- [x] EXOS→VOSS 4-principle Rosetta Stone on SW2 live config
- [x] 802.1X PEAP-MSCHAPv2 arc + dot1x_simulator.html

---

## How this list is maintained

- **Claude:** At session start, read this file and brief the user. Add new pending items as they arise in conversation. Mark done as work completes.
- **User:** Say "update the backlog" to capture anything mid-session. Say "what's pending?" to get the brief without re-reading everything.
- **Parity rule:** Every EOD HTML that lands in `docs/` must also get a card on all 3 index pages (`index.html`, `index-nyt.html`, `index-harpers.html`). This list tracks the lag.
