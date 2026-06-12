"""Серверная логика классического блекджека.

Состояние игры хранится в сессии пользователя — клиент не может его подделать.
Дилер добирает до 17 (стоит на soft 17). Выплата 1:1, ничья — возврат ставки.
"""
import random

SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]

MIN_BET_TOTAL = 3


def new_deck():
    deck = [{"r": r, "s": s} for r in RANKS for s in SUITS]
    random.shuffle(deck)
    return deck


def hand_value(hand):
    total, aces = 0, 0
    for card in hand:
        r = card["r"]
        if r == "A":
            total += 11
            aces += 1
        elif r in ("J", "Q", "K"):
            total += 10
        else:
            total += int(r)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def start_game(bet_items):
    """bet_items: dict {code: amount}, уже списанные с баланса.

    Руки пустые: карты не раздаются заранее, игрок берёт их по одной,
    после каждой его карты дилер берёт свою (пока у него меньше 17).
    """
    return {
        "deck": new_deck(),
        "player": [],
        "dealer": [],
        "bet": bet_items,
        "finished": False,
        "result": None,  # win / lose / push
    }


def hit(game):
    """Игрок берёт карту, затем ход дилера."""
    if game["finished"]:
        return game
    game["player"].append(game["deck"].pop())
    if hand_value(game["player"]) > 21:
        game["finished"] = True
        game["result"] = "lose"
        return game
    if hand_value(game["dealer"]) < 17:
        game["dealer"].append(game["deck"].pop())
        if hand_value(game["dealer"]) > 21:
            game["finished"] = True
            game["result"] = "win"
            return game
    if hand_value(game["player"]) == 21:
        return stand(game)
    return game


def stand(game):
    # «Хватит» доступно только когда у игрока минимум две карты
    if game["finished"] or len(game["player"]) < 2:
        return game
    while hand_value(game["dealer"]) < 17:
        game["dealer"].append(game["deck"].pop())
    p, d = hand_value(game["player"]), hand_value(game["dealer"])
    if d > 21 or p > d:
        game["result"] = "win"
    elif p < d:
        game["result"] = "lose"
    else:
        game["result"] = "push"
    game["finished"] = True
    return game
