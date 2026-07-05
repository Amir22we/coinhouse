"""WebSocket-потребитель дуэли (блекджек 1-на-1).

Масштабирование:
- Матчмейкинг — атомарный RPOP из Redis-списка по ключу состава ставки: каждого
  ожидающего игрока может «забрать» только один соперник (без двойных матчей).
- Состояние матча и метки игроков живут в Redis, не в памяти процесса, поэтому
  игроков одного матча могут обслуживать разные воркеры Daphne.
- На время хода берётся короткий распределённый лок матча (SET NX PX), чтобы
  оба игрока не записали состояние одновременно. Расчёт банка идёт ровно один раз.

Деньги (коины) — escrow: ставка списывается при входе в очередь. Возврат при
отмене/дисконнекте в очереди; победителю — банк (2× ставка); ничья — возврат обоим.
"""
import json
import uuid

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth.models import User
from django.db import transaction as db_transaction

from . import duel
from .kvstore import get_store
from .models import Duel, credit, debit

# --- ключи Redis ---
def _q(betkey):   return f"duel:q:{betkey}"      # список ожидающих (по составу ставки)
def _m(mid):      return f"duel:m:{mid}"         # JSON матча
def _u(uid):      return f"duel:u:{uid}"         # "q" в очереди | mid в матче (1 игра на юзера)
def _lock(mid):   return f"duel:lock:{mid}"

MATCH_TTL = 3600        # сек: чистка зависших матчей
LOCK_PX = 3000
_REL_LUA = "if redis.call('get',KEYS[1])==ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end"


# --- операции с БД (синхронные, оборачиваем для async) ---
@sync_to_async
def _debit(user, bet, reason):
    with db_transaction.atomic():
        return debit(user, bet, reason)


@sync_to_async
def _credit(user_id, bet, reason):
    with db_transaction.atomic():
        credit(User.objects.get(pk=user_id), bet, reason)


@sync_to_async
def _save_duel(p1_id, p2_id, bet, winner_id, forfeit):
    Duel.objects.create(
        player1_id=p1_id, player2_id=p2_id, bet=bet,
        winner_id=winner_id, forfeit=forfeit,
    )


class DuelConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        if not self.scope["user"].is_authenticated:
            await self.close()
            return
        self.user = self.scope["user"]
        self.uid = str(self.user.id)
        self.bet = None
        try:
            self.r = await get_store()
        except ConnectionError:
            await self.close()
            return
        await self.accept()

    async def disconnect(self, code):
        try:
            if not getattr(self, "r", None):
                return
            state = await self.r.get(_u(self.uid))
            if state == "q":
                await self._leave_queue(refund=True)
            elif state:
                await self._forfeit(state)
        finally:
            if getattr(self, "r", None):
                await self.r.aclose()

    async def receive_json(self, content):
        action = content.get("action")
        if action == "queue":
            await self._queue(content.get("bet") or {})
        elif action == "cancel":
            await self._cancel()
        elif action in ("hit", "stand"):
            await self._play(action)

    # ---------- матчмейкинг ----------
    async def _queue(self, raw_bet):
        bet = _clean_bet(raw_bet)
        total = sum(bet.values())
        if total < duel.MIN_BET_TOTAL:
            return await self._err(f"Минимальная ставка — {duel.MIN_BET_TOTAL} коина.")
        # одна игра/очередь на пользователя
        if not await self.r.set(_u(self.uid), "q", nx=True):
            return await self._err("Вы уже в очереди или в игре.")
        # escrow: списываем ставку
        if not await _debit(self.user, bet, "Ставка в дуэли"):
            await self.r.delete(_u(self.uid))
            return await self._err("Недостаточно коинов для такой ставки.")
        self.bet = bet
        qkey = _q(duel.bet_key(bet))
        # пытаемся забрать соперника
        while True:
            opp_raw = await self.r.rpop(qkey)
            if opp_raw is None:
                # соперника нет — встаём в очередь
                entry = json.dumps({"uid": self.uid, "username": self.user.username,
                                    "channel": self.channel_name})
                await self.r.lpush(qkey, entry)
                self._qentry = entry
                self._qkey = qkey
                return await self.send_json({"type": "queued", "bet": bet})
            opp = json.loads(opp_raw)
            if opp["uid"] == self.uid:
                continue
            # соперник ещё ждёт? (не отменил, не отвалился)
            if await self.r.get(_u(opp["uid"])) != "q":
                continue  # протухшая запись — пропускаем
            await self._create_match(bet, opp)
            return

    async def _create_match(self, bet, opp):
        mid = uuid.uuid4().hex
        me = {"uid": self.uid, "username": self.user.username, "channel": self.channel_name}
        match = duel.new_match(mid, bet, me, opp)
        await self.r.set(_m(mid), json.dumps(match), ex=MATCH_TTL)
        # помечаем обоих как «в матче mid»
        await self.r.mset({_u(self.uid): mid, _u(opp["uid"]): mid})
        self.match_id = mid
        # уведомляем соперника (его потребитель присоединится) и себя
        await self.channel_layer.send(opp["channel"], {"type": "duel.matched", "mid": mid})
        await self._send_state(match, "matched")

    async def _cancel(self):
        if await self.r.get(_u(self.uid)) != "q":
            return await self._err("Вы не в очереди.")
        await self._leave_queue(refund=True)
        await self.send_json({"type": "cancelled"})

    async def _leave_queue(self, refund):
        qentry = getattr(self, "_qentry", None)
        if qentry:
            await self.r.lrem(getattr(self, "_qkey", ""), 0, qentry)
            self._qentry = None
        await self.r.delete(_u(self.uid))
        if refund and self.bet:
            await _credit(self.user.id, self.bet, "Возврат ставки (дуэль отменена)")
            self.bet = None

    # ---------- игровой ход ----------
    async def _play(self, action):
        mid = await self.r.get(_u(self.uid))
        if not mid or mid == "q":
            return await self._err("Вы не в активном матче.")
        token = await self._acquire(mid)
        if not token:
            return await self._err("Сервер занят, повторите ход.")
        try:
            raw = await self.r.get(_m(mid))
            if not raw:
                return
            match = json.loads(raw)
            if match["finished"]:
                return
            if action == "hit":
                duel.apply_hit(match, self.uid)
            else:
                duel.apply_stand(match, self.uid)
            await self.r.set(_m(mid), json.dumps(match), ex=MATCH_TTL)
        finally:
            await self._release(mid, token)

        if match["finished"]:
            await self._settle(match)
        else:
            await self._broadcast(match, "update")

    async def _settle(self, match):
        winner = match["winner"]
        bet = match["bet"]
        order = match["order"]
        if winner == "push":
            for uid in order:
                await _credit(int(uid), bet, "Ничья в дуэли (возврат ставки)")
            winner_id = None
        else:
            pot = {k: v * 2 for k, v in bet.items()}  # своя ставка + ставка соперника
            await _credit(int(winner), pot, "Выигрыш в дуэли")
            winner_id = int(winner)
        await _save_duel(int(order[0]), int(order[1]), bet, winner_id, match["forfeit"])
        # очищаем метки игроков, матч оставляем ненадолго (TTL уже стоит)
        await self.r.delete(_u(order[0]), _u(order[1]))
        await self._broadcast(match, "result")

    async def _forfeit(self, mid):
        token = await self._acquire(mid)
        if not token:
            return
        try:
            raw = await self.r.get(_m(mid))
            if not raw:
                return
            match = json.loads(raw)
            if match["finished"]:
                return
            duel.forfeit(match, self.uid)
            await self.r.set(_m(mid), json.dumps(match), ex=MATCH_TTL)
        finally:
            await self._release(mid, token)
        await self._settle(match)

    # ---------- рассылка состояния ----------
    async def _broadcast(self, match, kind):
        for uid in match["order"]:
            ch = match["players"][uid]["channel"]
            await self.channel_layer.send(ch, {"type": "duel.push", "mid": match["id"], "kind": kind})

    async def _send_state(self, match, kind):
        """Отправляет состояние ИМЕННО этому игроку (рука соперника скрыта)."""
        state = duel.build_state(match, self.uid)
        msg = {"type": kind, "state": state}
        if match["finished"]:
            msg["outcome"] = _outcome(match, self.uid)
        await self.send_json(msg)

    # ---------- обработчики сообщений канального слоя ----------
    async def duel_matched(self, event):
        self.match_id = event["mid"]
        raw = await self.r.get(_m(event["mid"]))
        if raw:
            await self._send_state(json.loads(raw), "matched")

    async def duel_push(self, event):
        raw = await self.r.get(_m(event["mid"]))
        if raw:
            await self._send_state(json.loads(raw), event["kind"])

    # ---------- лок ----------
    async def _acquire(self, mid, tries=150):
        token = uuid.uuid4().hex
        for _ in range(tries):
            if await self.r.set(_lock(mid), token, nx=True, px=LOCK_PX):
                return token
            await _asleep(0.02)
        return None

    async def _release(self, mid, token):
        try:
            await self.r.eval(_REL_LUA, 1, _lock(mid), token)
        except Exception:
            pass

    async def _err(self, msg):
        await self.send_json({"type": "error", "msg": msg})


def _outcome(match, uid):
    w = match["winner"]
    if w == "push":
        return "push"
    return "win" if w == uid else "lose"


def _clean_bet(raw):
    """raw: {code: amount} из клиента → валидируем в int>0."""
    out = {}
    for code, amount in (raw or {}).items():
        try:
            v = int(amount)
        except (TypeError, ValueError):
            continue
        if v > 0:
            out[str(code)] = v
    return out


async def _asleep(sec):
    import asyncio
    await asyncio.sleep(sec)
