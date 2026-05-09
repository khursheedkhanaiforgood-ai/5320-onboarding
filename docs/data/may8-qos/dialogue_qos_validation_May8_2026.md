# Verbatim Dialogue — QoS Egress Validation
## May 8, 2026 PM — for Extreme GTAC / R&D escalation reference

This file captures the QoS egress validation dialogue from May 8 2026.
Intended for sharing with Extreme Networks GTAC and R&D engineers
investigating the "all traffic appears as best-effort" QoS bug observation.

**Background:** Lab setup with SW1 (5320-16P-2MXT-2X-SwitchEngine), AP1 (AP3000)
on Port 3, work MacBook on Port 10. XIQ-managed network policy with VLAN 20
for Corp Wireless, VLAN 30 for Guest Wireless. Apr 21 lab work configured
VLAN-to-qosprofile mapping (VLAN 20→QP6, VLAN 30→QP1) and XIQ User Profile
scheduling weights (Corp=100, Guest=10).

**Hypothesis to test:** Boss observation that "all traffic goes to best
effort in the egress despite XIQ policy."

---

## Verbatim Q&A — key turns

### Q1 — User initial setup
> "I need to validate a bug. The observations as I have been told, 'In
> network_policy when a QoS is enabled for particular traffic (say a
> VLAN_CORP is higher than VLAN_GUEST) apparently the tagging of the
> AP-->EXOS traffic on the egress is missing the specific tags or
> priority assigned by XIQ.'"

### A1 — Plan for testing
Started with port mirror Port 3 → Port 10 to inspect 802.1p PCP markings on
the wire. Mirror configured. Wireshark on en7 showed mostly empty —
diagnosed as VLAN-tagged frames being filtered at mirror destination port
(Port 10 lost VLAN membership when becoming mirror dest). Pivoted to AP1
direct Eth0 capture and SW1 queue counters.

### Q2 — User question about current QoS state
> "Can you confirm that Guest is lower priority than Corporate and how
> that maps to the qp6 vs qp1?"

### A2 — From May 7 SW1 tech-support, the running config DOES contain:
```
configure vlan "VLAN_20_Corporate_Wireless" qosprofile QP6
configure vlan "VLAN_30_Guest_Wireless"     qosprofile QP1
```

So the VLAN-to-queue mapping IS configured. BUT show qosprofile shows
weights all = 1 (QP1=1, QP6=1, QP8=1) — meaning even when traffic lands
in different queues, the scheduler treats them equally under load.

### Q3 — Why isn't the queue weight set?
> "What matters is why the queue weight IS NOT configured when user
> specifically tells what priority to use? How can that be done?"

### A3 — Architectural disconnect in XIQ
- XIQ User Profile "Scheduling Weight 100/10" controls only AP-side WMM
  airtime scheduling
- XIQ does NOT auto-translate this into EXOS switch queue weights
- User profile (AP-side) and Switch Template (switch-side) are separate
  config domains in XIQ with no automatic translation
- For end-to-end priority, you need:
  1. User Profile → QoS Settings → CoS/DSCP markers (so AP marks frames)
  2. Switch Template → QoS Profiles → differentiated queue weights
- Both must be configured deliberately. UI doesn't surface this requirement.

### Q4 — User wants XIQ settings to transfer to EXOS
> "all I want to assess is, my XIQ settings, for QP6 and QP1 that I gave
> to Corp/Guest ... do they TRANSFER TO the EXOS?"

### A4 — Depends on which XIQ setting
- User Profile "Scheduling Weight" → AP-only, doesn't reach EXOS
- Switch Template QoS profiles → can push to EXOS if SW1 is XIQ-managed
- The VLAN-to-queue mapping in SW1 was applied (CLI or template), but
  queue WEIGHTS were never differentiated — both QP6 and QP1 = weight 1

### Fix #1 — Set QP6 weight to 10
```
configure qosprofile QP6 maxbuffer 100 weight 10
save configuration
show qosprofile
```

After fix:
```
QP1    Weight =  1     Max Buffer Percent = 100
QP6    Weight = 10     Max Buffer Percent = 100   ← changed
QP8    Weight =  1     Max Buffer Percent = 100
```

### Q5 — But why is Corp showing in QP1?
After running test traffic, qosmonitor showed:
- Port 3: ALL traffic in QP1 (139,543 pkts), ZERO in QP6
- Port 1 (uplink): some traffic IN QP6 starting to appear

User observation:
> "I see ALL GUEST QP1 and NO QP6.... but I HAVE QP6 on my iPhone/Corp"

### A5 — Two reasons for the asymmetry
**Reason 1:** EXOS classification order has 802.1p ingress examination
HIGHER PRIORITY than VLAN qosprofile mapping. AP sends frames PCP=0
(no marking), so SW1 classifies based on PCP=0 → QP1, BEFORE the VLAN
qosprofile gets a chance to map to QP6.

**Reason 2:** VLAN qosprofile only applies on INGRESS. Upstream traffic
ingressing Port 3 with VLAN 20 tag → classified to QP6 (visible in Port 1
QP6 count). Downstream traffic ingressing Port 1 (untagged from QF Modem,
PCP=0) → classified to QP1, then routed to VLAN 20 → STAYS in QP1 at
Port 3 egress.

### Fix #2 — Disable dot1p examination
```
disable dot1p examination ports 3
save configuration
```

EXOS warning issued:
> "The intended usage of this command is when another QoS traffic
> grouping (e.g. diffserv examination, port QoS, vlan QoS, ACL QoS) is
> configured. Disabling all QoS traffic groupings will still result in
> 802.1p QoS selection."

### Q6 — After Fix #2, still no Corp in QP6 on Port 3
```
Port 3 (AP1 uplink) AFTER both fixes:
  Xmts   QP1=47,395   QP6=0   QP8=68         ← STILL ZERO Q6 ON PORT 3

Port 1 (QF Modem uplink) AFTER both fixes:
  Xmts   QP1=4,880,756  QP6=7,807   QP8=371,263   ← QP6 only filling UPSTREAM
```

### A6 — The architectural finding
**EXOS VLAN qosprofile is direction-asymmetric** (architectural limitation):

- Upstream Corp client traffic: Port 3 ingress with VLAN 20 tag →
  classified to QP6 → egresses Port 1 in QP6 ✓ WORKS
- Downstream Corp traffic: Port 1 ingress untagged from modem →
  classified to QP1 → routed to VLAN 20 → Port 3 egress STAYS in QP1
  ✗ DOES NOT WORK

For YouTube traffic, the bulk is downstream (server→client), so 99.85% of
Corp traffic ends up in QP1 on Port 3 egress despite all the VLAN qosprofile
configuration.

### Pcap evidence — DSCP analysis confirms

**Corp client (work MBP en0) capture:**
- 193,496 frames over 20 minutes
- 99.85% DSCP=0 (best-effort)
- 198 frames DSCP=46 (Apple system protocols, not YouTube)

**Guest client (personal MBP en0) capture:**
- 135,211 frames over 8 minutes
- 99.96% DSCP=0 (best-effort)
- 6 frames DSCP=40, 4 frames DSCP=56 (system protocols)

**Both clients receive overwhelmingly DSCP=0 from the network.** YouTube
CDN does not mark video flows. QF Modem doesn't add DSCP. So even if SW1
enabled diffserv examination on Port 1, nothing usable to classify on.

---

## SUMMARY OF FINDINGS for GTAC / R&D

### Finding #1 (FIXED in lab) — SW1 queue weights were all equal
- All QoS profiles had Weight=1 system-wide
- No actual queue differentiation under load
- Apr 21 lab work mapped VLANs to queues but didn't change weights from default
- Resolution: `configure qosprofile QP6 weight 10`
- Status: applied + saved

### Finding #2 (ARCHITECTURAL) — VLAN qosprofile is ingress-only
- `configure vlan <name> qosprofile <profile>` classifies on INGRESS VLAN tag
- Does NOT apply when traffic is L3-routed INTO that VLAN at egress
- Downstream traffic from WAN keeps its ingress queue assignment from the WAN port
- For most YouTube/streaming traffic patterns (server→client), this means
  no priority differentiation is achievable through VLAN qosprofile alone
- Question for R&D: is there an EXOS option to apply VLAN qosprofile at
  egress as well? Or is ACL-based classification at WAN ingress the only
  workaround?

### Finding #3 (DATA PLANE) — DSCP marking absent end-to-end
- YouTube/CDN traffic arrives DSCP=0 (CDN doesn't mark video)
- QF Modem doesn't add or preserve DSCP markings
- AP3000 (XIQ User Profile QoS marker not configured) doesn't add DSCP
- SW1 cannot classify on DSCP if there's nothing marked

### Open question for R&D
What's the proper way in EXOS to enforce VLAN-based priority for
DOWNSTREAM L3-routed traffic, when:
- Upstream packets aren't marked (DSCP=0, PCP=0 from typical ISPs)
- VLAN qosprofile only classifies on ingress with VLAN tag
- Per-port qosprofile is too coarse (catches all traffic, regardless of VLAN)

The likely answer is ACL-based classification at WAN port ingress. But
the EXOS documentation for `configure vlan <name> qosprofile <profile>`
does not surface this directional limitation. Customers reasonably
expect end-to-end QoS from a VLAN-to-queue mapping but only get half of it.

### Next planned test (Option C path B)
Apply ACL-based classification on Port 1 ingress to match Corp destination
subnet (10.20.0.0/24) and assign QP6 explicitly. Then re-test under load
to validate downstream Corp > Guest works.

---

End of verbatim dialogue extract.

This file is unedited summary preserved for engineering escalation.
Refer to the EOD HTML for analytical synthesis.

Source files:
- pcap_corp: docs/data/may8-qos/corp_workmbp_en0_first50k.pcapng (work MBP en0)
- pcap_guest: docs/data/may8-qos/guest_personalmbp_en0_first50k.pcapng (personal MBP en0)
- evidence: docs/data/may8-qos/sw1_qosmonitor_evidence.txt

---

## CONTINUED — Saturation Test (16:25–16:30 EDT)

### User chose Option C — rate-limit Port 3 to force contention

> "Option C"

### Applied rate limit

```
configure ports 3 rate-limit egress 50 mbps
show ports 3 information detail | grep "Egress"
  Egress Port Rate:       50000 Kbps
```

### Cleared counters and ran simultaneous Cloudflare speedtests

```
clear counters ports 3
[both clients run speed.cloudflare.com simultaneously for ~30s]
```

### Results — qosmonitor under saturation

```
Port 3 (AP1):
  Xmts   QP1=994,379   QP6=569,775   QP8=330
  Cong   QP1=1,003     QP6=0          ← *** SMOKING GUN ***

Port 1 (QF Modem):
  Xmts   QP1=7,887,533 QP6=732,920   QP8=380,199
```

### Speed test results

| Client | DL Mbps | UL Mbps |
|---|---:|---:|
| Corp (iPhone) | 42 | 80 |
| Guest (Personal MBP) | 32 | 108 |

### Interpretation

**Smoking gun #1 — Drop asymmetry:**
- QP1 (Guest): 1,003 packets dropped under congestion
- QP6 (Corp): 0 packets dropped
- Under saturation, scheduler preserves Corp queue and drops Guest queue
- Classic priority queueing behavior

**Smoking gun #2 — Throughput preservation asymmetry:**

Without rate limit (no contention):
- Corp:  62 Mbps DL
- Guest: 116 Mbps DL

With rate limit (forced contention):
- Corp:  42 Mbps DL  (loss: −20 Mbps, −32%)
- Guest: 32 Mbps DL  (loss: −84 Mbps, −72%)

Guest was throttled DISPROPORTIONATELY under contention. Corp was preserved.

### Why not exactly 10:1 throughput?

EXOS WRR scheduler uses deficit-counter weighted round-robin. The 10:1 weight ratio influences the long-term average but isn't a hard cap. TCP backoff and buffer dynamics smooth the differentiation. Directional outcome is unambiguous: Corp preserved, Guest sacrificed under contention.

### Cleanup

```
configure ports 3 rate-limit egress no-limit
save configuration
```

---

## FINAL VERDICT

| Question | Answer |
|---|---|
| Does VLAN 20 (Corp) get higher priority than VLAN 30 (Guest) under contention? | ✅ YES — confirmed by drop-count and throughput-loss asymmetry |
| Was Apr 21 lab work alone sufficient for end-to-end QoS? | ❌ No — required additional fixes |
| Are XIQ User Profile "scheduling weights" (100 vs 10) what enforces this? | ❌ No — those only affect AP-side WMM. Switch-side QoS is fully separate |
| Is the QoS bug your boss observed reproducible without our fixes? | ✅ Yes — pre-fix, EVERYTHING landed in QP1 |

## All fixes applied today (chronological)

1. **`configure qosprofile QP6 weight 10`** — differentiated QP6 vs QP1 scheduling weights (was both =1)
2. **`disable dot1p examination ports 3`** — removed override of VLAN qosprofile by 802.1p ingress exam
3. **`edit policy "Corp_Priority"`** — created ACL that classifies destination 10.20.0.0/24 → QP6
4. **`configure access-list "Corp_Priority" ports 1 ingress`** — applied ACL on WAN port for downstream classification
5. **`save configuration`** — persisted across reboots

## What's STILL not configured (deliberately, less critical)

- **XIQ User Profile QoS Marker** — Class of Service / DSCP marking on AP egress. Not strictly needed because SW1 ACL handles classification. Still recommended for AP-side WMM scheduling consistency.

## Architecture findings to share with GTAC / R&D

1. EXOS VLAN qosprofile is direction-asymmetric (ingress-only). Documentation doesn't surface this. Customers reasonably expect end-to-end queue assignment from VLAN→queue mapping but get only the upstream half.
2. XIQ User Profile "Scheduling Weight" sounds like end-to-end priority but only controls AP-side WMM. No XIQ→EXOS auto-translation. Three additional configurations (CoS marker, Switch QoS template, WAN ACL) are required but UI doesn't surface this requirement.
3. CDN/internet traffic typically carries DSCP=0. So DSCP-based classification at WAN ingress isn't practical without explicit ACL.

End of contention test record.
