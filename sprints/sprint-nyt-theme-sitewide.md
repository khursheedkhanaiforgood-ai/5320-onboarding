# Sprint: NYT Theme — Apply Sitewide
**Branch:** `feature/theme-nyt`  
**Status:** In progress — `docs/index.html` done ✅  
**Target:** All `docs/*.html` pages rethemed to NYT article style

---

## Design Tokens (from index.html)

```
Fonts:     Libre Baskerville (serif body/headlines) + Libre Franklin (labels/nav/badges)
BG:        #FFFFFF
Text:      #111111
Muted:     #666
Blue:      #326891 (section labels, links, accent)
Rule-top:  3px solid #111
Rule-thin: 1px solid #111
Divider:   1px solid #E2E2E2
Max-width: 740px centered
```

## Pages To Retheme

| Page | Status | Notes |
|------|--------|-------|
| `docs/index.html` | ✅ Done | Landing — NYT masthead, byline, rule lines |
| `docs/deploy.html` | ⬜ Todo | Option A/B tabbed deploy guide |
| `docs/agent_tutorial.html` | ⬜ Todo | 43-prompt chat build tutorial + SVG |
| `docs/railway_tutorial.html` | ⬜ Todo | Railway build tutorial |
| `docs/onboarding.html` | ⬜ Todo | Live session log (login-gated) |
| `docs/ap_onboarding_20260326.html` | ⬜ Todo | AP session report |
| `docs/lab_20260329.html` | ⬜ Todo | Phase 2 lab guide |
| `docs/session_20260325.html` | ⬜ Todo | EXOS config capture report (login-gated) |
| `docs/session_log_20260325.html` | ⬜ Todo | Side-by-side agent+console (login-gated) |
| `docs/session_log_20260329.html` | ⬜ Todo | Mar 29 session log |
| `docs/session_summary_20260325.html` | ⬜ Todo | Session summary |
| `docs/standards_references.html` | ⬜ Todo | Standards + references glossary |
| `docs/troubleshooting_20260327.html` | ⬜ Todo | Troubleshooting guide |
| `docs/ue_onboarding_20260327.html` | ⬜ Todo | UE onboarding report |

## Approach

1. Extract shared CSS into a `<style>` block template (same tokens as index.html)
2. Apply to each page one-by-one — preserve all content, only change:
   - `<head>` fonts + CSS
   - Add NYT masthead (rule-top, rule-thin, logo label, byline) at top of body
   - Replace dark bg/accent colors with NYT light palette
   - Login-gated pages: keep login overlay, retheme the reveal
3. Commit all at once with: `feat(theme): apply NYT style sitewide — all docs pages`
4. Push `feature/theme-nyt` → open PR to `feature/auto-deploy-agent` (not main)

## Key Constraints
- Login-gated pages (`onboarding.html`, `session_20260325.html`, `session_log_20260325.html`) must keep their `admin/Extreme01!!` gate
- `#me` fragment on index.html reveals Railway URL + token — must be preserved
- Copyright notice `© 2026 Khursheed Khan` on every page — already on all pages, keep in place
- GitHub Pages serves from `feature/auto-deploy-agent` /docs — merge there, not main
