"""Статистика админ-панели: игры, игроки, лента операций."""
from django.db.models import Count, Max, Q, Sum

from .models import Balance, CoinType, Duel, Transaction

GAMES = (
    {"key": "blackjack", "label": "Блекджек", "keyword": "блекджек"},
    {"key": "slots", "label": "Слот", "keyword": "слот"},
    {"key": "duel", "label": "Дуэль", "keyword": "дуэл"},
)

HOUSE_GAMES = frozenset({"blackjack", "slots"})


def _game_q(keyword):
    return Q(reason__icontains=keyword)


def _all_games_q():
    q = Q()
    for g in GAMES:
        q |= _game_q(g["keyword"])
    return q


def game_key_from_reason(reason):
    r = (reason or "").lower()
    if "блекджек" in r:
        return "blackjack"
    if "слот" in r:
        return "slots"
    if "дуэл" in r:
        return "duel"
    return None


def _action_meta(reason, delta):
    r = (reason or "").lower()
    if delta > 0:
        if "возврат" in r or "ничья" in r:
            return "push", "Возврат"
        return "win", "Выигрыш"
    return "bet", "Ставка"


def economy_overview():
    coins = list(CoinType.objects.all())
    on_hands = {
        r["coin__code"]: r["total"] or 0
        for r in Balance.objects.values("coin__code").annotate(total=Sum("amount"))
    }

    games = []
    total_house = 0
    for g in GAMES:
        qs = Transaction.objects.filter(_game_q(g["keyword"]))
        house_by_coin = {
            r["coin__code"]: -(r["d"] or 0)
            for r in qs.values("coin__code").annotate(d=Sum("delta"))
        }
        house_total = sum(house_by_coin.values())
        if g["key"] in HOUSE_GAMES:
            total_house += house_total
        bets = qs.filter(delta__lt=0).aggregate(s=Sum("delta"))
        wins = qs.filter(delta__gt=0).aggregate(s=Sum("delta"))
        games.append({
            **g,
            "house": house_total,
            "house_by_coin": house_by_coin,
            "tx_count": qs.count(),
            "players_count": qs.values("user").distinct().count(),
            "bets_volume": -(bets["s"] or 0),
            "payouts_volume": wins["s"] or 0,
        })

    house_all = (
        Transaction.objects.filter(_game_q("блекджек") | _game_q("слот"))
        .values("coin__code")
        .annotate(d=Sum("delta"))
    )
    house_by_coin = {r["coin__code"]: -(r["d"] or 0) for r in house_all}
    rows = [
        {
            "coin": c,
            "players": on_hands.get(c.code, 0),
            "house": house_by_coin.get(c.code, 0),
        }
        for c in coins
    ]

    return {
        "rows": rows,
        "games": games,
        "total_players": sum(r["players"] for r in rows),
        "total_house": total_house,
        "duel_count": Duel.objects.count(),
    }


def player_game_stats(game_key=None, limit=60):
    qs = Transaction.objects.filter(_all_games_q())
    if game_key:
        kw = next(g["keyword"] for g in GAMES if g["key"] == game_key)
        qs = qs.filter(_game_q(kw))

    nets = (
        qs.values("user_id", "user__username")
        .annotate(
            net=Sum("delta"),
            bets=Sum("delta", filter=Q(delta__lt=0)),
            wins=Sum("delta", filter=Q(delta__gt=0)),
            last_at=Max("created_at"),
            tx_count=Count("id"),
        )
        .order_by("net")[:limit]
    )

    return [
        {
            "id": n["user_id"],
            "username": n["user__username"],
            "net": n["net"] or 0,
            "lost": -(n["net"] or 0),
            "bets": -(n["bets"] or 0),
            "wins": n["wins"] or 0,
            "last_at": n["last_at"],
            "tx_count": n["tx_count"],
        }
        for n in nets
    ]


def game_activity(game_key=None, limit=100):
    qs = Transaction.objects.filter(_all_games_q()).select_related("user", "coin")
    if game_key:
        kw = next(g["keyword"] for g in GAMES if g["key"] == game_key)
        qs = qs.filter(_game_q(kw))

    items = []
    for t in qs.order_by("-created_at")[:limit]:
        gkey = game_key_from_reason(t.reason)
        glabel = next((g["label"] for g in GAMES if g["key"] == gkey), "Игра")
        action, action_label = _action_meta(t.reason, t.delta)
        items.append({
            "id": t.id,
            "created_at": t.created_at,
            "user_id": t.user_id,
            "username": t.user.username,
            "game_key": gkey or "",
            "game_label": glabel,
            "action": action,
            "action_label": action_label,
            "delta": t.delta,
            "coin": t.coin.code,
            "reason": t.reason,
        })
    return items
