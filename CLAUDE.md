# CLAUDE.md — 5320 Onboarding / Horizon Lab
<!-- 5320-onboarding-agent project file.
     Bootstraps Claude Code for the Extreme Networks lab (WiFi Digital Twin / Golden Parameter Sets).
     Last updated: 2026-07-22 (integrated with home-directory specifications).
     
     ↔️  CROSS-REFERENCE: ~/CLAUDE_5320_lab_specifications_20260722.md (source spec file)
     This file adds project-specific integrations: site index parity, reference files, git structure. -->

## Operator
Khursheed Khan — Senior Systems Architect, Extreme Networks (5320 onboarding program).
5G/cellular RF background; treats WiFi tuning like cellular golden parameter sets.
Host: EXT-L63PXM76R6 (company Mac). Uses voice/dictation — expect transcription artifacts.

## Interaction Rules
- When step-by-step instructions are requested, deliver them directly. No Socratic gatekeeping, no comprehension checks. (Socratic mode only when explicitly invited.)
- Session outputs: EOD HTML blueprint artifacts and PPTX decks on request.
- `CHANGELOG.md` is append-only and version-stamped. Never rewrite history in it.
- `journey_to_the_digital_twin.html` (v1.1) is the living strategy record.
- **This project:** Code-first on substance. Do not coach delivery; help with lab debugging, parameter tuning, and architecture.

---

## HARD CONSTRAINTS — Never Violate

1. **AP native VLAN** — Never change an AP's native VLAN without first separating the AP management VLAN.  
   *(Violation on 2026-05-06 broke AP1's CAPWAP tunnel — AP self-manages on VLAN 1.)*

2. **Optimization ≠ Calibration** — Optimization searches parameter space against a fixed model. Calibration adjusts the model against real measurements. Separate verbs, separate evidence trails. Never conflate.

3. **Simulator blocker** — 17 physics bugs must be fixed before any optimizer run.  
   Critical: C1 double-counted spatial gain · C2 unbounded multiplicative stack (4.62×, no saturation ceiling) · C3 airtime utilization vs goodput computed off different capacity bases.

4. **VOSS ACL rule** — Wherever the Anycast gateway lives, the Guest isolation ACL must also live — on BOTH switches.

5. **Live lab state** — When uncertain, ask before recommending config changes.

---

## Lab Topology (Horizon Custom Fabrication)

| Device | Role | Config |
|--------|------|--------|
| **SW1** | EXOS, primary L3 | IP: 192.168.0.28 · Port1→Modem · Port3→AP1 (192.168.0.12) · Port5→Wired VLAN10 · Port10→trunk to SW2 · DHCP: VLANs 10/20/30 (gateways 10.10.0.1 / 10.20.0.1 / 10.30.0.1) · Modem static routes 10.x.0.0/24 → .28 |
| **SW2** | VOSS/Fabric Engine (conv. 2026-05-01) | IS-IS up, nick 0.00.02, area 49.b0b1 (manual lock pending) · B-VLANs 4051-4052 · I-SIDs: 15999999 (Onboarding VLAN4048 ports 1/3+1/10), 16777001 (FAN) · AP2 (192.168.0.25) on Port1/3 |
| **IPE-40AX-V2** | Regional controller | lan1→SW2 Port1/1 (VLAN 4047) · wan2→HomeModem (192.168.0.20/24) · GRE to RDU Raleigh RDC (10.254.0.8/16 inside) · **lan1 IP unconfirmed — verify before SW2 default route** |

**Service VLANs (planned):** 70 Corp_New / 80 Guest_New → I-SIDs 100070/100080 (convention VLAN+100000).

**VIQ state:** IPE onboarded, SW2 discovered, no Corp/Guest policy yet.

**VOSS syntax cheat:**
- Config DHCP: `ip dhcp-server subnet` → pool/router/domain-name-servers/lease-time
- Default route: `ip route 0.0.0.0/0 {IP} weight 1` then `enable`
- Port taxonomy: 1/3/5 = UNI edge · Port10 = NNI backbone · FA on AP ports

---

## Active Project — WiFi Digital Twin / Golden Parameter Sets

**Scope:** ~60 Broadcom silicon params → search set (~12 stadium) / locked set (monotone gains, gates) / scenario inputs (Monte Carlo demand & threat).

**Five archetypes:**
- Dense Public Venue/Stadium
- High-Density Enclosed
- Sparse High-Throughput/Warehouse
- Mixed Retail/Hospitality
- Latency-Critical Low-Density

**Stadium objective:** Chance-constrained proportional-fairness utility, hard POS latency constraints.

**Agent design (incremental):** Orchestrator → Screening/DoE → Simulation → Surrogate → Governance/Audit → Calibration.

**Tracks:**
- Track A = JS simulator (now)
- Track B = ns-3/Sionna (deferred)

**V&V gap:** 2 physical APs can't emulate archetypes. Four-tier plan outlined but NOT executed:
1. Config verification
2. IEEE TGax model verification
3. Limited physical calibration
4. Full archetype validation

---

## Current EP1/XIQ Work

- AP3000 radio + wired interface templates in EP1/XIQ; building auditable golden parameter sets per archetype.
- WiFi1 5GHz profile (`radio_ng_11ax-5g`) fully mapped: WMM/EDCA (CW exponent→value) · Band Steering · Load Balancing · DCS · OFDMA · BSS Coloring · TWT · PHY.
- **Traceability chain:** Intent → Design → Configuration → Verification/Observation.
- **Docs:** XIQ Classic URLs rot; EP1 Networking v25.10.0 PDF is reliable; support hub = stable bookmark.
- **Note:** EP1 = Extreme Platform One (on-prem controller), NOT the IPE.
- **Warning:** AP5020 dropped DSSS/CCK — legacy 2.4GHz scanners may need replacement.

---

## Backlog (Priority Order)

1. ⚠️ **Fix 17 simulator bugs** (C1/C2/C3 first) → then optimizer
2. **Execute four-tier V&V**
3. **VOSS/Fabric Connect deep dive** (scheduled Jul–Aug 2026): pre-flight, OS change procedure, EVE-NG twin, post-migration verification
4. **SW2 safe block:**
   - Manual-area lock
   - vlan create 70/80 + i-sid
   - fa enable Port1/3
   - DHCP + IPE transit
   - VIQ Corp/Guest SSIDs
5. ACL GUEST_ISOLATION verify (both switches)
6. QoS qp6/qp1 verify
7. AP2 full wireless test on SW2
8. OWE + legacy compat
9. SPAN-port capture practicum
10. 802.1X wired auth
11. WiFi L1 PHY deep dive
12. AP1 proper fix: SW1 Port3 trunk = VLAN 1 (AP mgmt) + VLAN 20 (data)

---

## Site Index Parity (5320-onboarding-agent)

**BLOCKING:** The landing page (https://khursheedkhanaiforgood-ai.github.io/5320-onboarding/) is out of sync with main branch.

- [ ] Add **May 22 EOD card** to `index.html`, `index-nyt.html`, `index-harpers.html`
- [ ] Add **May 11 EOD card** to `index.html`, `index-nyt.html`, `index-harpers.html`
- [ ] Sync `gh-pages` branch with latest main commits (last update: March 25, 2026)

**Card details for May 22:**
- Label: `EOD · May 22 ★ Sprint B`
- Title: Sprint B Complete — 196 Fields, 31 Bins, Live Search
- Body: XIQ Classic fully scraped · G-01–G-07 CLI safety rules · Node 11 cross-val schema · 51 screenshots embedded
- Accent: `#7c3aed` (violet — intelligence engine)
- File: `docs/session_summary_20260522.html`

**Card details for May 11:**
- File: `docs/session_summary_20260511.html`
- Accent: `#1d4ed8`

---

## Environment Notes

- **Account:** Enterprise (khukhan@extremenetworks.com via SSO) as of 2026-07-22. Cloud sessions/scheduled tasks did not migrate — recreate if needed.
- **Custom commands/skills:** `.claude/commands/`, `.claude/skills/` (verify present; `/preflight` skill for SW2 safe block proposed, not built).
- **Packet capture (Windows):** No native monitor mode; Alfa RTL8812AU / MT7921AUN; Acrylic WiFi Home ≈ macOS Scan tab.
- **Known EXOS fix:** 169.x self-assigned on AP ports → `enable dhcp ports <port#> vlan Default`.

---

## Key Reference Files

- `CHANGELOG.md` — append-only session log (version-stamped)
- `BACKLOG.md` — task tracking (index parity, deferred work)
- `journey_to_the_digital_twin.html` — strategy record (living doc, v1.1)
- `docs/` — HTML session summaries (linked from landing page)
- `.claude/settings.local.json` — personal overrides (in `.gitignore`)

---

*Last updated: 2026-07-22 · Merged with home-directory specifications*
