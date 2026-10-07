# GTO charts

Your preflop decisions are compared with game-theory-optimal (GTO) charts from two places:

- **Computed push/fold charts** (source `nash`). Built in, nothing to set up. At 15 BB effective or less,
  first-in shove-or-fold decisions and calls of a lone shove are compared with the Nash equilibrium of
  the push-or-fold game for that exact spot (chip EV, no ICM). These also give the chip EV you lost.
- **Your own charts** (this page). For deeper spots the analyzer can't solve, you supply the ranges, for
  example exported from a solver or training tool. Two situations are covered: **raise first in**
  (`rfi`) and **facing a single open raise** (`vs_open`).

For each hand, the first preflop decision of every player whose cards are known (always you, and
opponents who showed) is compared with the first chart spot that fits it. Recorded: the chart's frequency
of each action for that hand class, the action that was taken, and a verdict:

| Verdict | The chart takes the action that was taken... |
|---|---|
| agrees | at least 50% of the time |
| mixed | 10% to 50% of the time |
| deviates | less than 10% of the time |

User charts carry frequencies only, so there is no EV loss for them.

Not compared: later preflop decisions (facing a 3-bet, squeezes, limped pots), postflop play, PLO, hands
where the player's cards are unknown, and hands with a straddle, bomb pot or double board.

## Where the files go

Put each chart in its own `.json` file in `data/charts/` (create the folder), or point the
`POKER_CHARTS` environment variable at another folder. All `*.json` files in that folder are read, in
file-name order; other files are ignored, and no folder means no charts.

Charts are applied when hands are imported or rebuilt, so after adding or changing a chart, use
**Rebuild** on the Import page to compare your existing hands with it.

A file with a problem is skipped and never stops the app; the GTO Charts page lists each skipped file
and the reason.

Ranges exported from a paid tool are normally covered by its licence: keep the files to yourself. Don't
commit them to a public repository: `data/charts/` is git-ignored for that reason (or set `POKER_CHARTS`
to a folder outside the repository).

## File format

```json
{
  "name": "My 9-handed 40 BB charts",
  "source": "Exported from my solver runs, 2.5x opens, no rake",
  "spots": [
    {"situation": "rfi", "positions": ["UTG", "UTG+1"], "stack_bb": [30, 60], "players": [8, 9],
     "actions": {"raise": "77+, ATs+, KQs, AJo+:0.5"}},
    {"situation": "vs_open", "position": "BB", "opener": ["BTN", "CO"], "stack_bb": [30, 60],
     "actions": {"3bet": "QQ+, AKs, A5s:50%", "call": "22-JJ, A2s-A4s, A6s-AQs, KTs+, QTs+, JTs"}}
  ]
}
```

A longer, obviously made-up example is in [`example-chart.json`](example-chart.json). Its ranges are
placeholders that only show the format; they are not GTO ranges.

### Top level

| Field | Required | Meaning |
|---|---|---|
| `name` | no (default: the file name without `.json`) | Shown in the dashboard and stored with each comparison. Must be unique across your chart files; `nash` is reserved. |
| `source` | no | Free text: where the ranges came from and what they assume (stack depth, open size, rake, antes). |
| `spots` | yes | Non-empty list of spots, below. |

### Spot

| Field | Required | Meaning |
|---|---|---|
| `situation` | yes | `"rfi"` (everyone before you folded) or `"vs_open"` (one player opened, everyone else folded to you). |
| `position` or `positions` | no (default: any position) | The decision maker's position: one name, or a list. Give one of the two keys, not both. `rfi` can't include `BB`. |
| `opener` | no, `vs_open` only (default: any opener) | Position of the player who opened: one name or a list. Can't include `BB`. |
| `stack_bb` | no (default: any stack) | `[low, high]`, inclusive, on the effective stack in big blinds (see below). Use a big number such as `1000` for "and deeper". |
| `players` | no (default: any table size) | `[low, high]`, inclusive, whole numbers: players dealt into the hand. |
| `actions` | yes | Action → range string (below). `rfi`: `raise`, `limp`, `allin`. `vs_open`: `3bet`, `call`, `allin`. Leave out `fold`: it is whatever weight the other actions leave. |

Position names are the analyzer's, case does not matter: `UTG`, `UTG+1`, `UTG+2`, `UTG+3`, `LJ`, `HJ`,
`CO`, `BTN`, `SB`, `BB`. Non-blind seats are named from the button backwards, so they depend on how many
players were dealt in:

| Players | Positions |
|---|---|
| 2 | BTN (posts the small blind), BB |
| 3 | BTN, SB, BB |
| 4 | CO, BTN, SB, BB |
| 5 | HJ, CO, BTN, SB, BB |
| 6 | UTG, HJ, CO, BTN, SB, BB |
| 7 | UTG, LJ, HJ, CO, BTN, SB, BB |
| 8 | UTG, UTG+1, LJ, HJ, CO, BTN, SB, BB |
| 9 | UTG, UTG+1, UTG+2, LJ, HJ, CO, BTN, SB, BB |
| 10 | UTG, UTG+1, UTG+2, UTG+3, LJ, HJ, CO, BTN, SB, BB |

With a dead small blind (common in tournaments) nobody is SB that hand, and the other seats are named as
at a table with one more player. If your tool calls 6-max's first seat "LJ", write `UTG` here.

### Ranges and weights

Ranges use the usual notation, separated by commas (or semicolons):

| Token | Means |
|---|---|
| `AKs`, `AKo`, `AK`, `77` | One class; `AK` is both `AKs` and `AKo` |
| `77+`, `ATs+`, `KTo+` | That class and better kickers up to the top card: `77`-`AA`; `ATs`, `AJs`, `AQs`, `AKs` |
| `22-55`, `A2s-A5s`, `K9o-KJo` | Every class from one end to the other (same top card and suit marker) |
| `AJo:0.5`, `AJo:50`, `AJo:50%` | A weight: the share of the time the chart takes this action with the class |

A token without a weight counts fully (1). A weight is 0 to 1; a number above 1 (up to 100), or one
written with `%`, is a percentage. Classes not named in an action's range have weight 0 for that action.
If the same class appears twice in one range, the later token wins, so `"77+, 99:0.5"` plays every pair
from 77 up fully except 99 half the time.

Per class, the weights of all actions in a spot must add up to at most 1; fold gets the rest. For
example `{"raise": "AJo:0.6", "limp": "AJo:0.2"}` means raise 60%, limp 20%, fold 20% with AJo.

Not supported: single combos (`AhKh`), the bracket syntax some tools use (`[50]AKs[/50]`), and spaces
as separators. When a solver mixes per combo (raises `AhKh` but not `AsKd`), use the class's average.

### Validation

Every file is checked when it is loaded. A file is skipped, with a message naming the file and the
problem, when it has:

- invalid JSON (with line and column), a duplicate key, or a top level that isn't an object;
- an unknown key at the top level or in a spot (a typo such as `postion` is caught this way);
- a missing or empty `spots` list, or a `name` that is empty, `nash`, or already used by another file;
- an unknown `situation`, position or opener name, both `position` and `positions`, `opener` on an
  `rfi` spot, or `BB` as an `rfi` position or an opener;
- an action that the situation doesn't allow (or `fold`), or a range that isn't a string;
- a bad range token or weight (outside 0-100, not a number);
- weights adding up to more than 1 for a class (the message lists the classes);
- a `stack_bb` or `players` that isn't `[low, high]` with 0 ≤ low ≤ high (whole numbers for `players`).

Messages look like `my-charts.json: spots[1] (vs_open): unknown vs_open action 'raise' (allowed: 3bet,
call, allin)`.

## Which spot is used

Only the player's **first preflop decision** is compared.

- **rfi**: nobody before the player raised or limped, and the player isn't the BB (the SB counts: its
  options are fold, complete or raise).
- **vs_open**: exactly one raise before the player, made first in (nobody limped before it), not
  all-in, and nobody called it before the player. The opener is the raiser's position. An all-in open is
  a push/fold spot instead: at 15 BB or less it is compared with the computed Nash call range.

A spot fits when its `situation` matches and every filter it has passes:

- `position(s)`: the player's position;
- `opener`: the opener's position;
- `stack_bb`: the effective stack at the decision, in BB: the smaller of the player's stack and the
  biggest stack among the others still in the hand, both as at the start of the hand (blinds included);
- `players`: players dealt into the hand.

**The first spot that fits wins**: files in file-name order, spots in the order they appear in the
file. Put narrow spots (one position, a short stack range) before broad ones. Raise sizes are not
checked: any raise that isn't all-in counts as a `raise` / `3bet`, whatever the chart's open size.

## Action mapping

| What the player did | `rfi` | `vs_open` |
|---|---|---|
| Fold | `fold` | `fold` |
| Call (limp, complete the SB, flat the open) | `limp` | `call` |
| Raise, all-in, and the spot has an `allin` action | `allin` | `allin` |
| Any other raise (including all-in when the spot has no `allin`) | `raise` | `3bet` |

The chart frequency of that action decides the verdict. An action the spot doesn't list has frequency 0,
so limping in an `rfi` spot without `limp` is a deviation. The chart's own most frequent action (fold
included) is shown next to it.

## In the dashboard

- **Leaks page**, "GTO chart comparison": spots compared, the share that agree with the chart, and the
  chip EV lost against the Nash push/fold charts, then a table of compared decisions (chart name,
  spot, hand, action, chart frequencies, verdict). Selecting a row draws that spot's 13x13 chart with
  your hand outlined, plus the hand's replay. Decisions where one of your charts says you deviated are
  also listed among the leak flags as a chart deviation.
- **GTO Charts page**: the Nash push/fold explorer, and "Your charts": each loaded chart with a grid per
  spot and action, the files that were skipped and why, and how to add charts.
- **Opponents page**: the same comparisons for each hand the selected opponent showed.

## Where ranges can come from

- **A solver or training tool you have a licence for** (PioSolver, GTO+, GTO Wizard and the like): open
  the spot, copy or export the range for each action, and paste it into `actions`. PioSolver-style
  strings (`AA:1,AKs:0.5,...`) paste as they are. Aggregate combo-level output to hand classes first.
- **Range strings you already have**, from a course, a book or your own notes, in the notation above.

Match the chart to the games you play: table size (`players`), stack depth (`stack_bb`), and the open
size, rake and antes the ranges assume (note those in `source`). The comparison is only as good as the
chart; nothing checks that the ranges really are GTO.
