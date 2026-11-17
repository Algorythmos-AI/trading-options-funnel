# How the Radar picks names today (audit, 2026-11-17)

Private. Read from a copy of the Radar routine's files taken on 2026-11-17 from the claude.ai project
"Stocksdelta" > Library: `skills/pre-market-radar.skill` plus `radar-dashboard/` (sender, notes, test edition).
`radar/MANIFEST.sha256` lists every copied file with its sha256. Facts about 16–17 Nov come from that day's
edition, the project thread and the Options page, as noted where used. The files themselves are not committed: they mention
the tools that run them, which the org rule D-016 keeps out of git. The copy lives at `~/trading/var/radar-copyout/`
on the owner's Mac (git-ignored there).

## The pipeline

| Step | What happens | Rule or judgement |
|---|---|---|
| Schedule | A claude.ai routine at 10:30 UTC on weekdays (designed as 20:30 Sydney): 05:30 New York in the northern winter, 06:30 in summer, when most small caps have no pre-market trade yet. A sender routine posts the newest outbox file to the dashboard at 11:12 UTC | Fixed times. On 17 Nov the run missed its schedule and was run by hand (project thread) |
| Data | IBKR weekly bars (one year) for every candidate plus SPY/QQQ/IWM; Stocklake market pulse, movers, earnings and economic calendars, insiders, news; web research by helper agents | Sources vary per run. The 16 Nov edition says the Stocklake sign-in had expired, so its calendar was built by hand |
| Universe | Whatever the scans and web research surface; the owner's spec says their own watchlists are a reference, not the primary universe. At least half of DAILY RADAR must come from independent scans | Not fixed, not counted, not logged |
| Levels | `scripts/levels.py` on **weekly** bars: support = highest of pivot lows (5-week centred window), the 10/20/40-week averages and the last two weeks' low, below price; resistance = lowest pivot high or average above price; RS13/RS26 vs SPY; a 4-vs-13-week volume ratio | Deterministic |
| Lists | Six lists of at most 10 names (DAILY RADAR, SUPPORT PLAYS, BREAKOUT WATCH, MEGA CAP OPTIONS, SMALL CAP MOMENTUM, INSTITUTIONAL CONVICTION) | Mostly judgement; three numeric gates (below) |
| Grades | Evidence grade and chart grade, 1 to 3, from the report's ranking section | Judgement |
| Publish | Report artifact, the six IBKR watchlists edited in place, an outbox JSON for the dashboard | Deterministic from `picks.json` |
| Scorecard | Next run: each list's average close-to-close move vs SPY (or QQQ/IWM), "held its line" counts, written into the report and into `references/lessons.md` | Computed by the same routine that picked |

The only numeric gates: SUPPORT PLAYS within about 0–4% above support; BREAKOUT WATCH within about 0–2.5% of
resistance or at a record; SMALL CAP MOMENTUM $2–$20 on the day's IBKR price with a catalyst, above-average
volume, relative strength, enough liquidity and no promotion pattern (the volume source varies between runs). DAILY RADAR is "strongest evidence today"; INSTITUTIONAL CONVICTION is fresh insider buys, activist stakes
or confirmed fund stakes (with stated exclusions); MEGA CAP OPTIONS is the mega-caps plus SPY/QQQ, "usually stable".

## The printed record, as it stands

From the scorecard lines in `lessons.md` (25 Sep to 22 Oct, 15 sessions), DAILY RADAR beat SPY on 8 sessions and
lagged on 7, and the best days were carried by one or two names (for example a single +11.6% name). BREAKOUT WATCH
cleared few of its lines (0 of 3, 0 of 4, 1 of 5 on 19–21 Oct). These are the routine's own numbers, close to close
from before the report was written (see finding 3), so they are a description, not evidence either way.

## Findings

1. **The selection is editorial.** Apart from the three gates, the routine decides by judgement which names go on which
   list and how they rank, from a different mix of sources each day. It is careful about sourcing (source, date and
   confidence on every line, "NOT VERIFIED" over a guess), but the choice itself is not reproducible: two runs on
   the same morning can pick different names, and no record says why a name was left out.
2. **No universe and no rejects.** Nothing records how many names were looked at, which were considered, or why
   one was dropped. "Removed for breaking support" is the only exit reason the notes track.
3. **The scorecard cannot show skill.**
   - It starts from the prior close, which is before the report is written. The report already sees the
     pre-market move, so a name picked because it is up pre-market starts the score with a gain it never had to earn.
   - The horizon is one session, equally weighted, and compared only with SPY/QQQ/IWM. There is no comparison with
     the names it looked at and did not pick, so "beat SPY" mixes picking with sector and size effects.
   - The routine grades its own picks, and the results live as prose in `lessons.md` (a scorecard for most sessions since
     24 Sep), not as data. Names that came off the lists are checked only now and then, in prose (one note records removed
     names rising 3–10% the next day), never as data.
4. **Two level systems for the same stocks.** The Radar's levels come from weekly bars. The Options desk's
   ceiling and floor come from daily and hourly levels (`focus.py` / `wt.options.levels`). On 16–17 Nov the Radar's
   edition gave NVDA's line as Thursday's 148.80 low, while the Options card's put trigger for 17 Nov was 155.02.
   The Radar also re-anchors support to the nearest line below the last close, so its lines jump after big moves.
   Neither system knows about the other.
5. **The bridge that matters is missing.** MEGA CAP OPTIONS covers almost the same names as the Options desk's
   12, and the Radar pulls a 7-day earnings calendar every run. None of that reaches the Options picker, which is
   how NVDA became the #1 focus name for Tuesday 17 Nov, the day it reports after the close.
6. **Fragile operations.** One routine does research, levels, report, six watchlist edits and the dashboard
   payload in a 25–40 minute budget. A missed schedule (16 Nov), an expired data sign-in, or a missing connector
   degrades the edition, and the edition says so only in prose.

## What this means for the funnel

- To be the top of the funnel the Radar needs a machine-readable block alongside its prose: for each of the 12 desk
  names, event risk (earnings or macro inside the hold, with source and date), catalyst, bias, confidence and a veto
  reason code; plus how many names it scanned and the ones it rejected, with reason codes. That is Phase 1 of the plan.
- Its quality can only be measured after a hypothesis and an experiment are registered in trading-lab-research (plan
  step S6), from sealed editions, entering at the 09:30 open, against a fixed benchmark universe, never against the
  prior close.
- The weekly-versus-daily level difference is not a bug in either system, but the page shows both to the owner on
  the same morning without saying they differ. Which level the funnel uses is part of that registration.
