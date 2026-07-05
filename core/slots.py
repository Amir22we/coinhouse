"""Серверная логика слот-аппарата.

Три барабана с цифрами. Три одинаковых — выигрыш ×2 (возврат ставки + прибыль 1:1).
RNG только на сервере.
"""
import random

from .blackjack import MIN_BET_TOTAL

SYMBOLS = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"]


def spin():
    reels = [random.choice(SYMBOLS) for _ in range(3)]
    return {"reels": reels, "won": reels[0] == reels[1] == reels[2]}
