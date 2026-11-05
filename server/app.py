import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, quote

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import db
import geo

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("taxi")

BOT_TOKEN = os.environ["BOT_TOKEN"]
BASE_URL = os.environ.get("BASE_URL", "https://memstroy.app").rstrip("/")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
COMMISSION_PCT = float(os.getenv("COMMISSION_PCT", "10"))
COMMISSION_MIN = float(os.getenv("COMMISSION_MIN", "0.5"))
DEBT_LIMIT = float(os.getenv("DEBT_LIMIT", "10"))
PAY_INFO = os.getenv("PAY_INFO", "Реквизиты для оплаты уточняйте у администратора.")
FAV_WAIT = int(os.getenv("FAV_WAIT", "60"))
ARRIVE_M = 150  # radius for "arrived" detection, metres
WEB_DIR = os.path.join(os.path.dirname(__file__), "..", "web")
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

bot = Bot(BOT_TOKEN)
dp = Dispatcher()


# ---------------------------------------------------------------- helpers
def now() -> int:
    return int(time.time())


def commission(price: float) -> float:
    return round(max(COMMISSION_MIN, price * COMMISSION_PCT / 100), 2)


def debt(driver_id: int) -> float:
    r = db.q1(
        "SELECT COALESCE(SUM(CASE WHEN kind='commission' THEN amount ELSE -amount END),0) d "
        "FROM ledger WHERE driver_id=?",
        (driver_id,),
    )
    return round(r["d"], 2)


def is_busy(driver_id: int) -> bool:
    return bool(
        db.q1("SELECT 1 x FROM orders WHERE driver_id=? AND status='accepted'", (driver_id,))
    )


def money(v) -> str:
    return f"{float(v):.2f}".rstrip("0").rstrip(".") if v is not None else "0"


def get_order(oid):
    return db.q1("SELECT * FROM orders WHERE id=?", (oid,))


def get_user(uid):
    return db.q1("SELECT * FROM users WHERE tg_id=?", (uid,))


def save_photo(data_url, kind: str, uid: int):
    """Save a jpeg data URL, return public path or None."""
    if not data_url or not isinstance(data_url, str) or "," not in data_url:
        return None
    try:
        raw = base64.b64decode(data_url.split(",", 1)[1])
    except Exception:
        return None
    if len(raw) > 2_000_000 or not raw.startswith(b"\xff\xd8"):
        raise HTTPException(400, "Фото должно быть JPEG до 2 МБ")
    fn = f"{kind}_{uid}_{now()}.jpg"
    with open(os.path.join(UPLOAD_DIR, fn), "wb") as f:
        f.write(raw)
    return "/uploads/" + fn


def car_line(u: dict) -> str:
    return ", ".join(str(x) for x in (u.get("car"), u.get("color"), u.get("year")) if x)


def auth(request: Request) -> dict:
    init_data = request.headers.get("x-init-data", "")
    if not init_data:
        raise HTTPException(401, "Откройте приложение через Telegram")
    data = dict(parse_qsl(init_data, keep_blank_values=True))
    h = data.pop("hash", None)
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not h or not hmac.compare_digest(calc, h):
        raise HTTPException(401, "Неверная подпись")
    if now() - int(data.get("auth_date", 0)) > 7 * 86400:
        raise HTTPException(401, "Сессия устарела, откройте заново")
    u = json.loads(data["user"])
    name = (u.get("first_name", "") + " " + u.get("last_name", "")).strip() or "Без имени"
    db.x(
        "INSERT INTO users(tg_id,name,username,created) VALUES(?,?,?,?) "
        "ON CONFLICT(tg_id) DO UPDATE SET name=CASE WHEN real_name IS NOT NULL THEN name ELSE excluded.name END, username=excluded.username",
        (u["id"], name, u.get("username"), now()),
    )
    return get_user(u["id"])


def nav_links(o: dict) -> dict:
    def pt(lat, lng, text):
        return f"{lat},{lng}" if lat is not None else geo.full_query(text)

    pick = pt(o["from_lat"], o["from_lng"], o["from_text"])
    dest = pt(o["to_lat"], o["to_lng"], o["to_text"])

    def waze(lat, lng, text):
        if lat is not None:
            return f"https://waze.com/ul?ll={lat},{lng}&navigate=yes"
        return f"https://waze.com/ul?q={quote(geo.full_query(text))}&navigate=yes"

    base = "https://www.google.com/maps/dir/?api=1&travelmode=driving"
    return {
        "gm_pickup": f"{base}&destination={quote(pick)}",
        "gm_route": f"{base}&origin={quote(pick)}&destination={quote(dest)}",
        # from driver's current position: pickup -> destination
        "gm_full": f"{base}&waypoints={quote(pick)}&destination={quote(dest)}",
        "waze_pickup": waze(o["from_lat"], o["from_lng"], o["from_text"]),
        "waze_dest": waze(o["to_lat"], o["to_lng"], o["to_text"]),
    }


def order_view(o: dict, viewer: int, with_nav=False) -> dict:
    keys = (
        "id", "from_text", "to_text", "price", "status", "km", "eta_min", "created",
        "accepted_at", "arrived_at", "picked_at", "done_at", "commission",
    )
    d = {k: o[k] for k in keys}
    d["mine_client"] = o["client_id"] == viewer
    if o["driver_id"]:
        dr = get_user(o["driver_id"])
        d["driver"] = {
            "id": dr["tg_id"], "name": dr["name"], "car": dr["car"], "plate": dr["plate"],
            "phone": dr["phone"], "username": dr["username"], "color": dr["color"],
            "year": dr["year"], "car_photo": dr["car_photo"], "driver_photo": dr["driver_photo"],
        }
    cl = get_user(o["client_id"])
    d["client"] = {"id": cl["tg_id"], "name": cl["name"], "username": cl["username"]}
    if o["status"] == "done":
        d["net"] = round(o["price"] - (o["commission"] or 0), 2)
        d["fav"] = bool(
            db.q1(
                "SELECT 1 x FROM favorites WHERE client_id=? AND driver_id=?",
                (o["client_id"], o["driver_id"]),
            )
        )
    if with_nav:
        d["nav"] = nav_links(o)
    return d


# ---------------------------------------------------------------- notifications
def open_kb(oid: int, text="Открыть") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, web_app=WebAppInfo(url=f"{BASE_URL}/?o={oid}"))]
        ]
    )


async def notify(chat_id: int, text: str, oid: int | None = None, btn="Открыть"):
    try:
        await bot.send_message(chat_id, text, reply_markup=open_kb(oid, btn) if oid else None)
    except Exception as e:
        log.warning("notify %s failed: %s", chat_id, e)


def notif_text(o: dict, prefix: str) -> str:
    km = f"\n📏 {o['km']} км" if o["km"] else ""
    return f"{prefix}\n📍 {o['from_text']} → {o['to_text']}\n💶 {money(o['price'])} €{km}"


async def clear_notifs(oid: int, final_text: str | None = None):
    rows = db.q("SELECT chat_id, message_id FROM notifs WHERE order_id=?", (oid,))
    db.x("DELETE FROM notifs WHERE order_id=?", (oid,))
    for r in rows:
        try:
            if final_text:
                await bot.edit_message_text(final_text, chat_id=r["chat_id"], message_id=r["message_id"])
            else:
                await bot.delete_message(r["chat_id"], r["message_id"])
        except Exception:
            pass


async def broadcast(oid: int, only: int | None = None, prefix="🚕 Новый заказ"):
    o = get_order(oid)
    if not o or o["status"] != "open":
        return
    drivers = db.q(
        "SELECT tg_id FROM users WHERE is_driver=1 AND online=1 AND tg_id!=?", (o["client_id"],)
    )
    text = notif_text(o, prefix)
    for d in drivers:
        uid = d["tg_id"]
        if only and uid != only:
            continue
        if debt(uid) >= DEBT_LIMIT or is_busy(uid):
            continue
        if db.q1("SELECT 1 x FROM declines WHERE order_id=? AND driver_id=?", (oid, uid)):
            continue
        try:
            m = await bot.send_message(uid, text, reply_markup=open_kb(oid))
            db.x(
                "INSERT OR REPLACE INTO notifs(order_id,chat_id,message_id) VALUES(?,?,?)",
                (oid, uid, m.message_id),
            )
        except Exception as e:
            log.warning("broadcast to %s failed: %s", uid, e)
        await asyncio.sleep(0.05)


async def fav_fallback(oid: int):
    await asyncio.sleep(FAV_WAIT)
    o = get_order(oid)
    if o and o["status"] == "open" and o["target_driver_id"]:
        t = o["target_driver_id"]
        db.x("UPDATE orders SET target_driver_id=NULL WHERE id=?", (oid,))
        db.x("INSERT OR IGNORE INTO declines(order_id,driver_id) VALUES(?,?)", (oid, t))
        await broadcast(oid)


async def process_order(oid: int):
    o = get_order(oid)
    if not o:
        return
    try:
        async with aiohttp.ClientSession() as s:
            a = await geo.geocode(s, o["from_text"])
            await asyncio.sleep(1.1)
            b = await geo.geocode(s, o["to_text"])
            km = await geo.route_km(s, a, b) if a and b else None
        db.x(
            "UPDATE orders SET from_lat=?,from_lng=?,to_lat=?,to_lng=?,km=? WHERE id=?",
            (a[0] if a else None, a[1] if a else None, b[0] if b else None, b[1] if b else None, km, oid),
        )
    except Exception as e:
        log.warning("geo failed: %s", e)
    o = get_order(oid)
    if not o or o["status"] != "open":
        return
    if o["target_driver_id"]:
        await broadcast(oid, only=o["target_driver_id"], prefix="⭐ Заказ лично вам")
        asyncio.create_task(fav_fallback(oid))
    else:
        await broadcast(oid)


async def finish(oid: int, auto=False):
    o = get_order(oid)
    if not o or o["status"] != "accepted":
        return False
    c = commission(o["price"])
    n, _ = db.x(
        "UPDATE orders SET status='done', done_at=?, commission=? WHERE id=? AND status='accepted'",
        (now(), c, oid),
    )
    if not n:
        return False
    db.x(
        "INSERT INTO ledger(driver_id,kind,amount,order_id,created) VALUES(?,?,?,?,?)",
        (o["driver_id"], "commission", c, oid, now()),
    )
    await notify(o["client_id"], f"🏁 Поездка завершена\n💶 {money(o['price'])} €", oid, "Открыть")
    msg = f"🏁 Поездка завершена{' (авто)' if auto else ''}\n💶 {money(o['price'])} €\nКомиссия: {money(c)} €"
    d = debt(o["driver_id"])
    if d >= DEBT_LIMIT:
        db.x("UPDATE users SET online=0 WHERE tg_id=?", (o["driver_id"],))
        msg += f"\n\n⛔ Долг {money(d)} € — оплатите комиссию, чтобы принимать заказы."
    await notify(o["driver_id"], msg, oid)
    return True


# ---------------------------------------------------------------- API
@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.delete_webhook(drop_pending_updates=False)
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Такси", web_app=WebAppInfo(url=BASE_URL))
        )
    except Exception as e:
        log.warning("menu button: %s", e)
    task = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
    yield
    task.cancel()
    await bot.session.close()


app = FastAPI(lifespan=lifespan)


def active_client_order(uid):
    o = db.q1(
        "SELECT * FROM orders WHERE client_id=? AND status IN ('open','accepted') ORDER BY id DESC LIMIT 1",
        (uid,),
    )
    if o:
        return o
    return db.q1(
        "SELECT * FROM orders WHERE client_id=? AND status='done' AND done_at>? ORDER BY id DESC LIMIT 1",
        (uid, now() - 900),
    )


def active_driver_order(uid):
    o = db.q1("SELECT * FROM orders WHERE driver_id=? AND status='accepted' LIMIT 1", (uid,))
    if o:
        return o
    return db.q1(
        "SELECT * FROM orders WHERE driver_id=? AND status='done' AND done_at>? ORDER BY id DESC LIMIT 1",
        (uid, now() - 900),
    )


@app.get("/api/state")
async def state(request: Request, role: str = "client"):
    u = auth(request)
    uid = u["tg_id"]
    d = debt(uid) if u["is_driver"] else 0
    out = {
        "me": {
            "id": uid, "name": u["name"], "username": u["username"], "is_driver": bool(u["is_driver"]),
            "online": bool(u["online"]), "car": u["car"], "color": u["color"], "year": u["year"], "driver_photo": u["driver_photo"], "plate": u["plate"], "phone": u["phone"],
            "debt": d, "limit": DEBT_LIMIT, "blocked": d >= DEBT_LIMIT,
        },
        "now": now(),
    }
    co = active_client_order(uid)
    out["client_order"] = order_view(co, uid, True) if co else None
    if u["is_driver"]:
        do = active_driver_order(uid)
        out["driver_order"] = order_view(do, uid, True) if do else None
        if role == "driver" and u["online"] and d < DEBT_LIMIT and not (do and do["status"] == "accepted"):
            rows = db.q(
                "SELECT * FROM orders WHERE status='open' AND client_id!=? "
                "AND (target_driver_id IS NULL OR target_driver_id=?) "
                "AND id NOT IN (SELECT order_id FROM declines WHERE driver_id=?) "
                "ORDER BY id DESC LIMIT 30",
                (uid, uid, uid),
            )
            out["open_orders"] = [order_view(r, uid) for r in rows]
        else:
            out["open_orders"] = []
    return out


@app.get("/api/order/{oid}")
async def order_get(oid: int, request: Request):
    u = auth(request)
    o = get_order(oid)
    if not o:
        raise HTTPException(404, "Заказ не найден")
    uid = u["tg_id"]
    if o["status"] == "open" and u["is_driver"] and o["client_id"] != uid:
        return order_view(o, uid, True)
    if uid not in (o["client_id"], o["driver_id"]):
        raise HTTPException(403, "Нет доступа")
    return order_view(o, uid, True)


@app.post("/api/driver/register")
async def driver_register(request: Request):
    u = auth(request)
    b = await request.json()
    if not u["username"]:
        raise HTTPException(400, "Установите @username в настройках Telegram и откройте приложение заново")
    name = " ".join(str(b.get("name", "")).split())[:50]
    if len(name.split()) < 2:
        raise HTTPException(400, "Укажите имя и фамилию")
    car = str(b.get("car", "")).strip()[:40]
    color = str(b.get("color", "")).strip()[:20]
    try:
        year = int(b.get("year"))
    except Exception:
        year = 0
    if not color:
        raise HTTPException(400, "Укажите цвет авто")
    if not (1980 <= year <= time.gmtime().tm_year + 1):
        raise HTTPException(400, "Укажите год авто")
    plate = str(b.get("plate", "")).strip().upper()[:12]
    phone = str(b.get("phone", "")).strip()[:20]
    if len(car) < 2 or len(plate) < 3:
        raise HTTPException(400, "Укажите марку авто и номер")
    db.x(
        "UPDATE users SET is_driver=1, name=?, real_name=?, car=?, color=?, year=?, plate=?, phone=? WHERE tg_id=?",
        (name, name, car, color, year, plate, phone, u["tg_id"]),
    )
    cp = save_photo(b.get("car_photo"), "car", u["tg_id"])
    dph = save_photo(b.get("driver_photo"), "drv", u["tg_id"])
    if cp:
        db.x("UPDATE users SET car_photo=? WHERE tg_id=?", (cp, u["tg_id"]))
    if dph:
        db.x("UPDATE users SET driver_photo=? WHERE tg_id=?", (dph, u["tg_id"]))
    return {"ok": True}


@app.post("/api/driver/online")
async def driver_online(request: Request):
    u = auth(request)
    if not u["is_driver"]:
        raise HTTPException(403, "Вы не водитель")
    b = await request.json()
    on = bool(b.get("online"))
    if on and debt(u["tg_id"]) >= DEBT_LIMIT:
        raise HTTPException(403, f"Долг {DEBT_LIMIT:g} € — сначала оплатите комиссию")
    db.x("UPDATE users SET online=? WHERE tg_id=?", (int(on), u["tg_id"]))
    if on != bool(u["online"]):
        txt = f"🟢 {u['name']} онлайн" if on else f"⚪ {u['name']} офлайн"
        for a in ADMIN_IDS:
            asyncio.create_task(notify(a, txt))
    return {"ok": True}


@app.post("/api/order")
async def order_create(request: Request):
    u = auth(request)
    b = await request.json()
    f = str(b.get("from", "")).strip()[:120]
    t = str(b.get("to", "")).strip()[:120]
    try:
        price = round(float(b.get("price")), 2)
    except Exception:
        raise HTTPException(400, "Неверная цена")
    if len(f) < 3 or len(t) < 3:
        raise HTTPException(400, "Укажите адреса")
    if not (1 <= price <= 200):
        raise HTTPException(400, "Цена от 1 до 200 €")
    if db.q1(
        "SELECT 1 x FROM orders WHERE client_id=? AND status IN ('open','accepted')", (u["tg_id"],)
    ):
        raise HTTPException(400, "У вас уже есть активный заказ")
    target = b.get("target_driver_id")
    if target:
        target = int(target)
        if not db.q1(
            "SELECT 1 x FROM favorites WHERE client_id=? AND driver_id=?", (u["tg_id"], target)
        ):
            target = None
    _, oid = db.x(
        "INSERT INTO orders(client_id,from_text,to_text,price,status,target_driver_id,created) "
        "VALUES(?,?,?,?,?,?,?)",
        (u["tg_id"], f, t, price, "open", target, now()),
    )
    asyncio.create_task(process_order(oid))
    return {"id": oid}


@app.post("/api/order/{oid}/accept")
async def order_accept(oid: int, request: Request):
    u = auth(request)
    uid = u["tg_id"]
    if not u["is_driver"]:
        raise HTTPException(403, "Только для водителей")
    if debt(uid) >= DEBT_LIMIT:
        raise HTTPException(403, f"Долг {DEBT_LIMIT:g} € — сначала оплатите комиссию")
    if is_busy(uid):
        raise HTTPException(400, "Сначала завершите текущий заказ")
    b = await request.json()
    try:
        eta = int(b.get("eta"))
    except Exception:
        raise HTTPException(400, "Укажите минуты")
    if not (1 <= eta <= 90):
        raise HTTPException(400, "Минуты от 1 до 90")
    n, _ = db.x(
        "UPDATE orders SET status='accepted', driver_id=?, eta_min=?, accepted_at=? "
        "WHERE id=? AND status='open' AND client_id!=? AND (target_driver_id IS NULL OR target_driver_id=?)",
        (uid, eta, now(), oid, uid, uid),
    )
    if not n:
        raise HTTPException(409, "Заказ уже занят")
    o = get_order(oid)
    asyncio.create_task(clear_notifs(oid, "✅ Заказ принят другим водителем"))
    msg = (
        f"✅ Водитель найден\n👤 {u['name']}\n🚗 {car_line(u)} · {u['plate']}\n"
        f"⏱ Будет через {eta} мин"
    )
    asyncio.create_task(notify(o["client_id"], msg, oid))
    return order_view(o, uid, True)


@app.post("/api/order/{oid}/release")
async def order_release(oid: int, request: Request):
    u = auth(request)
    uid = u["tg_id"]
    n, _ = db.x(
        "UPDATE orders SET status='open', driver_id=NULL, eta_min=NULL, accepted_at=NULL, "
        "arrived_at=NULL WHERE id=? AND driver_id=? AND status='accepted' AND picked_at IS NULL",
        (oid, uid),
    )
    if not n:
        raise HTTPException(400, "Нельзя отказаться")
    db.x("INSERT OR IGNORE INTO declines(order_id,driver_id) VALUES(?,?)", (oid, uid))
    o = get_order(oid)
    asyncio.create_task(notify(o["client_id"], "⚠️ Водитель отказался, ищем другого", oid))
    asyncio.create_task(broadcast(oid, prefix="🔁 Заказ снова свободен"))
    return {"ok": True}


@app.post("/api/order/{oid}/raise")
async def order_raise(oid: int, request: Request):
    u = auth(request)
    b = await request.json()
    try:
        delta = float(b.get("delta", 0.5))
    except Exception:
        delta = 0.5
    delta = max(-20, min(delta, 20))
    o = get_order(oid)
    if not o or o["client_id"] != u["tg_id"] or o["status"] != "open" or delta == 0:
        raise HTTPException(400, "Нельзя изменить цену")
    new = round(max(1, min(200, o["price"] + delta)), 2)
    if new == o["price"]:
        return {"price": new}
    db.x("UPDATE orders SET price=? WHERE id=? AND status='open'", (new, oid))

    async def up():
        # price went up: fresh push to drivers
        await clear_notifs(oid)
        o2 = get_order(oid)
        if o2 and o2["target_driver_id"]:
            await broadcast(oid, only=o2["target_driver_id"], prefix="💶 Цена выросла")
        else:
            await broadcast(oid, prefix="💶 Цена выросла")

    async def down():
        # price went down: quietly edit existing notifications
        o2 = get_order(oid)
        for r in db.q("SELECT chat_id, message_id FROM notifs WHERE order_id=?", (oid,)):
            try:
                await bot.edit_message_text(
                    notif_text(o2, "🚕 Новый заказ"), chat_id=r["chat_id"],
                    message_id=r["message_id"], reply_markup=open_kb(oid),
                )
            except Exception:
                pass

    asyncio.create_task(up() if delta > 0 else down())
    return {"price": new}


@app.post("/api/order/{oid}/cancel")
async def order_cancel(oid: int, request: Request):
    u = auth(request)
    o = get_order(oid)
    if not o or o["client_id"] != u["tg_id"] or o["status"] not in ("open", "accepted"):
        raise HTTPException(400, "Нельзя отменить")
    db.x("UPDATE orders SET status='cancelled' WHERE id=? AND status IN ('open','accepted')", (oid,))
    asyncio.create_task(clear_notifs(oid, "❌ Заказ отменён клиентом"))
    if o["driver_id"]:
        asyncio.create_task(notify(o["driver_id"], "❌ Клиент отменил заказ"))
    return {"ok": True}


@app.post("/api/order/{oid}/picked")
async def order_picked(oid: int, request: Request):
    u = auth(request)
    n, _ = db.x(
        "UPDATE orders SET picked_at=? WHERE id=? AND driver_id=? AND status='accepted' AND picked_at IS NULL",
        (now(), oid, u["tg_id"]),
    )
    if not n:
        raise HTTPException(400, "Нельзя")
    return {"ok": True}


@app.post("/api/order/{oid}/complete")
async def order_complete(oid: int, request: Request):
    u = auth(request)
    o = get_order(oid)
    if not o or o["driver_id"] != u["tg_id"]:
        raise HTTPException(403, "Нет доступа")
    if not await finish(oid):
        raise HTTPException(400, "Нельзя завершить")
    return {"ok": True}


@app.post("/api/location")
async def location(request: Request):
    u = auth(request)
    b = await request.json()
    try:
        lat, lng = float(b["lat"]), float(b["lng"])
    except Exception:
        raise HTTPException(400, "bad coords")
    uid = u["tg_id"]
    db.x("UPDATE users SET lat=?,lng=?,loc_ts=? WHERE tg_id=?", (lat, lng, now(), uid))
    o = db.q1("SELECT * FROM orders WHERE driver_id=? AND status='accepted' LIMIT 1", (uid,))
    if not o:
        return {"ok": True}
    if o["from_lat"] is not None:
        dp = geo.haversine_m(lat, lng, o["from_lat"], o["from_lng"])
        if not o["arrived_at"] and dp < ARRIVE_M:
            db.x("UPDATE orders SET arrived_at=? WHERE id=?", (now(), o["id"]))
            asyncio.create_task(notify(o["client_id"], "🚕 Водитель подъехал", o["id"]))
        elif o["arrived_at"] and not o["picked_at"] and dp > 300:
            db.x("UPDATE orders SET picked_at=? WHERE id=?", (now(), o["id"]))
            o["picked_at"] = now()
    if o["picked_at"] and o["to_lat"] is not None and now() - o["picked_at"] > 60:
        if geo.haversine_m(lat, lng, o["to_lat"], o["to_lng"]) < ARRIVE_M:
            await finish(o["id"], auto=True)
    return {"ok": True}


@app.get("/api/history")
async def history(request: Request, role: str = "client"):
    u = auth(request)
    uid = u["tg_id"]
    col = "driver_id" if role == "driver" else "client_id"
    rows = db.q(
        f"SELECT * FROM orders WHERE {col}=? AND status='done' ORDER BY id DESC LIMIT 40", (uid,)
    )
    return [order_view(r, uid) for r in rows]


@app.get("/api/favorites")
async def favorites(request: Request):
    u = auth(request)
    rows = db.q(
        "SELECT u.tg_id id, u.name, u.car, u.color, u.year, u.plate, u.online FROM favorites f "
        "JOIN users u ON u.tg_id=f.driver_id WHERE f.client_id=?",
        (u["tg_id"],),
    )
    return rows


@app.post("/api/favorites")
async def favorites_set(request: Request):
    u = auth(request)
    b = await request.json()
    did = int(b.get("driver_id"))
    if b.get("on"):
        if not db.q1(
            "SELECT 1 x FROM orders WHERE client_id=? AND driver_id=? AND status='done'",
            (u["tg_id"], did),
        ):
            raise HTTPException(400, "Можно добавить только водителя после поездки")
        db.x("INSERT OR IGNORE INTO favorites(client_id,driver_id) VALUES(?,?)", (u["tg_id"], did))
    else:
        db.x("DELETE FROM favorites WHERE client_id=? AND driver_id=?", (u["tg_id"], did))
    return {"ok": True}


@app.get("/api/driver/stats")
async def driver_stats(request: Request):
    u = auth(request)
    if not u["is_driver"]:
        raise HTTPException(403, "Вы не водитель")
    uid = u["tg_id"]
    s = db.q1(
        "SELECT COUNT(*) n, COALESCE(SUM(price),0) gross, COALESCE(SUM(commission),0) com "
        "FROM orders WHERE driver_id=? AND status='done'",
        (uid,),
    )
    extra = db.q1(
        "SELECT COALESCE(SUM(km),0) km, COUNT(DISTINCT date(done_at,'unixepoch')) days "
        "FROM orders WHERE driver_id=? AND status='done'",
        (uid,),
    )
    since = max(1, (now() - (u["created"] or now())) // 86400 + 1)
    paid = db.q1(
        "SELECT COALESCE(SUM(amount),0) p FROM ledger WHERE driver_id=? AND kind='payment'", (uid,)
    )["p"]
    pend = db.q1(
        "SELECT COALESCE(SUM(amount),0) p FROM pay_requests WHERE driver_id=? AND status='pending'",
        (uid,),
    )["p"]
    pays = db.q(
        "SELECT amount, created FROM ledger WHERE driver_id=? AND kind='payment' ORDER BY id DESC LIMIT 10",
        (uid,),
    )
    return {
        "km": round(extra["km"], 1), "work_days": extra["days"], "since_days": since,
        "count": s["n"], "gross": round(s["gross"], 2), "commission": round(s["com"], 2),
        "earned": round(s["gross"] - s["com"], 2), "paid": round(paid, 2),
        "pending": round(pend, 2), "debt": debt(uid), "limit": DEBT_LIMIT,
        "pay_info": PAY_INFO, "payments": pays, "pct": COMMISSION_PCT, "min": COMMISSION_MIN,
    }


@app.post("/api/driver/pay")
async def driver_pay(request: Request):
    u = auth(request)
    if not u["is_driver"]:
        raise HTTPException(403, "Вы не водитель")
    d = debt(u["tg_id"])
    if d <= 0:
        raise HTTPException(400, "Долга нет")
    if db.q1("SELECT 1 x FROM pay_requests WHERE driver_id=? AND status='pending'", (u["tg_id"],)):
        raise HTTPException(400, "Заявка уже отправлена, ждите подтверждения")
    _, rid = db.x(
        "INSERT INTO pay_requests(driver_id,amount,status,created) VALUES(?,?,?,?)",
        (u["tg_id"], d, "pending", now()),
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"pay:ok:{rid}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"pay:no:{rid}"),
        ]]
    )
    for a in ADMIN_IDS:
        try:
            await bot.send_message(
                a, f"{u['name']} оплатил {money(d)} €",
                reply_markup=kb,
            )
        except Exception as e:
            log.warning("admin notify: %s", e)
    return {"ok": True, "amount": d}


_last_chat = {}


@app.get("/api/chat")
async def chat_get(request: Request, after: int = 0):
    u = auth(request)
    if not u["is_driver"]:
        raise HTTPException(403, "Чат только для водителей")
    if after > 0:
        rows = db.q("SELECT * FROM chat WHERE id>? ORDER BY id ASC LIMIT 100", (after,))
    else:
        rows = db.q("SELECT * FROM (SELECT * FROM chat ORDER BY id DESC LIMIT 60) ORDER BY id ASC")
    return [
        {"id": r["id"], "name": r["name"], "text": r["text"], "created": r["created"],
         "mine": r["user_id"] == u["tg_id"]}
        for r in rows
    ]


@app.post("/api/chat")
async def chat_post(request: Request):
    u = auth(request)
    if not u["is_driver"]:
        raise HTTPException(403, "Чат только для водителей")
    b = await request.json()
    text = str(b.get("text", "")).strip()[:500]
    if not text:
        raise HTTPException(400, "Пустое сообщение")
    if time.time() - _last_chat.get(u["tg_id"], 0) < 1:
        raise HTTPException(429, "Не так быстро")
    _last_chat[u["tg_id"]] = time.time()
    db.x("INSERT INTO chat(user_id,name,text,created) VALUES(?,?,?,?)", (u["tg_id"], u["name"], text, now()))
    return {"ok": True}


# ---------------------------------------------------------------- bot
_welcome_file_id = None


@dp.message(CommandStart())
async def cmd_start(m: Message):
    global _welcome_file_id
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Открыть такси", web_app=WebAppInfo(url=BASE_URL))]]
    )
    caption = "Заказывайте поездку или принимайте заказы как водитель."
    photo = _welcome_file_id or FSInputFile(os.path.join(os.path.dirname(__file__), "assets", "welcome.jpg"))
    sent = await m.answer_photo(photo, caption=caption, reply_markup=kb)
    if not _welcome_file_id and sent.photo:
        _welcome_file_id = sent.photo[-1].file_id


@dp.message(Command("id"))
async def cmd_id(m: Message):
    await m.answer(f"Ваш ID: {m.from_user.id}")


@dp.message(Command("admin"))
async def cmd_admin(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        return
    s = db.q1("SELECT COUNT(*) n, COALESCE(SUM(price),0) g, COALESCE(SUM(commission),0) c FROM orders WHERE status='done'")
    dr = db.q1("SELECT COUNT(*) n FROM users WHERE is_driver=1")["n"]
    us = db.q1("SELECT COUNT(*) n FROM users")["n"]
    owed = db.q1(
        "SELECT COALESCE(SUM(CASE WHEN kind='commission' THEN amount ELSE -amount END),0) d FROM ledger"
    )["d"]
    await m.answer(
        f"Пользователей: {us}\nВодителей: {dr}\nПоездок: {s['n']}\nОборот: {money(s['g'])} €\n"
        f"Комиссия всего: {money(s['c'])} €\nВодители должны: {money(owed)} €"
    )


@dp.message(Command("pay"))
async def cmd_pay(m: Message):
    """/pay <tg_id> <amount> — записать оплату вручную"""
    if m.from_user.id not in ADMIN_IDS:
        return
    try:
        _, tid, amt = m.text.split()
        tid, amt = int(tid), float(amt)
    except Exception:
        return await m.answer("Формат: /pay <tg_id> <сумма>")
    db.x(
        "INSERT INTO ledger(driver_id,kind,amount,created,note) VALUES(?,?,?,?,?)",
        (tid, "payment", amt, now(), "manual"),
    )
    await m.answer(f"Записано. Долг водителя: {money(debt(tid))} €")
    await notify(tid, f"✅ Оплата {money(amt)} € принята. Долг: {money(debt(tid))} €")


@dp.callback_query(F.data.startswith("pay:"))
async def cb_pay(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS:
        return await c.answer("Нет доступа", show_alert=True)
    _, act, rid = c.data.split(":")
    r = db.q1("SELECT * FROM pay_requests WHERE id=?", (int(rid),))
    if not r or r["status"] != "pending":
        return await c.answer("Уже обработано")
    if act == "ok":
        db.x("UPDATE pay_requests SET status='ok' WHERE id=?", (r["id"],))
        db.x(
            "INSERT INTO ledger(driver_id,kind,amount,created,note) VALUES(?,?,?,?,?)",
            (r["driver_id"], "payment", r["amount"], now(), f"req{r['id']}"),
        )
        await notify(r["driver_id"], f"✅ Оплата {money(r['amount'])} € подтверждена. Можно принимать заказы.")
        await c.message.edit_text(c.message.text + "\n\n✅ Подтверждено")
    else:
        db.x("UPDATE pay_requests SET status='no' WHERE id=?", (r["id"],))
        await notify(r["driver_id"], "❌ Оплата не подтверждена. Свяжитесь с администратором.")
        await c.message.edit_text(c.message.text + "\n\n❌ Отклонено")
    await c.answer()


# ---------------------------------------------------------------- static
@app.get("/")
async def index():
    return FileResponse(
        os.path.join(WEB_DIR, "index.html"),
        headers={"Cache-Control": "no-store"},
    )


app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
