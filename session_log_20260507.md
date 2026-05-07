# Session Log — May 7, 2026

## DHCP MacBook Incident — Full Socratic Walk-through

This file captures the Socratic dialogue from May 7 that diagnosed the May 6 lab DHCP failure.

---

## Linked Resources

### Pcaps
- [pcap_May6_v4_guest.pcapng](data/may7-dhcp/pcap_May6_v4_guest.pcapng) — May 6 BAD: MacBook on Guest SSID, 7 DISCOVERs, 0 replies
- [pcap_May6_v5_corporate.pcapng](data/may7-dhcp/pcap_May6_v5_corporate.pcapng) — May 6 BAD: MacBook on Corporate SSID, 7 DISCOVERs, 0 replies
- [pcap_May7_10-40am_good.pcapng](data/may7-dhcp/pcap_May7_10-40am_good.pcapng) — May 7 GOOD: post-reboot, full DORA in 1.1s

### Prior Dialogue
- [DHCP_Socratic_Dialogue_May6_2026.docx](data/may7-dhcp/DHCP_Socratic_Dialogue_May6_2026.docx) — May 6 Claude session transcript

### Related EOD
- [session_summary_20260504.html](session_summary_20260504.html) — May 4 EAPOL & Packet Forensics deep dive (theory)
- [session_summary_20260507.html](session_summary_20260507.html) — Today's EOD (this incident, applied)

---

## Lab Setup (May 6)

```
QF Modem (192.168.0.1)
    │
    └── SW1 EXOS (192.168.0.28)
        ├── Port 1 → QF Modem uplink
        ├── Port 3 → AP1 (AP3000)
        ├── Port 5 → MacBook wired (10.10.0.100)
        └── Port 10 → SW2/AP2 (UNPLUGGED during incident)
```

VLANs:
- VLAN 10 — Wired Corporate (10.10.0.0/24, GW 10.10.0.1)
- VLAN 20 — Wi-Fi Corporate (10.20.0.0/24, GW 10.20.0.1)
- VLAN 30 — Wi-Fi Guest (10.30.0.0/24, GW 10.30.0.1)

**SW1 is the DHCP server for all three VLANs.** No upstream DHCP relay needed.

---

## Symptom Timeline

| T | Event |
|---|-------|
| T0 | iPhone working fine on Wi-Fi Corp + Guest |
| T1 | User turns on MacBook Wi-Fi (while wired on Port 5) |
| T2 | iPhone DHCP breaks; MacBook also can't get DHCP on either SSID |
| T3 | Pcap captured from MacBook en0 — 7 DHCP DISCOVERs over ~50s, ZERO server replies |
| T4 | Reboot APs + MacBook → all working again |

---

## Three MacBook MACs (Apple per-SSID privacy randomization)

| Interface | MAC | UAA/LAA | Source |
|-----------|-----|---------|--------|
| Wired (Port 5, VLAN 10) | `80:69:1a:dd:0f:c3` | UAA — Apple OUI burned-in | show dhcp-server |
| Wi-Fi Corporate (VLAN 20, May 6) | `e2:68:dc:4d:37:3c` | LAA — privacy randomized | pcap_May6_v5 |
| Wi-Fi Guest (VLAN 30, May 6) | `4e:60:81:bb:23:18` | LAA — privacy randomized | pcap_May6_v4 |
| Wi-Fi Corporate (May 7 post-reboot) | `84:2f:57:94:bd:cf` | UAA — Apple OUI hardware | pcap_May7_good |

**Insight:** Same machine, FOUR different MACs across two days. Apple's per-SSID privacy randomization explains 3 of them; the 4th (May 7 UAA) suggests Private Wi-Fi Address was disabled on Corporate post-reboot, OR the OS rotated to hardware MAC after long disconnect.

iPhone MAC on Corporate (working baseline): `62:75:85:f6:4e:3e` (LAA, leases 10.20.0.101).

---

## Hypothesis Arena

### Alex / NotebookLLM — "MAC Flap" (May 7) — REJECTED

**Premise:** Same MAC appears on both Port 5 (wired) and Port 3 (via AP) → SW1 FDB locks → DHCP listener stalls.

**Verdict:** REJECTED. Three different MACs. Wired NIC uses burned-in Apple OUI (UAA). Wi-Fi uses privacy-randomized MAC (LAA), different per SSID. **There is no flap** — the wired and wireless interfaces never share a MAC.

### Alex Q1 — "Wrong VLAN landing (192.168.1.1 modem)" — PARTIAL

Only fits v4 pcap (Guest, has 192.168.1.1 IGMP). The v5 pcap (Corporate) is completely silent — no 192.168.1.1, no IGMP, no traffic at all. Theory only explains 50% of the failure pattern.

### Prior-Claude (May 6) — "Soft state in AP3000 forwarding" — TRAIL

Got to: SW1's FDB on VLAN 20 had ONLY iPhone MAC. MacBook's MAC was NEVER in FDB despite active DHCP transmission. **Frames never reached SW1 Port 3.** Reboot consuming the failure = volatile in-memory state. Pointed at AP3000 internal forwarding.

### May 7 Final Diagnosis — **WPA2 Key State (option b)** — CONFIRMED via elimination

---

## The 4-Table Elimination

The AP3000's software bridge has multiple in-memory tables. Frames pass through them in sequence. We tested each:

| Option | What it tracks | Direction | Selectivity | Silent? | Verdict |
|---|---|---|---|---|---|
| (a) WLAN client table | Association state machine | Both | Per-client | Sometimes (logs) | Runner-up |
| **(b) WPA2 key state** | **TK + GTK per client** | **Both** | **Per-client + All-clients** | **Yes** | ✅ **WINS** |
| (c) Bridge MAC FDB | MAC → port mapping | Forwarding only | Per-MAC | NO — FLOODS | ❌ Fails |
| (d) Per-client TX queue | Downstream buffer | **Downstream only** | Per-client | Sometimes | ❌ Fails (one-way) |

### Why (c) fails

Linux software bridges **flood unknown unicast** to all ports — they don't drop. So a bad/missing FDB entry would still result in SW1 Port 3 receiving the frame. SW1 didn't receive anything → frames died **upstream of the bridge FDB lookup** → before bridging → at the **decryption layer**.

### Why (d) fails

TX queue is downstream-only (AP→client). Even if it wedged, upstream client→AP→wire would still work. But SW1's FDB never saw the MacBook's MAC, meaning upstream is also broken. (d) cannot explain a bidirectional failure alone.

### Why (b) wins

Temporal Key (TK) installed in AP's hardware crypto engine encrypts/decrypts unicast 802.11 traffic in **both directions** with this client. If TK is wrong:

- **Upstream:** AP can't decrypt client frame → discards before bridge sees it → never reaches FDB → never reaches wire ✓
- **Downstream:** AP encrypts with wrong TK → client can't decrypt → drops as MIC failure → no DHCP OFFER seen ✓

**Same broken key, both directions deaf, silent, MAC-specific. Reboot = fresh handshake = fix.**

---

## The Plot Twist — iPhone also failed

Single-client TK theory cannot explain why iPhone's DHCP also broke. Resolution: extend (b) to **GTK** (Group Temporal Key).

| Key | Scope | Encrypts |
|---|---|---|
| TK | Per-client unicast | AP↔this-one-client |
| **GTK** | **Per-BSSID broadcast** | **All multicast/broadcast frames the AP transmits to every client on this SSID** |

**Sequence that broke iPhone:**

```
T0  iPhone has GTK_v1, working
T1  MacBook 4-way handshake wedges/times out
T2  AP deauths MacBook (handshake never completed)
T3  AP rotates GTK → GTK_v2 (security: prevent failed/disassoc'd client from
    decrypting future broadcasts)
T4  AP runs Group Key Handshake to push GTK_v2 to remaining clients
    M1: AP sends GTK_v2 (encrypted with iPhone's KEK)
    M2: iPhone ACK back to AP
T5  M1 or M2 wedges (lost, retried, replay counter desync)
T6  AP commits to GTK_v2; iPhone still on GTK_v1
T7  DHCP OFFER (broadcast) → AP encrypts with GTK_v2 → iPhone decrypts with GTK_v1
    → MIC fails → silent discard
T8  iPhone's lease expires → can't renew → loses IP → starts DISCOVER → broken
```

**Reboot fixes it because:** AP wipes ALL key state, every client re-runs full 4-way handshake from scratch, fresh GTK distributed cleanly.

This ties directly to **May 4 EOD Concept #5**:
> *"GTK Impact — Group key decrypts ALL broadcast/multicast. Cracking GTK reveals ARP, DHCP, mDNS — full VLAN topology."*

---

## XIQ AP3000 Diagnostic Procedure (Next Time, BEFORE Reboot)

Run via XIQ → Device 360 → Tools → SSH/CLI Session:

| # | Command | What it shows | Smoking gun looks like |
|---|---------|---------------|------------------------|
| 1 | `show client` | Associated clients + state | MacBook listed but stuck at `4WAY-PENDING` instead of `Authenticated/Data-OK` |
| 2 | `show client mac <MAC>` | Per-client deep dive | TK Installed: NO, or PTK install failed |
| 3 | `show counters wireless` (or `show wlan-statistics`) | Decrypt errors / MIC failures | **Spiking decrypt-error counter — THE confirmation** |
| 4 | `show interface wifi0 statistics` | Per-radio RX OK / RX errors / Decrypt failures | Decrypt failures > 0 with no plaintext output |
| 5 | AP packet capture, filter `wlan.addr == <MAC>` | Raw 802.11 + decryption result | `Protected Frame=1` arrives but undecodable payload |

**Procedure:** Run once → wait 30s → run again → diff counters. That's the live failure rate. Save the .pcap. THEN reboot. Compare to confirm the wedge cleared.

---

## Concepts Mastered (May 7)

1. **Apple per-SSID privacy MAC** — same device shows different LAA MAC per SSID (and may revert to UAA after long disconnect / setting toggle)
2. **Linux software bridge floods unknowns** — bridge FDB doesn't drop on missing entry, it floods → can't be silent-drop killer
3. **TX queue is downstream-only** — can't explain upstream loss alone
4. **TK = unicast, per-client** — bad TK = one client deaf in both directions
5. **GTK = broadcast, per-BSSID** — bad GTK = ALL clients deaf to broadcasts
6. **Encryption mismatch is silent** — failed MIC discards happen in hardware crypto, no client-visible error
7. **Reboot consumes evidence** — capture AP-side diagnostics BEFORE rebooting
8. **GTK rotation triggers on disassoc** — security-by-design feature that can wedge group key handshake
9. **Group Key Handshake (M1+M2)** — separate 2-message exchange after the initial 4-way; wedges silently if M2 lost

---

## Cross-References

- May 4 EOD: [session_summary_20260504.html](session_summary_20260504.html) — EAPOL theory deep dive (the prequel)
- May 7 EOD: [session_summary_20260507.html](session_summary_20260507.html) — applied case study (this file)
- Memory: `project_dhcp_macbook_incident.md` — full saga + diagnostic procedure
- Memory: `project_session_20260507.md` — today's session plan

---

## Status

✅ DHCP incident diagnosed via Socratic elimination — answer is **WPA2 key state wedge** (TK for MacBook + GTK rotation for iPhone)
✅ Diagnostic procedure documented for next-time-before-reboot capture
✅ Concepts mastered span both unicast and broadcast key paths
⏭️ Next workstream: EXOS→VOSS 4-principle cheatsheet for SW2 (Corp_New + Guest_New on Port 1/3, MacBook on Port 1/5)
