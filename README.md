# Poker Analyzer

A local tool that reads [PokerNow](https://www.pokernow.club) hand-history exports (JSON), finds leaks in
your play, tracks results over time and scouts opponents.

- **Find leaks:** rule-based flags (limping, loose early-position play, folding the big blind to
  min-raises), short-stack shove/fold checks against approximate charts, and all-in luck: what your
  all-ins actually won compared with what your equity was worth.
- **Track results:** stack graph for each tournament, results in big blinds by position, game and stack
  depth, and cumulative results across sessions (actual and all-in adjusted).
- **Scout opponents:** tracker stats for every player, plus every hand each one showed, with their betting
  line.

Hold'em and PLO are both parsed and get stats; equity uses `eval7` (Hold'em) and `phevaluator` (PLO).
Out of scope for now: LLM hand commentary, solver comparisons, other sites' formats.

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
| Leaks | Headline leak rates, flagged hands with the reason, all-in luck (expected vs actual) |
| Hands | Filterable hand list; select a hand for a step-by-step text replay with pot and stack sizes in BB |
| Opponents | Every opponent's stats, and each hand they showed with cards, board, betting line and result |

Selecting a row in any hand table opens its replay underneath.

## Project layout

```
pokeranalyzer/
  parser.py   # JSON → Hand/Action objects: positions, streets, pot and stack (in BB) at each decision,
              # table detection, chip-replay check
  db.py       # SQLite schema, idempotent import, rebuild from stored raw JSON, dashboard queries
  stats.py    # per-hand stat flags for any player, aggregation with sample sizes
  equity.py   # Hold'em + PLO equity (exact or seeded Monte Carlo), side pots, all-in luck
  leaks.py    # rule-based flags and short-stack push/fold checks
  ranges.py   # Hold'em hand classes, strength ranking, range notation ("77+, ATs+, KQo")
  replay.py   # text replays and compact betting lines
app/          # Streamlit: Home.py + pages/
data/raw/     # your exported JSON files (git-ignored)
docs/         # notes on the PokerNow export format
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
| Loose from early position | Played a hand from UTG/UTG+n outside `66+, A9s+, KTs+, QTs+, JTs, T9s, AJo+, KQo` |
| Folded BB to a min-raise | Folded a top-50% hand in the BB to a single raise of at most 2 BB |
| Folded a chart shove / Shoved outside the chart | At ≤ 15 BB effective and folded to you, compared with "shove the top X%" |
| Opened small when short | Limped or min-raised at ≤ 8 BB instead of shove-or-fold |
| Folded to / called a shove | In the BB facing a lone shove, compared with an approximate call range |

The short-stack charts are approximate chip-EV ranges with no antes and no ICM: the share of hands to
shove depends on players left to act and effective stack, and hands are ranked by equity against a random
hand. A deviation is flagged only when it is more than 7 percentile points outside the chart. All the
thresholds are constants at the top of `pokeranalyzer/leaks.py`.

## Assumptions and limits

- Tournament results (place, field size, prize) aren't in the export; enter them on the Import page.
- Antes, straddles, bomb pots and double boards aren't modelled; such hands import with a warning.
- Run-it-twice boards are parsed (later runs reuse cards already dealt), but no sample export has one yet.
- Imports are manual (download the JSON, drop it in).
