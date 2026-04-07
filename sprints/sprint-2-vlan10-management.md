# Sprint 2: Management VLAN 10
**Branch:** `feature/auto-deploy-agent` (or new `feature/sprint2-vlan10`)  
**Status:** Planned — begin after VLANs 20/30/50/60 + APs verified end-to-end  
**Goal:** Isolate switch management traffic onto a dedicated VLAN 10, configured entirely via XIQ

---

## Why VLAN 10

Currently both switches are managed via VLAN 1 (Default) — same VLAN that handles AP provisioning and ZTP+. In an enterprise deployment, management traffic (SSH, SNMP, XIQ connectivity) should be isolated so:
- Users on VLANs 20/30/50/60 cannot reach switch management plane
- Management IP is predictable and stable (not DHCP from home router)
- Mirrors real-world enterprise practice

---

## Design

```
VLAN 1  (Default)   192.168.0.x/24   DHCP    AP provisioning + ZTP+ only
VLAN 10 (Mgmt)      10.10.0.0/24     Static  Switch management (SSH, XIQ, SNMP)
VLAN 20 (Corporate) 10.20.0.0/24     DHCP    SW1 AP — Corp SSID clients
VLAN 30 (Guest)     10.30.0.0/24     DHCP    SW1 AP — Guest SSID clients
VLAN 50 (Corp2)     10.50.0.0/24     DHCP    SW2 AP — Corp2 SSID clients
VLAN 60 (Guest2)    10.60.0.0/24     DHCP    SW2 AP — Guest2 SSID clients

SW1 management IP:  10.10.0.1/24  (static, on VLAN 10)
SW2 management IP:  10.10.0.2/24  (static, on VLAN 10)
```

---

## XIQ Steps

### Step 1 — Add VLAN 10 to Network Policy
- XIQ → Network Policies → your policy → VLANs → Add VLAN
- Name: `Mgmt`, ID: `10`, Purpose: Management
- Do NOT assign it to AP-facing ports

### Step 2 — Create Management VLAN Port Profile
- Port type: Tagged on uplink/trunk ports only
- Never untagged on AP ports (APs don't need management VLAN)

### Step 3 — Assign Management IP per Switch
- XIQ → Devices → select SW1 → Configure → Management → Management VLAN: 10 → IP: 10.10.0.1/24
- Repeat for SW2 → IP: 10.10.0.2/24

### Step 4 — Add static routes on Quantum Fiber router
- 10.10.0.0/24 → 192.168.0.28 (SW1 gateway)
- (Or manage directly from a device on VLAN 10 subnet)

### Step 5 — Push config via XIQ → verify
- SSH to 10.10.0.1 and 10.10.0.2 — confirm management reachable on VLAN 10
- Confirm VLAN 1 still works for ZTP+ / AP adoption

---

## Constraints
- EXOS: "Mgmt" is a reserved keyword — use "APMgmt" or "SwMgmt" as VLAN name, not "Mgmt"
- Do NOT remove VLAN 1 from uplink ports — XIQ connectivity will break
- Physical MGMT port on 5320 is separate — do not confuse with in-band VLAN 10
- Test SSH to VLAN 10 IP BEFORE removing VLAN 1 management access
