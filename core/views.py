from django.contrib import messages as flash
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.db import transaction as db_transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import blackjack as bj
from .models import (
    Balance, CoinRequest, CoinType, Message, Transaction,
    credit, debit, get_balances,
)

MIN_DEPOSIT_PER_RANK = 5


def _parse_items(post, prefix="coin_"):
    """Собирает {code: amount} из формы с полями coin_<CODE>."""
    items = {}
    for coin in CoinType.objects.all():
        raw = post.get(f"{prefix}{coin.code}", "").strip()
        if not raw:
            continue
        try:
            val = int(raw)
        except ValueError:
            continue
        if val > 0:
            items[coin.code] = val
    return items


# ---------- Публичные страницы ----------

def home(request):
    return render(request, "home.html", {"coins": CoinType.objects.all()})


def info(request):
    return render(request, "info.html", {
        "coins": CoinType.objects.all(),
        "min_deposit": MIN_DEPOSIT_PER_RANK,
        "min_bet": bj.MIN_BET_TOTAL,
    })


def help_page(request):
    return render(request, "help.html")


def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = UserCreationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        get_balances(user)
        flash.success(request, "Аккаунт создан. Добро пожаловать!")
        return redirect("dashboard")
    return render(request, "registration/register.html", {"form": form})


# ---------- Кабинет пользователя ----------

@login_required
def dashboard(request):
    balances = get_balances(request.user)
    unread = request.user.site_messages.filter(is_read=False).count()
    recent_requests = request.user.coin_requests.all()[:5]
    return render(request, "dashboard.html", {
        "balances": balances,
        "total": sum(b.amount for b in balances),
        "unread": unread,
        "recent_requests": recent_requests,
    })


@login_required
def profile(request):
    balances = get_balances(request.user)
    txs = request.user.transactions.select_related("coin")[:30]
    return render(request, "profile.html", {
        "balances": balances,
        "total": sum(b.amount for b in balances),
        "transactions": txs,
    })


@login_required
def inbox(request):
    msgs = request.user.site_messages.select_related("related_request")
    request.user.site_messages.filter(is_read=False).update(is_read=True)
    return render(request, "inbox.html", {"msgs": msgs})


# ---------- Пополнение и вывод ----------

@login_required
def deposit(request):
    coins = CoinType.objects.all()
    if request.method == "POST":
        items = _parse_items(request.POST)
        if not items:
            flash.error(request, "Укажите хотя бы один тип коинов.")
        elif any(v < MIN_DEPOSIT_PER_RANK for v in items.values()):
            flash.error(request, f"Минимум {MIN_DEPOSIT_PER_RANK} коинов каждого выбранного ранга.")
        else:
            req = CoinRequest.objects.create(
                user=request.user, kind=CoinRequest.DEPOSIT,
                items=items, user_comment=request.POST.get("comment", "")[:500],
            )
            flash.success(request, f"Заявка №{req.id} на пополнение создана. Ожидайте инструкции от администрации.")
            return redirect("requests")
    return render(request, "deposit.html", {"coins": coins, "min_deposit": MIN_DEPOSIT_PER_RANK})


@login_required
def withdraw(request):
    balances = get_balances(request.user)
    if request.method == "POST":
        items = _parse_items(request.POST)
        if not items:
            flash.error(request, "Укажите хотя бы один тип коинов.")
        else:
            with db_transaction.atomic():
                ok = debit(request.user, items, "Заявка на вывод (заморозка)")
                if not ok:
                    flash.error(request, "Недостаточно коинов на балансе.")
                else:
                    req = CoinRequest.objects.create(
                        user=request.user, kind=CoinRequest.WITHDRAW,
                        items=items, user_comment=request.POST.get("comment", "")[:500],
                    )
                    flash.success(request, f"Заявка №{req.id} на вывод создана. Коины заморожены до подтверждения.")
                    return redirect("requests")
    return render(request, "withdraw.html", {"balances": balances})


@login_required
def my_requests(request):
    reqs = request.user.coin_requests.all()
    return render(request, "requests.html", {"reqs": reqs})


# ---------- Игры ----------

@login_required
def games(request):
    return render(request, "games.html")


@login_required
def blackjack_view(request):
    game = request.session.get("bj_game")
    balances = get_balances(request.user)
    ctx = {
        "balances": balances,
        "min_bet": bj.MIN_BET_TOTAL,
        "game": None,
    }
    if game:
        # Вторая карта дилера закрыта до конца раздачи
        dealer_cards = [
            dict(c, hidden=(not game["finished"] and i == 1))
            for i, c in enumerate(game["dealer"])
        ]
        # Счёт дилера: в процессе игры — только по открытым картам
        dealer_visible = [c for c in dealer_cards if not c["hidden"]]
        ctx["game"] = {
            "player": game["player"],
            "dealer": dealer_cards,
            "player_value": bj.hand_value(game["player"]),
            "dealer_value": bj.hand_value(game["dealer"]) if game["finished"] else (bj.hand_value(dealer_visible) if dealer_visible else None),
            "dealer_partial": not game["finished"] and len(dealer_cards) > len(dealer_visible),
            "finished": game["finished"],
            "result": game["result"],
            "can_stand": len(game["player"]) >= 2,
            "bet": game["bet"],
            "bet_display": ", ".join(f"{v} {k}" for k, v in game["bet"].items()),
        }
    return render(request, "blackjack.html", ctx)


@login_required
@require_POST
def blackjack_bet(request):
    if request.session.get("bj_game") and not request.session["bj_game"]["finished"]:
        flash.error(request, "Сначала завершите текущую раздачу.")
        return redirect("blackjack")
    items = _parse_items(request.POST, prefix="bet_")
    total = sum(items.values())
    if total < bj.MIN_BET_TOTAL:
        flash.error(request, f"Минимальная ставка — {bj.MIN_BET_TOTAL} коина (можно смешивать ранги).")
        return redirect("blackjack")
    with db_transaction.atomic():
        if not debit(request.user, items, "Ставка в блекджек"):
            flash.error(request, "Недостаточно коинов для такой ставки.")
            return redirect("blackjack")
        request.session["bj_game"] = bj.start_game(items)
    return redirect("blackjack")


@login_required
@require_POST
def blackjack_action(request, action):
    game = request.session.get("bj_game")
    if not game:
        return redirect("blackjack")
    if game["finished"]:
        if action == "new":
            del request.session["bj_game"]
            request.session.modified = True
        return redirect("blackjack")
    if action == "hit":
        game = bj.hit(game)
    elif action == "stand":
        game = bj.stand(game)
    if game["finished"]:
        _settle(request, game)
    request.session["bj_game"] = game
    return redirect("blackjack")


def _settle(request, game):
    bet = game["bet"]
    if game["result"] == "win":
        winnings = {k: v * 2 for k, v in bet.items()}  # возврат ставки + выигрыш 1:1
        credit(request.user, winnings, "Выигрыш в блекджек")
        flash.success(request, "Победа! Выигрыш зачислен на баланс.")
    elif game["result"] == "push":
        credit(request.user, bet, "Ничья в блекджек (возврат ставки)")
        flash.info(request, "Ничья. Ставка возвращена.")
    else:
        flash.error(request, "Поражение. Ставка списана.")


# ---------- Админ-панель ----------

def staff_required(view):
    return user_passes_test(lambda u: u.is_staff, login_url="login")(view)


def _economy_stats():
    """Сводка экономики: коины у игроков и заработок биржи (блекджек)."""
    coins = list(CoinType.objects.all())
    on_hands = {
        r["coin__code"]: r["total"] or 0
        for r in Balance.objects.values("coin__code").annotate(total=Sum("amount"))
    }
    # Ставка списывается (−), выигрыш/возврат начисляется (+):
    # минус суммы дельт по блекджеку = чистый заработок биржи
    house = {
        r["coin__code"]: -(r["d"] or 0)
        for r in Transaction.objects.filter(reason__icontains="блекджек")
        .values("coin__code").annotate(d=Sum("delta"))
    }
    rows = [
        {"coin": c, "players": on_hands.get(c.code, 0), "house": house.get(c.code, 0)}
        for c in coins
    ]
    return {
        "rows": rows,
        "total_players": sum(r["players"] for r in rows),
        "total_house": sum(r["house"] for r in rows),
    }


@staff_required
def panel_home(request):
    eco = _economy_stats()
    return render(request, "panel/home.html", {
        "pending_deposits": CoinRequest.objects.filter(kind="deposit", status__in=["pending", "awaiting"]).count(),
        "pending_withdraws": CoinRequest.objects.filter(kind="withdraw", status="pending").count(),
        "users_count": User.objects.count(),
        "recent": CoinRequest.objects.select_related("user")[:8],
        "total_players": eco["total_players"],
        "total_house": eco["total_house"],
    })


@staff_required
def panel_economy(request):
    eco = _economy_stats()
    # Кто сколько проиграл: сумма дельт по блекджеку на пользователя,
    # отрицательная — игрок в минусе (проиграл бирже)
    nets = (
        Transaction.objects.filter(reason__icontains="блекджек")
        .values("user_id", "user__username")
        .annotate(net=Sum("delta"))
        .order_by("net")[:100]
    )
    players = [
        {"id": n["user_id"], "username": n["user__username"], "lost": -(n["net"] or 0)}
        for n in nets
    ]
    return render(request, "panel/economy.html", {
        "rows": eco["rows"],
        "total_players": eco["total_players"],
        "total_house": eco["total_house"],
        "players": players,
    })


@staff_required
def panel_requests(request):
    kind = request.GET.get("kind", "")
    status = request.GET.get("status", "")
    reqs = CoinRequest.objects.select_related("user", "processed_by")
    if kind:
        reqs = reqs.filter(kind=kind)
    if status:
        reqs = reqs.filter(status=status)
    return render(request, "panel/requests.html", {"reqs": reqs[:100], "kind": kind, "status": status})


@staff_required
def panel_request_detail(request, pk):
    req = get_object_or_404(CoinRequest.objects.select_related("user"), pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        note = request.POST.get("admin_comment", "")[:1000]
        with db_transaction.atomic():
            if action == "comment":
                # Комментарий пользователю без смены статуса заявки
                if note.strip():
                    Message.objects.create(user=req.user, related_request=req, text=note.strip())
                    flash.success(request, "Комментарий отправлен пользователю.")
                else:
                    flash.error(request, "Введите текст комментария.")
            elif action == "send_instructions" and req.kind == "deposit" and req.status == "pending":
                req.status = CoinRequest.AWAITING
                req.admin_comment = note
                req.processed_by = request.user
                req.save()
                Message.objects.create(
                    user=req.user, related_request=req,
                    text=note or f"По заявке №{req.id}: переведите коины по инструкции и ожидайте подтверждения.",
                )
                flash.success(request, "Инструкция отправлена пользователю.")
            elif action == "confirm" and req.status in ("pending", "awaiting"):
                req.status = CoinRequest.COMPLETED
                req.processed_by = request.user
                if note:
                    req.admin_comment = note
                req.save()
                if req.kind == "deposit":
                    credit(req.user, req.items, f"Пополнение по заявке №{req.id}")
                    Message.objects.create(user=req.user, related_request=req,
                                           text=f"Заявка №{req.id} подтверждена — коины зачислены: {req.items_display()}.")
                else:
                    # коины уже заморожены при создании заявки — просто фиксируем выдачу
                    Message.objects.create(user=req.user, related_request=req,
                                           text=f"Вывод по заявке №{req.id} выполнен: {req.items_display()}. Коины переданы в игре.")
                flash.success(request, "Заявка подтверждена.")
            elif action == "reject" and req.status in ("pending", "awaiting"):
                req.status = CoinRequest.REJECTED
                req.processed_by = request.user
                if note:
                    req.admin_comment = note
                req.save()
                if req.kind == "withdraw":
                    credit(req.user, req.items, f"Возврат заморозки (заявка №{req.id} отклонена)")
                Message.objects.create(user=req.user, related_request=req,
                                       text=f"Заявка №{req.id} отклонена." + (f" Причина: {note}" if note else ""))
                flash.info(request, "Заявка отклонена.")
        # Быстрые действия из списка возвращают обратно в список
        nxt = request.POST.get("next", "")
        if nxt.startswith("/panel/"):
            return redirect(nxt)
        return redirect("panel_request_detail", pk=req.pk)
    sent_messages = Message.objects.filter(related_request=req)
    return render(request, "panel/request_detail.html", {"req": req, "sent_messages": sent_messages})


@staff_required
def panel_users(request):
    q = request.GET.get("q", "").strip()
    users = User.objects.annotate(total_coins=Sum("balances__amount")).order_by("-date_joined")
    if q:
        users = users.filter(username__icontains=q)
    return render(request, "panel/users.html", {"users": users[:50], "q": q})


@staff_required
def panel_user_detail(request, pk):
    target = get_object_or_404(User, pk=pk)
    balances = get_balances(target)
    if request.method == "POST":
        action = request.POST.get("action")
        items = _parse_items(request.POST, prefix="adj_")
        note = request.POST.get("note", "")[:120] or "Корректировка администратором"
        if not items:
            flash.error(request, "Укажите количество хотя бы для одного ранга.")
        elif action == "credit":
            credit(target, items, note)
            flash.success(request, "Коины начислены.")
        elif action == "debit":
            if debit(target, items, note):
                flash.success(request, "Коины списаны.")
            else:
                flash.error(request, "У пользователя недостаточно коинов.")
        elif action == "message":
            text = request.POST.get("note", "").strip()
            if text:
                Message.objects.create(user=target, text=text)
                flash.success(request, "Сообщение отправлено.")
        return redirect("panel_user_detail", pk=pk)
    txs = target.transactions.select_related("coin")[:30]
    reqs = target.coin_requests.all()[:10]
    return render(request, "panel/user_detail.html", {
        "target": target, "balances": balances, "transactions": txs, "reqs": reqs,
    })
