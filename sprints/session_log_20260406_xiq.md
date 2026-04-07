# Session Log — April 6 2026
## XIQ-Only Deployment: Two 5320 Switches + Two AP3000s

---

## Full Troubleshooting Flow (linked list)

### 1. STARTING POINT
- Goal: Deploy two 5320 switches + two AP3000s entirely via XIQ — no CLI
- Option A chosen: factory reset both switches, start clean
- Post-reset IPs (DHCP from Quantum Fiber):
  - SW1: 192.168.0.28 (serial FJ012544G-00483)
  - SW2: 192.168.0.11 (serial FJ012544G-00233)
- AP1 (SW1 port 3): 192.168.0.12
- AP2 (SW2 port 3): 192.168.0.25
→ **Next:** Claim switches in XIQ

---

### 2. XIQ — SEPARATE POLICIES CREATED
- Decision: one switch policy (Test_SW1) + two AP policies (one per AP)
- AP1 policy: VLANs 20/30
- AP2 policy: VLANs 50/60
- Switch policy: all VLANs + routing
→ **Next:** Configure VLAN Attributes

---

### 3. VLAN ATTRIBUTES — MINOR HICCUP
- Added VLANs 20/30/50/60 to VLAN Attributes
- Hiccup: table appeared empty multiple times after save
- Root cause: switches not yet confirmed assigned to Test_SW1 policy
- Resolution: persisted, eventually saved
→ **Next:** Network Allocation

---

### 4. NETWORK ALLOCATION — HICCUP: VLAN 1 MISTAKE
- Added 4 subnets: Corp_20 (10.20.0.0/24), Guest_30 (10.30.0.0/24), Corp_50 (10.50.0.0/24), Guest_60 (10.60.0.0/24)
- Used "first IP for IPv4 interface" → XIQ auto-set SVIs (10.20.0.1, 10.30.0.1 etc.)
- Also added VLAN 1 (192.168.0.0/24) trying to make static routes work
- Hiccup: adding VLAN 1 to Routing section broke XIQ↔switch communication
- Lesson: **VLAN 1 = XIQ lifeline. Never add to Routing section.**
- Fix: removed VLAN 1 from Routing → push restored
→ **Next:** Routing (assign SVIs)

---

### 5. ROUTING SECTION — PARTIALLY SUCCESSFUL
- Assigned SVIs to both switches for VLANs 20/30/50/60
- Enabled IPv4 Forwarding on all entries ✅
- XIQ successfully pushed: SVI IPs + IPv4 Forwarding (confirmed via show vlan — `f` flag)
- NOT pushed: DHCP server, default route
→ **Next:** Static Routes (default gateway)

---

### 6. STATIC ROUTES — BLOCKED (XIQ BUG/LIMITATION)
- Attempted: 0.0.0.0/0 → 192.168.0.1 on both switches
- Error: "next hop IP doesn't match any of the subnetworks configured for this device"
- Root cause (confirmed by Extreme Networks expert):
  - XIQ cannot push L3 features (static routes) via L2 interface
  - VLAN 1 (192.168.0.0/24) is unmanaged by XIQ (DHCP-assigned) so 192.168.0.1 is not a recognized next hop
  - This is a known XIQ behaviour/limitation
- XIQ CLI Script push also attempted → failed (SSH issue on XIQ side)
- Resolution: deferred to CLI
→ **Next:** Try to get into switch via console/SSH

---

### 7. SWITCH ACCESS — MULTIPLE FAILURES
- SSH attempt: failed (unknown password — set by XIQ during ZTP+ onboarding)
- Console cable: `/dev/cu.usbserial-A9XH7AI0` (different from previous session)
- Console login: failed (blank password not accepted — XIQ set unknown password)
- Tried: Extreme01!!, blank, admin, Extreme@Networks → all failed
- XIQ credential reset: searched in Global Settings → Credential Distribution Groups → Device Management Settings → could not find SSH password reset
- XIQ CLI scripting: found feature, pushed commands → no output, no execution
→ **Next:** Work around from XIQ side

---

### 8. PORT TYPE — DISCOVERED MISSING CONFIG
- Ran `show vlan` via console (eventually got in)
- Discovered: SW_VLAN_20 and SW_VLAN_30 showed 0/0 ports
- Root cause: XIQ Port Types not configured for port 3
- XIQ pushed SVI IPs but never tagged port 3 with VLANs 20/30/50/60
- This was a SECOND reason DHCP wasn't working (besides no DHCP server)
→ **Next:** Create Port Type in XIQ

---

### 9. PORT TYPE CREATION — SUCCESS ✅
- Created port type `AP_Port` in XIQ:
  - Type: Trunk (802.1Q)
  - Native VLAN: 1 (Default) — keeps AP management + XIQ comms alive
  - Allowed VLANs: 20, 30, 50, 60 — all data VLANs on one port type
  - Edge Port: Enabled — skips STP listening/learning for instant AP connectivity
  - PoE: ON
  - LLDP: ON
- Assigned `AP_Port` to port 3 on both switches via Switch Template
- Pushed via XIQ ✅
- Design decision: one port type for both switches → adding VLAN 10 in Sprint 2 is one change
→ **Next:** DHCP server + default route via CLI

---

### 10. CLI — DHCP + DEFAULT ROUTE (IN PROGRESS)
- Access via console: /dev/cu.usbserial-A9XH7AI0
- VLAN names confirmed: SW_VLAN_20, SW_VLAN_30 (from show vlan output)
- Commands being pushed:
  - configure vlan SW_VLAN_20/30 dhcp-range + gateway + dns
  - enable dhcp ports vlan SW_VLAN_20/30
  - configure iproute add default 192.168.0.1
  - save configuration
- SW2 still needs same (SW_VLAN_50, SW_VLAN_60)
→ **Pending:** Test iPhone IP + internet

---

## Key Decisions Made Today

| Decision | Reason |
|----------|--------|
| One switch policy for both switches | Simplifies management — one change covers both |
| All 4 VLANs (20/30/50/60) in one port type | Sprint 2 VLAN 10 = one update, both switches |
| Edge Port enabled on AP_Port | AP ports have no loop risk — instant STP convergence |
| CLI for DHCP + default route | XIQ L3/L2 limitation confirmed by Extreme expert |
| VLAN 1 never in XIQ Routing | Breaks XIQ management if added |

---

## XIQ vs CLI Boundary (learned today)

| Function | XIQ | CLI |
|----------|-----|-----|
| VLAN creation | ✅ | |
| SVI IP addresses | ✅ | |
| IPv4 Forwarding | ✅ | |
| Port type/trunk config | ✅ | |
| SSID → VLAN binding | ✅ (AP policy) | |
| DHCP server | ❌ | ✅ |
| Default route | ❌ (L3/L2 bug) | ✅ |
| DNS server config | ❌ | ✅ |

---

## Pending Before Session Close
- [ ] SW1: verify DHCP + default route working (iPhone gets 10.20.x/10.30.x)
- [ ] SW2: same commands for SW_VLAN_50 + SW_VLAN_60
- [ ] Test internet on iPhone (google.com)
- [ ] Save configuration on both switches

## Sprint 2 Items (from today's learnings)
- VLAN 10 management: add to AP_Port type + configure SVIs
- XIQ static route workaround: assign static VLAN 1 IPs before first push
- Find XIQ SSH credential reset path (Device Management Settings)
- Investigate XIQ CLI script SSH failure root cause
