# Verbatim Dialogue — Pcap Analysis & GTK Rotation Mitigations
## May 8, 2026 — for Extreme GTAC / R&D escalation reference

This file captures the post-repro dialogue verbatim — the user's investigation
into mitigations after observing the 2-4 minute GTK rotation cascade in their
lab. Intended for sharing with Extreme Networks GTAC and R&D engineers.

**Background context:** A reproducible WPA3-SAE-PMF cascade on AP3000 (IQ Engine
OS) where introducing a wired interface from a Wi-Fi-associated client triggers
GTK rotation; lagging (idle) clients miss the Group Key Handshake re-sync
and stay offline 2-4 minutes before self-recovery.

The full incident write-up, pcap, tech-support dumps, and verbatim 3,076-
paragraph session DOCX are at:
https://khursheedkhanaiforgood-ai.github.io/5320-onboarding/session_summary_20260508.html

---

## DIALOGUE (verbatim, post-repro analysis)

### User question 1 — request for pcap analysis

> "I want you to analyze the pcap from today, as I have some questions."
> /Users/khukhan/Library/CloudStorage/OneDrive-ExtremeNetworks,Inc/Desktop/WiFi_en0_May8_2026_ISSUES_CAPTURE1.pcapng

### Claude response 1 — deep pcap analysis

**Capture overview**
- 10 minutes total (596 seconds), 34,814 frames, 23 MB
- Work MBP dominant (20,248 TX frames = 58% of capture) — heaviest active client
- iPhone (290 frames) and Personal MBP (98 frames) were nearly idle — important point, see below

**What's CONSPICUOUSLY ABSENT**

ZERO DHCP frames in the entire 10-minute capture. Not one DISCOVER, OFFER, REQUEST, or ACK visible.

This is normal for a single-client en0 capture — DHCP is unicast at L2 between client and AP, encrypted with each client's individual TK. Work MBP can't decrypt iPhone's DHCP frames. What this means: the iPhone's DHCP loss + recovery happened, but it's invisible from the work MBP's vantage point. The recovery evidence is the gratuitous ARP, not the DHCP cycle itself.

**Trigger window — sharper picture**

| Time | Event | Interpretation |
|---|---|---|
| T=0–180s | 5 frames per 30s | Pre-trigger idle baseline |
| T=180–192s | First sign of activity | en8 plugged into Port 5; macOS detects new wired link |
| T=192.39s | Work MBP ARP for gateway 10.20.0.1 | macOS revalidating Wi-Fi gateway after route reshuffle |
| T=192.87–192.88s | 6× ICMP "Destination unreachable: Port unreachable" from 8.8.4.4 and 8.8.8.8 | macOS sockets torn down mid-flight; existing UDP/QUIC streams orphaned |
| T=195.19s | Work MBP ARPs Personal MBP (10.20.0.101) | macOS testing peer reachability |
| T=215–221s | Burst of 3 work MBP gateway ARPs over 5s | Aggressive route table validation |
| T=284.73s | 3 rapid back-to-back ARPs in <1ms | High retransmit pressure — possibly trying to force a route refresh |
| T=309s, 399s, 489s | Gateway probes work MBP every ~90s | SW1 keepalive |

**Recovery markers (the smoking gun)**

| Frame | Time | Event | Recovery delay |
|---|---|---|---|
| 12036 | T+163s (355s wall-clock) | iPhone gratuitous ARP × 2 | ~2.7 min |
| 12065 | T+165s | iPhone ARP × 2 (confirmation) | — |
| 22098 | T+256s (448s wall-clock) | Personal MBP gratuitous ARP × 4 (over 3.2s) | ~4.3 min |
| 22252 | T+258s | ICMP unreachable from Google service to 10.20.0.102 | Trailing socket teardown |

**Activity-level asymmetry (THE confirmation of B)**

| Client | Frames sent | Failed? | Why |
|---|---|---|---|
| Work MBP | 20,248 (Claude API, GitHub, Apple, Microsoft, Azure) | NO | High activity = constant EAPOL rekey context = stays in sync with GTK rotation |
| iPhone | 290 | YES (2.7 min) | Low activity = no re-rekey trigger = vulnerable to missed GTK |
| Personal MBP | 98 | YES (4.3 min) | Lowest activity = longest miss window |

This is a significant new datum: failure duration scales inversely with client activity level. The most idle client (Personal MBP) failed longest. This is consistent with how AP retries push GTK_v2 — the retry timer fires periodically, but only re-syncs clients that ack within the window. Idle clients miss the windows.

**Source IPs the work MBP was talking to (during the capture)**

```
160.79.104.10    4115 frames    Anthropic / Claude API
185.199.109.153  1038 frames    GitHub Pages
44.234.22.93     1007 frames    AWS
17.248.242.101    834 frames    Apple iCloud
66.33.22.231      822 frames    DreamHost
3.145.235.85      696 frames    AWS
```

So you were actively interacting with me (Claude API), GitHub Pages (the EOD HTML you were reviewing), Apple services. Heavy active traffic = heavy EAPOL-related TX activity = work MBP kept its TK + GTK sync fresh.

**Things I noticed that warrant your questions**

1. The 6 ICMP "port unreachable" at T=192.87s are interesting timing — exactly at trigger. Three from 8.8.8.8, one from 8.8.4.4. This is macOS's QUIC-over-UDP failing because the source port closed during the route table change. Normally invisible noise but here it timestamps the trigger event with sub-second precision.

2. The 3 rapid ARPs at T=284.7s (within 1ms each) suggest a TCP-stack-level retransmit storm. Something the work MBP was tracking (TCP keepalive? QUIC heartbeat?) wasn't getting through.

3. iPhone went silent for 163 seconds. During failure window we see ZERO frames from 62:75:85:f6:4e:3e between T=243s and T=355s. That's a 112-second gap of complete silence from iPhone. iPhone wasn't even broadcasting probes during that window — fully GTK-deaf and no unicast happening (probably no app traffic to drive it).

4. The IGMP membership reports for 224.0.0.251 are mDNS / Bonjour. Apple devices very chatty about service discovery. The bursts of IGMP correlate with the trigger and recoveries.

5. No EAPOL frames at all because we're capturing on en0 in normal mode (post-decrypt, no monitor mode). Confirms we'd need AP-side kdebug for that visibility.

What questions do you have? I can dig deeper on any specific frame range, MAC, IP, or pattern.

---

### User question 2 — operational mitigation question

> "No DHCP, no EAPOL and could not get AP1 _Kdebug. WHat can be done to limit the time that devices are 'off'?"

### Claude response 2 — layered mitigations ranked by impact

Good question — and the answer is a layered set of levers, ranked by impact. The 2-4 minute window isn't fixed; it's a function of specific timers and behaviors that you can tune.

**Why the offline window is THAT long**

The lag clients (iPhone, Personal MBP) recover when ONE of these happens:

| Recovery path | Default timing |
|---|---|
| AP retries Group Key Handshake (M1) to lagging client | Typically 60s retry interval, bounded retries |
| AP rotates GTK again periodically | Default WPA3: every 1-24 hours (rekey lifetime) |
| Client's DHCP lease expires → tries renewal → fails → re-associates | DHCP lease 50% renewal time = 1 hour for 2-hour leases, but tunable |
| Client roams or naturally re-associates | Variable — depends on signal/idle behavior |
| Client app activity triggers re-keying | Variable — Work MBP's heavy activity is why it stayed in sync |

The 2.7 / 4.3 minute durations you observed are likely the AP's internal GTK retry timer + client EAPOL backoff racing. The most idle client (Personal MBP at 98 frames) waited longest because it never sent traffic that would have prompted a unicast PTK refresh.

**Levers ranked by impact**

🟢 **Highest leverage — AP-side timers (XIQ Network Policy → SSID → Security)**

| Setting | Default | Suggested | Effect |
|---|---|---|---|
| Group Key Update Interval (GTK rekey lifetime) | 1 hour or longer | 5-15 min | More frequent re-syncs, bounded miss window |
| EAPOL retransmit timer (M1 retry) | ~1s, 4 retries | 0.5s, 6 retries | Faster GTK push to lagging clients |
| Group Key Handshake retry | 60s+ default | 30s | Faster re-attempt when ACK is missed |
| Disable GTK rekey on disassoc (security trade-off) | enabled | disabled | No rekey during state changes = no cascade. NOT recommended — drops PFS. |
| PMK caching / 802.11r (Fast Transition) | varies | enable | Re-association takes <100ms instead of full 4-way handshake |

To find these in XIQ:
- Network Policies → [your policy] → Wireless Networks → Corporate_Wireless → Security tab → Advanced
- Look for "Group Rekey", "Reauthentication Period", "Fast Transition" toggles

🟡 **Medium leverage — DHCP behavior**

| Lever | Effect |
|---|---|
| Increase DHCP lease time on EXOS SW1 (currently default 7200s = 2 hours) — set to 12-24h | Clients don't try to renew during the failure window — they just keep using their existing IP. Most clients use the IP for ~50% of lease before renewing. Longer lease = wider buffer against transient GTK loss. |
| Configure DHCP unicast flag in AP | If supported, AP unicasts OFFER/ACK back to client TK instead of broadcast GTK. Bypasses the GTK rotation problem entirely for DHCP. Check XIQ for "Unicast DHCP" or "DHCP Proxy" mode. |

🟡 **Medium leverage — proactive client recovery (script or manual)**

When you see the failure:
- Toggle iPhone Wi-Fi off → on — forces fresh 4-way handshake → fresh PTK + GTK_v2 → instant recovery
- Personal MBP: same — `networksetup -setairportpower en0 off; sleep 2; networksetup -setairportpower en0 on`

Script for automation:
```bash
# Watch for APIPA on Personal MBP, recover automatically
ip=$(ipconfig getifaddr en0)
case "$ip" in
  169.254.*) networksetup -setairportpower en0 off; sleep 2; networksetup -setairportpower en0 on ;;
esac
```

🟠 **Lower leverage but worth knowing**

| Lever | Effect |
|---|---|
| Static IP on iPhone / Personal MBP | Bypasses DHCP entirely. Iffy because they're mobile devices — but for the lab it works. |
| Separate Wi-Fi SSID for "stable" clients vs "lab" clients | Different BSSID = different GTK = no cascade between groups |
| Different AP for iPhone vs work MBP | If feasible — completely separate fault domain |

🔴 **Avoid the trigger entirely**

The cleanest solution: don't trigger it.

| Practice | Why |
|---|---|
| Don't plug en8 into the same SW1 fabric while en0 is associated | Eliminates the dual-interface event — no GTK rotation reason |
| Disable Wi-Fi on work MBP BEFORE plugging into Port 5 | Single-interface state — no race |
| Use a different switch entirely for work MBP wired | Different L2 domain — AP doesn't see the state change |

**My ranked recommendation for YOUR lab**

| # | Action | Implementation effort | Expected reduction |
|---|---|---|---|
| 1 | Increase DHCP lease time on SW1 to 24h | Low (1 EXOS command) | Significant — clients won't try to renew during 2-4 min cascade |
| 2 | Enable 802.11r Fast Transition + PMK caching in XIQ on Corporate SSID | Low (XIQ toggle) | Reduces re-association time from 4-way-handshake to ~100ms when client recovers |
| 3 | Reduce Group Key Update Interval from default to 5 min | Low (XIQ setting) | Forces frequent GTK refreshes — bounds the miss window |
| 4 | Write a recovery script that watches for APIPA and bounces Wi-Fi | Medium | Manual but reliable — sub-10-second recovery |
| 5 | Procedural: Wi-Fi off → plug en8 → Wi-Fi on | Zero (workflow change) | Eliminates the trigger entirely |

**The deeper question — is this an AP3000 firmware bug?**

Worth investigating because:
- WPA3 GTK rotation should be more robust than WPA2 (PMF protects management frames)
- Self-recovery in 2-4 min suggests a working but slow retry mechanism
- Other WPA3 deployments don't typically see this kind of cascade

Action: check AP3000 firmware version (`show version` on the AP), then check Extreme release notes for known issues around "Group Key Handshake" or "GTK rotation" in 6E radios. The behavior you observed is consistent with a known class of issues that have specific fixes in some firmware versions.

**TL;DR for limiting offline time**

Highest impact, lowest effort:
1. Toggle Wi-Fi off → on on affected iPhone/MBP — instant recovery (10-15 sec)
2. Increase DHCP lease to 24h — clients don't try to renew during the cascade
3. Enable 802.11r in XIQ — re-association is fast when it does happen
4. Don't trigger it: disable Wi-Fi on work MBP before plugging en8 into Port 5

The cascade is real but the recovery is bounded. The fastest practical fix is procedural — manual Wi-Fi toggle on lagging clients, or just don't dual-interface in the first place.

---

## Notes for GTAC / R&D conversation

This dialogue is preserved as-is for engineering reference. Synthesis files
(EOD HTML, structured memory references) are linked from the main session
report at:
https://khursheedkhanaiforgood-ai.github.io/5320-onboarding/session_summary_20260508.html

**Key data points to share with GTAC / R&D:**

- Platform: Extreme AP3000 + EXOS 5320, ExtremeCloud IQ managed
- Firmware versions: as captured in tech-support dumps (linked from EOD HTML)
- Encryption: WPA3-SAE-PMF, AES-CCMP, channel 161, 20 MHz, 11ax-5g
- Trigger: deterministic — wired interface (USB-C Ethernet) plugging into the
  same SW1 fabric while same client's Wi-Fi is associated
- Symptom: 2-of-N clients fail; trigger device immune; failure 2-4 min;
  self-recovery without intervention
- Reproduced multiple times; behavior consistent
- Captured 24 MB pcap from work MBP en0 spanning full failure cycle
- AP-side EAPOL trace not captured because `_kdebug wifi-driver msglevel wsec`
  flooded SSH session and dropped the connection

**Question for R&D:**
- Is this a known issue in current AP3000 firmware?
- What is the expected behavior of Group Key Handshake retry when M2 ACK is missed
  from a lagging client?
- Are the configurable knobs (Group Key Update Interval, EAPOL retransmit timer,
  802.11r) the right levers to mitigate, or is there a firmware fix?

---

End of verbatim dialogue.

This file is preserved unedited for engineering escalation. Refer to the EOD
HTML for analytical synthesis.
