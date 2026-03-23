# Extreme Networks 5320 Onboarding Guide

## Overview
Complete step-by-step guide for onboarding the Extreme Networks **5320-16P-2MXT-2X** switch from factory (Fabric Engine Pre-GA) to ExtremeCloud IQ (XIQ) managed EXOS.

This guide was produced from a live session on **March 23, 2026** and covers every issue encountered and resolved.

---

## Quick Reference

| Item | Value |
|------|-------|
| Model | 5320-16P-2MXT-2X |
| Initial Firmware | Fabric Engine 9.2.0.0_B888 (Pre-GA) |
| Final Firmware | EXOS SwitchEngine 33.5.2b118 |
| Serial Baud Rate | **115200** |
| Fabric Engine Console Login | `rwa` / `rwa` |
| EXOS Default Login | `admin` / *(blank)* |
| Management | In-band via any switch port (no dedicated MGMT port) |
| XIQ Onboarding | ZTP+ automatic via DHCP + internet on port 1 |

---

## Files

| File | Description |
|------|-------------|
| `index.html` | Full interactive HTML guide with navigation and color-coded commands |
| `5320_Onboarding_Guide.pdf` | PDF version for offline/print use |
| `README.md` | This file |

---

## Key Lessons Learned

1. **PuTTY on macOS cannot configure serial parameters** — use `screen` or `minicom` instead
2. **Baud rate is 115200**, not 9600
3. **`admin` user is blocked from ACLI console** in Fabric Engine Pre-GA firmware by design
4. **Use `rwa` / `rwa`** to log in via console on Fabric Engine
5. **Factory reset** = delete `/intflash/config.cfg` then `boot` (no reset button on 5320-16P)
6. **No dedicated MGMT port** — plug any RJ45 port into internet-connected network for ZTP+
7. After factory reset, **ZTP+ automatically onboards** to XIQ and pushes EXOS firmware
8. After EXOS migration, login with **`admin` / blank password**

---

## Onboarding Steps (Summary)

```
1.  Connect USB-C serial console cable to switch
2.  TERM=vt100 screen /dev/cu.usbserial-XXXXXXXX 115200
3.  Wait for full boot (do not touch keys during boot log scroll)
4.  Login: rwa  |  Password: rwa
5.  enable
6.  delete /intflash/config.cfg  → y
7.  boot  → y
8.  Wait for reboot, login: rwa / rwa (set new password)
9.  Plug port 1 RJ45 into internet-connected network
10. Watch for: "Cloud IQ Agent is connected to Cloud IQ Manager"
11. XIQ auto-pushes EXOS firmware → switch reboots
12. Login: admin / (blank)
13. Complete setup wizard
14. save configuration
15. Verify switch online in XIQ portal
```

---

## Future: Automated Agent Deployment

This repository will be extended with:
- [ ] Agent scaffold for automated 5320 deployment
- [ ] EXOS provisioning script (VLANs, ports, SNMP)
- [ ] XIQ API integration for bulk onboarding
- [ ] Claude Code agent commands for switch deployment workflows

---

*Generated with Claude Code — March 23, 2026*
