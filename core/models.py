from django.conf import settings
from django.db import models
from django.db.models import F


COIN_CODES = ["X", "S", "A", "P", "G", "B", "C", "D", "E", "H", "K", "N", "Q", "L"]


class CoinType(models.Model):
    code = models.CharField(max_length=2, unique=True)
    name = models.CharField(max_length=40, blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return self.code


class Balance(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="balances")
    coin = models.ForeignKey(CoinType, on_delete=models.CASCADE)
    amount = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("user", "coin")
        ordering = ["coin__order"]

    def __str__(self):
        return f"{self.user} — {self.amount} {self.coin.code}"


def get_balances(user):
    """Гарантирует наличие строки баланса для каждого типа коина."""
    coins = list(CoinType.objects.all())
    existing = {b.coin_id: b for b in user.balances.select_related("coin")}
    missing = [Balance(user=user, coin=c) for c in coins if c.id not in existing]
    if missing:
        Balance.objects.bulk_create(missing, ignore_conflicts=True)
        existing = {b.coin_id: b for b in user.balances.select_related("coin")}
    return [existing[c.id] for c in coins if c.id in existing]


def credit(user, items, reason):
    """items: dict {code: amount}. Начисляет коины и пишет транзакции."""
    for code, amount in items.items():
        if amount <= 0:
            continue
        coin = CoinType.objects.get(code=code)
        bal, _ = Balance.objects.get_or_create(user=user, coin=coin)
        Balance.objects.filter(pk=bal.pk).update(amount=F("amount") + amount)
        Transaction.objects.create(user=user, coin=coin, delta=amount, reason=reason)


def debit(user, items, reason):
    """items: dict {code: amount}. Списывает коины. Возвращает False, если не хватает."""
    coins = {c.code: c for c in CoinType.objects.filter(code__in=items.keys())}
    balances = {b.coin.code: b for b in user.balances.select_related("coin").filter(coin__code__in=items.keys())}
    for code, amount in items.items():
        if amount <= 0:
            continue
        bal = balances.get(code)
        if bal is None or bal.amount < amount:
            return False
    for code, amount in items.items():
        if amount <= 0:
            continue
        Balance.objects.filter(pk=balances[code].pk).update(amount=F("amount") - amount)
        Transaction.objects.create(user=user, coin=coins[code], delta=-amount, reason=reason)
    return True


class Transaction(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="transactions")
    coin = models.ForeignKey(CoinType, on_delete=models.CASCADE)
    delta = models.IntegerField()
    reason = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class CoinRequest(models.Model):
    """Заявка на пополнение или вывод."""
    DEPOSIT = "deposit"
    WITHDRAW = "withdraw"
    KIND_CHOICES = [(DEPOSIT, "Пополнение"), (WITHDRAW, "Вывод")]

    PENDING = "pending"            # создана пользователем, ждёт админа
    AWAITING = "awaiting"          # админ отправил инструкции, ждёт оплаты игроком
    COMPLETED = "completed"        # подтверждена, коины начислены/выданы
    REJECTED = "rejected"
    STATUS_CHOICES = [
        (PENDING, "Ожидает администрацию"),
        (AWAITING, "Ожидает оплату"),
        (COMPLETED, "Завершена"),
        (REJECTED, "Отклонена"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="coin_requests")
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    items = models.JSONField(default=dict)  # {"S": 5, "B": 10}
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    user_comment = models.TextField(blank=True)
    admin_comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="processed_requests",
    )

    class Meta:
        ordering = ["-created_at"]

    def items_display(self):
        return ", ".join(f"{v} {k}" for k, v in self.items.items() if v)

    def total(self):
        return sum(self.items.values())


class Message(models.Model):
    """Сообщение пользователю от администрации (видно на сайте)."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="site_messages")
    text = models.TextField()
    related_request = models.ForeignKey(CoinRequest, null=True, blank=True, on_delete=models.SET_NULL)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
