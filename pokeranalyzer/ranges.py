"""Hold'em starting-hand classes, a strength ranking, and range notation ("77+, ATs+, KQo")."""

from __future__ import annotations

import math
import re

RANKS = "23456789TJQKA"

# The 169 starting-hand classes, strongest first, ranked by all-in equity against a random
# hand (eval7 Monte Carlo, 400k trials per class). Good enough for "top X%" push/fold ranges.
RANKING = (
    "AA KK QQ JJ TT 99 88 AKs 77 AQs AJs AKo ATs AQo AJo KQs 66 ATo A9s KJs A8s KTs KQo A7s "
    "A9o KJo 55 QJs A6s A5s K9s A8o KTo QTs A7o A4s A3s K8s QJo K9o A6o Q9s A5o JTs K7s A2s "
    "QTo 44 A4o K6s Q8s K8o A3o K5s J9s Q9o JTo K7o A2o K4s Q7s K6o T9s K3s J8s 33 Q8o Q6s "
    "K5o J9o K2s Q5s K4o T8s J7s Q4s Q7o T9o J8o K3o Q6o Q3s 98s J6s T7s K2o 22 Q2s Q5o J5s "
    "J7o T8o 97s Q4o J4s T6s Q3o J3s 98o J6o T7o 87s 96s J2s Q2o J5o T5s T4s 97o 86s J4o T6o "
    "95s T3s 76s J3o 87o T2s 96o 85s J2o T5o 75s 94s T4o 65s 86o 93s 95o T3o 84s 92s 76o 74s "
    "T2o 85o 54s 64s 83s 94o 75o 82s 65o 93o 73s 53s 63s 84o 92o 43s 74o 72s 54o 64o 52s 62s "
    "83o 82o 42s 73o 53o 63o 32s 43o 72o 52o 62o 42o 32o"
).split()


# The 13x13 grid every chart is drawn on: rows and columns run A..2, pairs on the diagonal,
# suited hands above it (row rank higher) and offsuit hands below it.
GRID_RANKS = "AKQJT98765432"


def grid_class(row: int, col: int) -> str:
    hi, lo = GRID_RANKS[min(row, col)], GRID_RANKS[max(row, col)]
    if row == col:
        return hi + lo
    return hi + lo + ("s" if row < col else "o")


CLASSES = [grid_class(r, c) for r in range(13) for c in range(13)]  # row-major grid order
CLASS_INDEX = {cls: i for i, cls in enumerate(CLASSES)}


def combos(cls: str) -> int:
    return 6 if len(cls) == 2 else (4 if cls[2] == "s" else 12)


def _percentiles() -> dict[str, float]:
    out, total = {}, 0
    for cls in RANKING:
        total += combos(cls)
        out[cls] = 100.0 * total / 1326
    return out


# Share of all 1326 combos at least as strong as the class (AA = 0.45, 32o = 100).
PERCENTILE = _percentiles()


def hand_class(cards: list[str]) -> str:
    """['Ah', 'Kh'] -> 'AKs'; ['7d', '7c'] -> '77'."""
    if len(cards) != 2:
        raise ValueError(f"not a Hold'em hand: {cards}")
    (r1, s1), (r2, s2) = (cards[0][0].upper(), cards[0][1]), (cards[1][0].upper(), cards[1][1])
    if RANKS.index(r1) < RANKS.index(r2):
        r1, r2 = r2, r1
    if r1 == r2:
        return r1 + r2
    return r1 + r2 + ("s" if s1 == s2 else "o")


def percentile(cards_or_class: list[str] | str) -> float:
    cls = cards_or_class if isinstance(cards_or_class, str) else hand_class(cards_or_class)
    return PERCENTILE[cls]


def top_percent(pct: float) -> set[str]:
    return {cls for cls, p in PERCENTILE.items() if p <= pct + 1e-9}


def _expand(token: str) -> set[str]:
    token = token.strip()
    if not token:
        return set()
    if "-" in token:  # "A2s-A5s", "77-99"
        lo, hi = (t.strip() for t in token.split("-"))
        if len(lo) == 2 and lo[0] == lo[1]:
            a, b = sorted((RANKS.index(lo[0]), RANKS.index(hi[0])))
            return {RANKS[i] * 2 for i in range(a, b + 1)}
        top, suffix = lo[0], lo[2:] if len(lo) > 2 else ""
        a, b = sorted((RANKS.index(lo[1]), RANKS.index(hi[1])))
        return _with_suffix({top + RANKS[i] for i in range(a, b + 1)}, suffix)
    plus = token.endswith("+")
    body = token.rstrip("+")
    if len(body) == 2 and body[0] == body[1]:  # pairs
        start = RANKS.index(body[0])
        return {RANKS[i] * 2 for i in range(start, 13)} if plus else {body}
    hi, lo, suffix = body[0], body[1], body[2:]
    if RANKS.index(hi) < RANKS.index(lo):
        hi, lo = lo, hi
    if plus:  # "ATs+" = ATs, AJs, AQs, AKs
        return _with_suffix({hi + RANKS[i] for i in range(RANKS.index(lo), RANKS.index(hi))}, suffix)
    return _with_suffix({hi + lo}, suffix)


def _with_suffix(pairs: set[str], suffix: str) -> set[str]:
    if suffix in ("s", "o"):
        return {p + suffix for p in pairs}
    return {p + s for p in pairs for s in ("s", "o")}


def parse_range(text: str) -> set[str]:
    """Parse standard range notation into hand classes, e.g. "77+, ATs+, KQo, A2s-A5s"."""
    out: set[str] = set()
    for token in text.replace(";", ",").split(","):
        out |= _expand(token.upper().replace("S", "s").replace("O", "o"))
    unknown = out - set(PERCENTILE)
    if unknown:
        raise ValueError(f"bad range tokens: {sorted(unknown)}")
    return out


def range_percent(classes: set[str]) -> float:
    return 100.0 * sum(combos(c) for c in classes) / 1326


# One hand class, optionally with "+" ("77+", "ATs+"), or a dash range between two classes of
# the same shape ("22-55", "A2s-A5s"). Ranks are upper case, suits lower case here.
_RANK = "[AKQJT2-9]"
_SINGLE = re.compile(rf"({_RANK})({_RANK})([so]?)\+?")
_DASH = re.compile(rf"({_RANK})({_RANK})([so]?)-({_RANK})({_RANK})([so]?)")
_COMBO = re.compile(r"[2-9TJQKA][shdc][2-9TJQKA][shdc]", re.IGNORECASE)


def _weighted_token(body: str, token: str) -> set[str]:
    """Classes for one token of a weighted range; stricter than _expand about odd notation."""
    if _COMBO.fullmatch(body):
        raise ValueError(f"bad range token {token!r}: single combos are not supported, use hand classes (e.g. AKs)")
    text = body.upper().replace("S", "s").replace("O", "o")
    single, dash = _SINGLE.fullmatch(text), _DASH.fullmatch(text)
    if single:
        hi, lo, suffix = single.groups()
        ok = (hi == lo and not suffix) or hi != lo
    elif dash:
        h1, l1, s1, h2, l2, s2 = dash.groups()
        pairs = h1 == l1 and h2 == l2 and not s1 and not s2
        # Non-pair ranges keep the top card and suffix and move the kicker: "A2s-A5s", "K9o-KJo".
        kickers = h1 == h2 and s1 == s2 and RANKS.index(l1) < RANKS.index(h1) and RANKS.index(l2) < RANKS.index(h2)
        ok = pairs or kickers
    else:
        ok = False
    if not ok:
        raise ValueError(f"bad range token {token!r}")
    return _expand(text)


def _weight(text: str, token: str) -> float:
    """Weight after the colon: 0..1, or a percentage when above 1 (up to 100) or written with "%"."""
    text = text.strip()
    percent = text.endswith("%")
    try:
        value = float(text[:-1] if percent else text)
    except ValueError:
        raise ValueError(f"bad weight in {token!r}") from None
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError(f"weight out of range in {token!r}: use 0-1 or a percentage up to 100")
    return value / 100 if percent or value > 1 else value


def parse_weighted_range(text: str) -> dict[str, float]:
    """Parse a range whose tokens may carry a weight, PioSolver style: "AKs:0.5, 77+:0.25, KQo".

    A token without a weight counts fully; a weight above 1 (up to 100), or one written with
    "%", is a percentage. Later tokens override earlier ones for the same class. Returns
    {class: weight} for the classes named; bad tokens raise ValueError.
    """
    out: dict[str, float] = {}
    for token in text.replace(";", ",").split(","):
        token = token.strip()
        if not token:
            continue
        body, colon, weight = token.partition(":")
        value = _weight(weight, token) if colon else 1.0
        for cls in _weighted_token(body.strip(), token):
            out[cls] = value
    return out
