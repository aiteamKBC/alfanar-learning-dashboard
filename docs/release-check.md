# Final client-presentation release check

The attendance presentation distinguishes three separate facts:

- Confirmed delivered lectures: Advanced has 12, confirmed by the owner.
- Sessions with attendance evidence: Advanced 9, Operations 14, PCP 16, Risk 12.
- Known attendance observations: explicit learner-session records used by the existing rates. Missing observations remain unavailable and excluded.

Advanced's module card, filtered attendance KPI and learner attendance views visibly state: **Only 9 of 12 confirmed lectures have attendance data. The percentage applies to known observations, not all 12 lectures.** This is visible with tooltips closed. Its 100% rate is 128 present observations out of 128 known observations; evidence coverage within the nine dated sessions is 128/225 = 56.9%. The three lectures without source attendance columns remain outside the measurable denominator.

| Module | Sessions with evidence | Present / known observations | Existing rate | Known / possible observations within dated sessions | Evidence coverage |
|---|---:|---:|---:|---:|---:|
| Advanced | 9 of 12 owner-confirmed lectures | 128 / 128 | 100.0% | 128 / 225 | 56.9% |
| Operations | 14 | 211 / 282 | 74.8% | 282 / 364 | 77.5% |
| PCP | 16 | 308 / 336 | 91.7% | 336 / 416 | 80.8% |
| Risk | 12 | 247 / 286 | 86.4% | 286 / 324 | 88.3% |

All four delivered lecture counts are now officially owner-confirmed: Planning 16, Procurement Risk 14, Operations 16 and Advanced 12. The separate attendance evidence session counts remain 16, 12, 14 and 9 in that order. Evidence coverage measures completeness of the dated matrix; it is distinct from attendance and from the fraction of confirmed lectures represented. Binary attendance cannot verify duration.

This release check changes frontend presentation only. Backend source, calculations, identities, analytics database tables, source workbooks and `.env` are checked against a pre-edit baseline. No live sync or attendance import is needed for these UI changes. Tests run in a separate temporary database.

Validation: final TypeScript/Vite production build; 38 focused backend tests covering attendance, matrices and metric APIs; 23 Chromium checks covering desktop/mobile module cards, all four filtered KPIs, visible warnings with tooltips closed, learner profiles, collapsed attendance tables, the directory and unchanged source/domain data. Detailed results and screenshots are kept privately in ignored `data/release-presentation-check/`.

There is no local implementation blocker once those checks pass. Missing Advanced lecture data, unmatched source emails and documented date interpretation remain source limitations; the interface discloses them and does not claim full-cohort or duration-based attendance. Deployment still requires the target VPS's normal backup, migration, configuration and HTTPS verification.

Follow [the exact Ubuntu deployment procedure](presentation-release.md). Deploy source through the verified Git commit and transfer attendance workbooks privately. The source-only archive and matching checksum are a fallback/reference for that commit. Stop on any checksum, backup, migration, sync, import, audit, build or smoke-test failure. Preparing and pushing the Git release does not authorize or perform a production deployment.
