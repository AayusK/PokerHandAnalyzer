from pathlib import Path

import pytest

from pokeranalyzer.parser import parse_file, parse_hand

SAMPLE = Path(__file__).parent / "fixtures" / "sample_mtt.json"


@pytest.fixture(scope="session")
def session():
    return parse_file(SAMPLE)


@pytest.fixture(scope="session")
def hand(session):
    """hand(number, table=None) -> the sample hand with that number (and table, when numbers repeat)."""
    def get(number, table=None):
        found = [h for h in session.hands if h.number == str(number) and (table is None or h.table == table)]
        assert len(found) == 1, f"hand {number} table {table}: {len(found)} matches"
        return found[0]
    return get


# ---- Synthetic hands -------------------------------------------------------------------

def sb(seat, v, allin=False):
    return {"type": 3, "seat": seat, "value": v, **({"allIn": True} if allin else {})}


def bb(seat, v, allin=False):
    return {"type": 2, "seat": seat, "value": v, **({"allIn": True} if allin else {})}


def call(seat, v, allin=False):
    return {"type": 7, "seat": seat, "value": v, **({"allIn": True} if allin else {})}


def bet(seat, v, allin=False):
    return {"type": 8, "seat": seat, "value": v, **({"allIn": True} if allin else {})}


def check(seat):
    return {"type": 0, "seat": seat}


def fold(seat):
    return {"type": 11, "seat": seat}


def board(turn, cards, run=1):
    return {"type": 9, "turn": turn, "run": run, "cards": cards}


def ret(seat, v):
    return {"type": 16, "seat": seat, "value": v}


def win(seat, v, pot=None, position=1, run="1"):
    return {"type": 10, "seat": seat, "value": v, "pot": pot or v, "position": position, "runNumber": run}


def show(seat, cards):
    return {"type": 12, "seat": seat, "cards": cards}


END = {"type": 15}


def raw_hand(players, dealer, events, small=10, big=20, game="th", hid="h1", number="1", started=0):
    """players: [(seat, stack, cards or None)]; ids are p<seat>, names P<seat>."""
    return {
        "id": hid, "handVersion": 2, "number": number, "gameType": game, "smallBlind": small, "bigBlind": big,
        "ante": None, "dealerSeat": dealer, "startedAt": started, "bombPot": False, "doubleBoard": None,
        "straddleSeat": None,
        "players": [{"id": f"p{seat}", "seat": seat, "name": f"P{seat}", "stack": stack,
                     **({"hand": cards} if cards else {})} for seat, stack, cards in players],
        "events": [{"at": started + i, "payload": e} for i, e in enumerate(events)],
    }


def make_hand(players, dealer, events, hero="p1", **kw):
    return parse_hand(raw_hand(players, dealer, events, **kw), hero_id=hero, tournament="test")
