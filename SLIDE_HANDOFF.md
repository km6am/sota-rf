# Handoff — build one summary slide (paste this to Claude in PowerPoint)

**Goal:** Build a single, polished 16:9 summary slide for the *SOTA Summit RF-Source Mapper* project (California / W6 findings). All content and design direction is below — nothing else needed. Verify it renders cleanly (no overflow/overlap) before finishing.

## Design direction
- **Theme:** dark "spectrum-analyzer / instrument panel" look (the subject is RF spectrum). Premium, single-dark-background.
- **Palette (hex):** background `0B1620` (deep slate) · cards `13273A` · hairlines `20374C` · text `E7EEF4` · muted text `8FA3B3` · **accent amber `E8A13A`** (energy/RF) · risk red `E3694A`.
- **Motif:** rounded stat cards + amber for the big numbers. Small amber square as the marker before each key-finding header.
- **Do NOT** use accent stripes/underlines under the title, edge bars, or cream backgrounds. Title left-aligned; body left-aligned.
- **Fonts:** Calibri throughout (bold for title/headers). Title ~38pt, stat numbers ~38pt, section headers ~14–16pt bold, body ~11–13pt, captions ~10.5pt muted.

## Content

**Title:** California Summit RF Environment
**Subtitle:** **SOTA RF-source mapper**  ·  605 summits mapped against 1.23 M FCC RF sources (ASR + ULS land-mobile/microwave + CDBS broadcast)
*(make "SOTA RF-source mapper" amber, the rest muted)*

**Four stat callouts** (big amber number / bold label / muted sub-line):
1. **605** — impacted summits — of 4,329 W6 summits
2. **30%** — MODERATE+ overload — 184 summits at real risk
3. **70 cm** — most-threatened band — 69 HIGH · 153 MOD+
4. **96 MW** — UHF-TV broadcast ERP — 37× all other bands

**Bar chart** — horizontal bars, title "Summits at HIGH overload risk, by ham band", red-orange bars (`E3694A`), data labels on, no legend, quiet axes:
| Ham band | HIGH summits |
|---|---|
| 70 cm | 69 |
| 23 cm | 42 |
| 2 m | 24 |
| 6 m | 4 |

**Key findings** (bold header + muted description, amber square marker):
1. **Broadcast powers it, land-mobile blankets it** — UHF-TV alone is 96 MW of ERP, but VHF/UHF/microwave land-mobile reach 330–399 summits each vs 121–131 for broadcast.
2. **70 cm is squeezed from both sides** — full-power UHF-TV (470–700 MHz) above and UHF public-safety/GMRS (450–470) below — the worst band by far.
3. **Distance-aware capture** — a field-strength model (ERP/d²) pulls in far megawatt masts, e.g. Sutro Tower's ~7 MW, 1.9 km from Mt Davidson.

**Speaker notes:** Summary of the SOTA RF-source mapper, W6 (California). 605 of 4,329 summits have an in-range fixed RF source; 184 (30%) show MODERATE-or-worse ham-band overload risk. 70 cm is the most-threatened amateur band. Data: FCC ASR structures + ULS land-mobile/microwave + CDBS broadcast = 1.23 M sources. Field-strength inclusion model captures distant high-ERP broadcast masts.

## Suggested layout (13.33" × 7.5")
- Title + subtitle across the top (~0.35–1.3").
- Row of 4 stat cards below (~1.6", height ~1.5"), evenly spaced with 0.5" side margins.
- Bottom half: bar chart in a card on the left (~55% width), the three key findings stacked on the right.
- Keep ≥0.5" slide margins and ~0.3–0.5" gaps between blocks.

*(A reference build already exists at `W6_RF_summary.pptx` in this project — same content — if you want to open it as a starting point.)*
