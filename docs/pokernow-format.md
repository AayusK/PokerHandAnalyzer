# PokerNow JSON export format (handVersion 2)

Worked out from a sample tournament export and checked by replaying every hand's chips: each player's
starting stack + winnings − amount put in matches their stack at the start of their next hand at the same
table, with zero mismatches (`pokeranalyzer.parser.chip_replay`).

## Top level

`generatedAt`, `playerId` (the person who exported, i.e. you), `tournamentSlug`, `hands[]`

## Hand

`id`, `number` (string), `gameType` (`"th"` = Hold'em, `"omaha"` = PLO with 4 hole cards), `smallBlind`,
`bigBlind`, `ante`, `dealerSeat`, `startedAt` (ms), `bombPot`, `doubleBoard`, `straddleSeat`,
`sevenDeuceBounty`, `cents`, `players[]`, `events[]`, and sometimes `chatMessages[]`.

## Player

`id`, `seat`, `name`, `stack` (at the start of the hand), `hand` (hole cards; always present for you, and
for opponents only when shown).

## Events

`{ at: <ms timestamp>, payload: { type, ... } }`

| `type` | Meaning | Fields |
|---|---|---|
| 0 | Check | `seat` |
| 2 | Big blind post | `seat`, `value`, `allIn?` |
| 3 | Small blind post | `seat`, `value`, `allIn?` |
| 7 | Call | `seat`, `value` (total committed this street), `allIn?` |
| 8 | Bet / raise | `seat`, `value` (total committed this street), `allIn?` |
| 9 | Board dealt | `turn` (1 = flop, 2 = turn, 3 = river), `run`, `cards`, `handsLabels` |
| 10 | Pot awarded | `seat`, `value`, `pot`, `position`; at showdown also `cards`, `combination`, `handDescription`, `runNumber`, `hiLo` |
| 11 | Fold | `seat` |
| 12 | Cards shown | `seat`, `cards` |
| 15 | Hand end | none |
| 16 | Uncalled bet returned | `seat`, `value` |

`handsLabels[seat][].c` is the hand category for that seat on that street: 10 = high card, 9 = pair,
8 = two pair, 7 = trips, 6 = straight, 5 = flush, 4 = full house (lower = stronger). `d` lists the ranks
involved.

## Quirks

- **Hand numbers count per table.** After a table move they jump (27 → 31 in the sample) or even repeat
  (two different hands numbered 22). Order hands by `startedAt`; detect a table change from your seat
  changing or the number not following on.
- **Dead button.** `dealerSeat` can point at an empty seat. The player who acts last before the blinds
  still plays as the button.
- **Dead small blind.** Sometimes no small blind is posted at all; only the big blind (type 2) is certain.
- **Pot awards.** Several type 10 events in a row are separate pots (`position` 1 = main pot, 2+ = side
  pots). In a split pot each winner gets an event with their share in `value` and the whole pot in `pot`.
- **Uncalled bets** are returned with type 16 before the awards; an unopened pot returns the big blind's
  excess over the small blind.
- **One card at a time.** A player can show one card, then the other: type 12 carries `null` for the
  card not shown, and two events have to be merged.
- **Voluntary shows.** A type 12 after a pot won without a showdown is a voluntary show, not a showdown.
- The number of players changes as people bust, and the game can switch between Hold'em and PLO within
  one tournament.
- Opponents keep playing hands you don't see after you change tables, so their stacks only chain from
  hand to hand within a table.
