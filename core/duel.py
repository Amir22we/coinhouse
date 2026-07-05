"""Правила дуэли: блекджек один-на-один, без дилера.

Чистые функции над словарём матча — без Redis и без БД (этим занимается
consumer). Матч сериализуется в JSON и хранится в Redis, поэтому любой воркер
Daphne может обслужить любого игрока.

Каждому игроку — своя колода (исключает влияние порядка карт между игроками).
Каждый добирает карты независимо (hit/stand). Когда оба закончили — сравнение:
большая валидная рука (<= 21) забирает банк (обе ставки). Перебор = проигрыш.
Оба перебор или равные руки = ничья (возврат ставок).
"""
from . import blackjack as bj

MIN_BET_TOTAL = bj.MIN_BET_TOTAL


def bet_key(items):
    """Канонический ключ состава ставки — матчим только одинаковые ставки.

    Так банк всегда симметричен (2× ставка) и победитель забирает ровно вдвое.
    """
    return ",".join(f"{k}:{v}" for k, v in sorted(items.items()) if v)


def new_match(mid, bet, p1, p2):
    """p1/p2: dict {uid(str), username, channel}. Раздаём по 2 карты каждому."""
    players = {}
    for p in (p1, p2):
        deck = bj.new_deck()
        players[p["uid"]] = {
            "username": p["username"],
            "channel": p["channel"],
            "hand": [deck.pop(), deck.pop()],
            "deck": deck,
            "stood": False,
            "busted": False,
        }
    return {
        "id": mid,
        "bet": bet,
        "betkey": bet_key(bet),
        "order": [p1["uid"], p2["uid"]],
        "players": players,
        "finished": False,
        "winner": None,  # uid победителя | "push" | uid (форфейт)
        "forfeit": False,
    }


def _done(p):
    return p["stood"] or p["busted"]


def value(p):
    return bj.hand_value(p["hand"])


def opponent_uid(match, uid):
    a, b = match["order"]
    return b if uid == a else a


def apply_hit(match, uid):
    if match["finished"]:
        return
    p = match["players"][uid]
    if _done(p):
        return
    p["hand"].append(p["deck"].pop())
    if value(p) > 21:
        p["busted"] = True
    _maybe_finish(match)


def apply_stand(match, uid):
    if match["finished"]:
        return
    p = match["players"][uid]
    if _done(p):
        return
    p["stood"] = True
    _maybe_finish(match)


def _maybe_finish(match):
    if all(_done(p) for p in match["players"].values()):
        match["finished"] = True
        match["winner"] = _resolve(match)


def _resolve(match):
    a, b = match["order"]
    pa, pb = match["players"][a], match["players"][b]
    va = None if pa["busted"] else value(pa)
    vb = None if pb["busted"] else value(pb)
    if va is None and vb is None:
        return "push"
    if va is None:
        return b
    if vb is None:
        return a
    if va > vb:
        return a
    if vb > va:
        return b
    return "push"


def forfeit(match, quitter_uid):
    """Игрок отвалился в активном матче — победа сопернику."""
    if match["finished"]:
        return
    match["finished"] = True
    match["forfeit"] = True
    match["winner"] = opponent_uid(match, quitter_uid)


def build_state(match, viewer_uid):
    """Состояние для конкретного игрока. Рука соперника скрыта до конца матча."""
    me = match["players"][viewer_uid]
    opp_uid = opponent_uid(match, viewer_uid)
    opp = match["players"][opp_uid]
    finished = match["finished"]
    return {
        "match_id": match["id"],
        "bet": match["bet"],
        "finished": finished,
        "me": {
            "hand": me["hand"],
            "value": value(me),
            "stood": me["stood"],
            "busted": me["busted"],
            "can_act": not finished and not _done(me),
        },
        "opponent": {
            "username": opp["username"],
            "cards": len(opp["hand"]),
            "done": _done(opp),
            # руку и счёт соперника раскрываем только в конце
            "hand": opp["hand"] if finished else None,
            "value": value(opp) if finished else None,
            "busted": opp["busted"] if finished else None,
        },
    }
