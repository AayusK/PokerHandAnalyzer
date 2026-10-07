"""Build pokeranalyzer/data/preflop_equity.json: all-in preflop equity of every Hold'em hand class
against every other, with card removal.

E[i][j] is the share of the pot class i wins (ties split) against class j, averaged over the
combos of j that don't share a card with i. Every combo of a class is the same up to a
relabelling of suits, so one representative combo per class is enough, and E[j][i] = 1 - E[i][j].

Runs eval7's Monte Carlo (default 300k boards per pair, standard error ~0.001); takes a few
minutes. Usage: python scripts/build_equity_matrix.py [iterations]
"""

import json
import sys
import time
from pathlib import Path

import eval7

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pokeranalyzer.ranges import CLASSES  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "pokeranalyzer" / "data" / "preflop_equity.json"


def representative(cls: str) -> list:
    if len(cls) == 2:
        return [eval7.Card(cls[0] + "s"), eval7.Card(cls[1] + "h")]
    if cls[2] == "s":
        return [eval7.Card(cls[0] + "s"), eval7.Card(cls[1] + "s")]
    return [eval7.Card(cls[0] + "s"), eval7.Card(cls[1] + "h")]


def main(iterations: int) -> None:
    n = len(CLASSES)
    eq = [[0.5] * n for _ in range(n)]
    ranges = {c: eval7.HandRange(c) for c in CLASSES}
    start = time.time()
    for i, a in enumerate(CLASSES):
        hand = representative(a)
        for j in range(i + 1, n):
            e = eval7.py_hand_vs_range_monte_carlo(hand, ranges[CLASSES[j]], [], iterations)
            eq[i][j] = round(e, 4)
            eq[j][i] = round(1 - e, 4)
        print(f"{i + 1}/{n} {a} ({time.time() - start:.0f}s)", flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "description": "Preflop all-in equity of row class vs column class (ties split), card removal included",
        "classes": CLASSES,
        "iterations_per_pair": iterations,
        "equity": eq,
    }, separators=(",", ":")))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 300_000)
