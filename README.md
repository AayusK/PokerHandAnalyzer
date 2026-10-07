# Poker Analyzer

A local tool that reads [PokerNow](https://www.pokernow.club) hand-history exports (JSON), finds leaks in
your play, tracks results over time and scouts opponents.

- **Find leaks:** rule-based flags (limping, loose early-position play, folding the big blind to
  min-raises), preflop decisions compared with GTO charts (computed Nash push/fold charts for short
  stacks, with the chip EV each deviation cost, and your own charts for deeper spots), and all-in luck:
  what your all-ins actually won compared with what your equity was worth.
- **Track results:** stack graph for each tournament, results in big blinds by position, game and stack
  depth, and cumulative results across sessions (actual and all-in adjusted).
- **Scout opponents:** tracker stats for every player, plus every hand each one showed, with their betting
  line.

Hold'em and PLO are both parsed and get stats; equity uses `eval7` (Hold'em) and `phevaluator` (PLO).
Out of scope for now: LLM hand commentary, postflop and deep-stack solving (bring your own charts for
deep-stack preflop spots), other sites' formats.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app/Home.py
```

Download a hand history from PokerNow (the JSON export) and drop it on the **Import** page. You can enter
finishing place, field size, buy-in and prize at import time or later, since the export doesn't include
them. Imports skip hand ids that are already stored, so re-importing a file never creates duplicates.

The database lives at `data/poker.db` (set `POKER_DB` to use another file) and imported files are copied
to `data/raw/`. Both are git-ignored, because exports contain other players' names.

Run the tests with `python -m pytest`.

## Sample size

One tournament is about 40 hands, far too few for VPIP, PFR and the like to mean anything; those need
hundreds to thousands of hands. So single sessions are judged hand by hand (flags, short-stack checks,
all-in equity) and the aggregate stats firm up as the database grows. Every stat in the UI shows the
number of chances it was measured on, e.g. `22% (n=9)`.

## Dashboard

| Page | What it shows |
|---|---|
| Import | Upload exports ("N hands added, M duplicates"), edit tournament results, rebuild or delete sessions |
| Results | Stack across a tournament (table changes marked), net BB by position / game / starting stack, cumulative results across sessions with an all-in adjusted line, biggest wins and losses |
| My stats | VPIP, PFR, open raise, limp, 3-bet, flop c-bet, fold to c-bet, WTSD, W$SD, BB fold vs min-raise, aggression factor and frequency, sliced by position, game, stack depth, table size or session |
| Leaks | Headline leak rates, flagged hands with the reason, GTO chart comparison (agreement and EV lost, the 13x13 chart for each spot), all-in luck (expected vs actual) |
| Hands | Filterable hand list; select a hand for a step-by-step text replay with pot and stack sizes in BB |
| Opponents | Every opponent's stats, and each hand they showed with cards, board, betting line, result and GTO chart comparison |
| GTO Charts | Nash push/fold explorer (players left to act, stack, ante), and your own charts with any files that failed to load |

Selecting a row in any hand table opens its replay underneath.

## Project layout

```
pokeranalyzer/
  parser.py   # JSON → Hand/Action objects: positions, streets, pot and stack (in BB) at each decision,
              # table detection, chip-replay check
  db.py       # SQLite schema, idempotent import, rebuild from stored raw JSON, dashboard queries
  stats.py    # per-hand stat flags for any player, aggregation with sample sizes
  equity.py   # Hold'em + PLO equity (exact or seeded Monte Carlo), side pots, all-in luck
  leaks.py    # rule-based flags, plus flags from the GTO chart comparisons
  gto.py      # Nash push/fold solver, and comparing short-stack decisions with it
  charts.py   # loading your own GTO chart files, and comparing decisions with them
  ranges.py   # Hold'em hand classes, strength ranking, range notation ("77+, ATs+, KQo:0.5")
  replay.py   # text replays and compact betting lines
  data/preflop_equity.json  # class-vs-class preflop equity (built by scripts/build_equity_matrix.py)
app/          # Streamlit: Home.py + pages/
data/raw/     # your exported JSON files (git-ignored)
data/charts/  # your own GTO chart files (optional)
docs/         # notes on the PokerNow export format, the GTO chart format and an example chart
scripts/      # build_equity_matrix.py
tests/        # parser, stats, equity, db and leak tests on an anonymised sample tournament
```

## How things are worked out

**Positions** come from who actually posted the blinds, so dead buttons and dead small blinds (both common
in tournaments as players bust) come out right. Non-blind seats are named from the button backwards
(BTN, CO, HJ, LJ) with UTG, UTG+1… filling the front; with three or fewer non-blind players they are HJ /
CO / BTN. Heads-up, the dealer posts the small blind and is labelled BTN. Position groups: EP = UTG…,
MP = LJ/HJ, LP = CO/BTN, Blinds.

**Tables.** Exports have no table id and hand numbers restart at each table, so a change of your seat or
a break in hand numbers starts a new table. Hands are ordered by `startedAt`, not by number.

**Chip replay.** For every hand, chips awarded must equal chips put in, and each player's
`start + won − invested` must equal their stack the next time they are dealt in at the same table (you
are followed across tables too). The sample tournament balances with zero mismatches.

**Stats.** Hands with no preflop decision (a walk, or all-in from posting) are left out of preflop stats.
3-bet opportunity = facing exactly one raise. C-bet opportunity = preflop raiser, first to act on the flop
or checked to. Fold to c-bet = facing that c-bet with no raise in between. WTSD is measured on hands where
you saw the flop. AF = postflop (bets + raises) / calls.

**All-in luck.** When a hand ends at showdown with someone all-in and cards still to come, equity is
measured at the last decision (exact with two or fewer cards to come, Monte Carlo with a fixed seed
otherwise), split across main and side pots, with folded hands that were shown treated as dead cards.
Luck = chips won − equity × pot. It needs every remaining player's cards.

**Leak flags** (Hold'em-specific checks use your hole cards; PLO gets the limp flag and the aggregate
rates):

| Flag | Rule |
|---|---|
| Limped in | First in with a call (or completing the SB), or over-limping |
| Loose from early position | Played a hand from UTG/UTG+n outside `66+, A9s+, KTs+, QTs+, JTs, T9s, AJo+, KQo` (above 15 BB) |
| Folded BB to a min-raise | Folded a top-50% hand in the BB to a single raise of at most 2 BB |
| Folded a Nash shove / Shoved outside the Nash range | Folded to you at ≤ 15 BB effective: you folded a hand the Nash push/fold chart shoves (or shoved one it folds) |
| Folded a Nash call vs a shove / Called a shove outside the Nash range | Facing a lone first-in shove at ≤ 15 BB: folded a hand the Nash chart calls (or called one it folds) |
| Opened small when short | Limped or raised less than all-in, first in at ≤ 8 BB, instead of shove-or-fold |
| Off your GTO chart | One of your own charts takes the action you took less than 10% of the time |

A push/fold flag needs the chart to take your action less than 10% of the time and the deviation to cost
at least 0.05 BB of chip EV; the message gives the chart frequency and the EV lost. The thresholds are
constants at the top of `pokeranalyzer/leaks.py`.

**GTO charts.** The first preflop decision of every player whose cards are known (you, and opponents
who showed) is compared with a chart when one fits the spot, and stored with the chart's frequencies for
that hand, the verdict (agrees: the chart takes that action at least 50% of the time; mixed: 10–50%;
deviates: under 10%) and, for push/fold, the chip EV lost.

- *Nash push/fold* (built in). At 15 BB effective or less, first-in decisions and calls of a lone
  first-in shove are compared with the Nash equilibrium of the push-or-fold game for that exact spot:
  the hand's stacks, blinds, antes and players left to act. It is solved on the fly (usually in under a
  tenth of a second) from a precomputed class-vs-class preflop equity matrix with card removal. The
  model simplifies: chip EV with no ICM, so near the bubble or a pay jump a flagged fold can still be
  right; push or fold only (a limp or smaller raise is shown as off-chart); at most one caller, since
  everyone behind a caller is assumed to fold; and callers' hands are independent of each other. That
  last one overstates the shover's fold equity with many players left to act (a folder's low cards
  leave more high cards for the players behind), so early-position push ranges come out slightly too
  wide and the EV of a marginal hand can be off by up to about 0.07 BB.
- *Your own charts.* For deeper spots (raise first in, and facing a single open) you supply the ranges
  as JSON files in `data/charts/` (or the folder `POKER_CHARTS` points to), e.g. exported from a solver
  or training tool you have a licence for. These carry frequencies only, so no EV. The format, matching
  rules and an illustrative example are in [docs/gto-charts.md](docs/gto-charts.md).

Charts are applied when hands are imported. After adding or changing a chart file, use **Rebuild** on
the Import page to compare stored hands with it; a database created before GTO comparisons existed also
gets them (and the Nash-based leak flags) on Rebuild.

## Assumptions and limits

- Tournament results (place, field size, prize) aren't in the export; enter them on the Import page.
- Straddles, bomb pots and double boards aren't modelled; such hands import with a warning and get no
  GTO chart comparison. Antes are used by the push/fold model but not by the chip replay.
- Run-it-twice boards are parsed (later runs reuse cards already dealt), but no sample export has one yet.
- Imports are manual (download the JSON, drop it in).
