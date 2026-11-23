"""Shelf Life live server: phones, projector and director in the browser.

    python -m shelflife.server             # http://localhost:8010
    SHELFLIFE_ADMIN_KEY=...                # director key; a random one is printed if unset
    OPENAI_API_KEY=...                       # read by the OpenAI client, never stored or sent to a browser
    SHELFLIFE_PUBLIC_URL=https://...       # join link for the QR code

Pages: /          phone: a student who shops in the corner shop
       /beamer    projector; with #key=... it carries the director's controls
       /director  director on a phone (needs #key=...)

The week runs evening by evening. Each evening the Night Shift Agent checks in at 20:00, 21:00
and 22:00; between check-ins the shop is open and the phones, simulated students and regulars
shop. After closing every student cooks, and the lecturer continues to the next evening.
Nothing about players is written to disk.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import random
import secrets
import threading
import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .agent import MODEL, MODELS, TOOLS, LLMManager, RuleManager, check_model, request_body
from .bots import STYLES, shop_once
from .shop import (BUDGET, BY_KEY, CASHIER_LIMIT, CHECKINS, DAY_NOTE, DAYS, FOOD_BANK_KG, GOAL, INJECTIONS, POLICY,
                   PRODUCTS, SHOP_DAYS, Household, Shop, day_name, fridge_expiry, next_open)

STATIC = Path(__file__).resolve().parent / "static"
ADMIN_KEY = os.environ.get("SHELFLIFE_ADMIN_KEY") or secrets.token_urlsafe(9)
NO_CACHE = {"Cache-Control": "no-store"}
NAME_MAX = 18
LOCK = threading.Lock()  # guards the shop between the agent's thread and the web requests
RULE_TEXT = "Only complete meals score. Whatever spoils in your fridge costs a point."
GROUP_NAMES = {"main": "main", "side": "side", "fresh": "fresh", "dairy": "yoghurt or milk", "morning": "rolls or berries"}

app = FastAPI(title="Shelf Life")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class Stale(Exception):
    """Raised in the agent's thread when the week was reset or the evening replayed under it."""


class LiveShop(Shop):
    """The shop with a lock around every agent action and a record of the agent's steps."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.steps: list[dict] = []
        self.flats: dict[str, Household] = {}  # phones by player id
        self.bots: list[tuple[Household, str]] = []  # simulated students and their shopping style
        self.rng_bots = random.Random(7)

    def use_tool(self, name: str, args: dict) -> str:
        with LOCK:
            return super().use_tool(name, args)


def ask_owner(action: dict) -> bool:
    """Called inside a tool while the shop is locked: wait for the lecturer's decision."""
    return live.wait_for_owner(action)


def clean_name(raw) -> str:
    s = "".join(c for c in str(raw or "") if c.isprintable())
    return " ".join(s.split())[:NAME_MAX].strip()


def urgency(shop: Shop, date: int, kind: str) -> str:
    """How urgent a batch is tonight: past, today (must go tonight), tomorrow, or ok."""
    if date < shop.day:
        return "past"
    if kind == "baked" or date < next_open(shop.day):
        return "today"
    if date == next_open(shop.day):
        return "tomorrow"
    return "ok"


def when(shop: Shop, date: int, kind: str) -> str:
    """The date on a label in one short word, for the shelf tiles."""
    if date < shop.day:
        return "expired"
    if kind == "baked":
        return "today"
    if date - shop.day > 60:
        return "next year"
    return "today" if date == shop.day else "tomorrow" if date == shop.day + 1 else DAYS[date % 7][:3]


# The director's choices survive a restart (a deploy restarts the server); nothing about players goes in here.
SETTINGS_FILE = Path(os.environ.get("SHELFLIFE_SETTINGS", "settings.json"))
# models the key cannot use, with the reason; checked once at start so the director sees it before choosing
MODEL_PROBLEMS: dict[str, str] = {}


def probe_models() -> None:
    for m in MODELS:
        problem = check_model(m)
        if problem:
            MODEL_PROBLEMS[m] = problem
        else:
            MODEL_PROBLEMS.pop(m, None)


KEPT = ("mode", "effort", "model", "town", "open_seconds", "step_pause", "budget", "holds")


class Live:
    def __init__(self) -> None:
        self.players: dict[str, dict] = {}  # pid -> {"name", "seen"}
        self.shop: LiveShop | None = None
        self.manager = None
        self.mode = "llm" if os.environ.get("OPENAI_API_KEY") else "rule"
        self.effort = "low"
        self.model = MODEL
        self.town = 30  # households in the town; simulated students fill what the phones leave free
        self.phase = "lobby"  # lobby | morning | checkin | open | night | week_over
        self.epoch = 0  # changes whenever the shop is replaced, so pages drop their animation state
        self.task: asyncio.Task | None = None
        self.paused = False
        self.go = False
        self.skip = False
        self.fast = False
        self.open_seconds = 60
        self.step_pause = 3.0
        self.budget = BUDGET  # each student's money for the week, set by the director
        self.deadline = 0.0
        self.snap = None  # the shop as it was before this evening began
        self.pending: dict | None = None  # an action waiting for the owner's approval
        self.pending_event: threading.Event | None = None
        self.decisions: list[dict] = []
        self.approval_seq = 0
        self.last_step_t = 0.0
        self.busy = False  # the agent is in a check-in
        self.holds = True  # stop before and after every check-in until the director goes on
        self.waiting: dict | None = None  # {"kind": before | after | night, "label": the director's next step}
        self.away_seconds = 45
        self.control = {"overlay": None, "reveal": False, "tour": None,  # tour: stop of the guided tour, or None
                        "music": True, "fx": True,  # sound on the projector, switched separately
                        "join_url": os.environ.get("SHELFLIFE_PUBLIC_URL")}
        self.load_settings()

    def load_settings(self) -> None:
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for k in KEPT:
            if k in saved:
                setattr(self, k, saved[k])
        for k in ("music", "fx"):
            if k in saved:
                self.control[k] = bool(saved[k])
        if self.model not in MODELS:
            self.model = MODEL
        if self.mode == "llm" and not os.environ.get("OPENAI_API_KEY"):
            self.mode = "rule"

    def save_settings(self) -> None:
        data = {k: getattr(self, k) for k in KEPT} | {k: self.control[k] for k in ("music", "fx")}
        try:
            SETTINGS_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")
        except OSError:
            pass  # the game runs on without remembering

    # ------------------------------------------------------------ players
    def join(self, name: str, pid: str | None) -> str:
        name = clean_name(name)
        if pid in self.players:
            if name:
                self.players[pid]["name"] = self.unique(name, pid)
            return pid
        pid = secrets.token_urlsafe(12)
        self.players[pid] = {"name": self.unique(name or f"Student {len(self.players) + 1}", pid), "seen": time.time()}
        if self.shop:
            self.add_flat(self.shop, pid)
        return pid

    def unique(self, name: str, pid: str | None) -> str:
        used = {p["name"].lower() for k, p in self.players.items() if k != pid}
        out, n = name, 2
        while out.lower() in used:
            out = f"{name[:NAME_MAX - 3]} {n}"
            n += 1
        return out

    def add_flat(self, shop: LiveShop, pid: str) -> None:
        """A phone joining during the week takes the place of a simulated student, if there is one."""
        if pid in shop.flats:
            return
        with LOCK:
            if shop.bots:
                shop.bots.pop()
            shop.flats[pid] = Household(self.players[pid]["name"], budget=self.budget)

    def name_of(self, pid: str) -> str:
        return self.players.get(pid, {}).get("name", "?")

    # ------------------------------------------------------------ the week
    async def start_week(self) -> None:
        await self.stop()
        phones = list(self.players)
        town = max(self.town, len(phones), 1)
        shop = LiveShop(scale=town / 30, seed=random.randrange(10**6), approve=ask_owner, approval_from=None)
        shop.flats = {pid: Household(self.players[pid]["name"], budget=self.budget) for pid in phones}
        shop.bots = [(Household(f"Simulated {i + 1}", budget=self.budget), STYLES[i % len(STYLES)])
                     for i in range(town - len(phones))]
        self.shop = shop
        self.manager = self.make_manager()
        self.epoch += 1
        self.control.update(overlay=None, reveal=False, tour=None)
        self.decisions = []
        self.task = asyncio.create_task(self.run())

    def make_manager(self):
        if self.mode == "llm":
            return LLMManager(model=self.model, effort=self.effort)
        return RuleManager()

    async def stop(self) -> None:
        if self.pending_event:
            self.pending["decision"] = "reject"
            self.pending_event.set()
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
        self.task = None
        self.paused = False
        self.busy = False

    def snapshot(self, shop: LiveShop):
        shop.approve = None  # a function of this server; not part of the copy
        try:
            return copy.deepcopy(shop)
        finally:
            shop.approve = ask_owner

    async def replay_evening(self) -> None:
        """Play tonight again from its start, with the owner's approval for risky actions."""
        if not self.snap:
            raise HTTPException(409, "no evening to replay")
        await self.stop()
        shop = copy.deepcopy(self.snap)
        shop.approve = ask_owner
        shop.approval_from = shop.day + 1
        for pid in self.players:  # phones that joined during the evening
            if pid not in shop.flats:
                shop.flats[pid] = Household(self.players[pid]["name"], budget=self.budget)
        self.shop = shop
        self.epoch += 1
        self.control.update(overlay=None, reveal=False)
        self.task = asyncio.create_task(self.run())

    async def run(self) -> None:
        shop = self.shop
        try:
            while shop.day + 1 < SHOP_DAYS:
                await self.evening(shop)
                self.phase = "night"
                last = shop.day + 1 >= SHOP_DAYS
                await self.wait_go("night", "Show the week's result" if last else f"Start {DAYS[shop.day + 1]} evening")
            self.phase = "week_over"
            self.control["overlay"] = "week"
        except asyncio.CancelledError:
            raise
        except Stale:
            pass

    async def wait_go(self, kind: str, label: str) -> None:
        """Wait until the director goes on; the label names the step that follows."""
        self.go = False
        self.waiting = {"kind": kind, "label": label}
        try:
            while not self.go:
                await asyncio.sleep(0.2)
        finally:
            self.waiting = None
        self.go = False

    async def evening(self, shop: LiveShop) -> None:
        self.snap = self.snapshot(shop)
        with LOCK:
            shop.start_day()
            shop.steps = [s for s in shop.steps if s["day"] >= shop.day - 1]
        self.manager.new_day()
        self.phase = "morning"
        await self.hold(2.0 if not self.fast else 0.8)
        for c in range(3):
            shop.checkin = c
            shop.closing = c == 2
            if self.holds:
                self.phase = "checkin"
                await self.wait_go("before", f"Start the {CHECKINS[c]} check-in")
            await self.checkin(shop, c)
            if c < 2:
                if self.holds:
                    await self.wait_go("after", f"Open the shop until {CHECKINS[c + 1]}")
                await self.open_phase(shop, c)
        with LOCK:
            shop.close()
            self.cook(shop)

    async def hold(self, seconds: float) -> None:
        left = seconds
        while left > 0 or self.paused:
            t = time.time()
            await asyncio.sleep(0.2)
            if not self.paused:
                left -= time.time() - t

    async def checkin(self, shop: LiveShop, c: int) -> None:
        self.phase = "checkin"
        self.busy = True
        with LOCK:
            shop.steps.append({"i": len(shop.steps), "day": shop.day, "checkin": c, "name": "wake", "args": {},
                               "result": shop.wake()})
        self.last_step_t = time.time()

        def on_step(step: dict) -> None:
            if self.shop is not shop:
                raise Stale()
            with LOCK:
                shop.steps.append({"i": len(shop.steps), "day": shop.day, "checkin": c, **step})
            self.last_step_t = time.time()
            end = time.time() + (0.4 if self.fast else self.step_pause)
            while time.time() < end or self.paused:
                if self.shop is not shop:
                    raise Stale()
                time.sleep(0.1)
            self.last_step_t = time.time()

        try:
            await self.hold(0.6 if self.fast else 1.8)  # the room reads the wake-up message
            await asyncio.to_thread(self.manager.checkin, shop, on_step)
        finally:
            self.busy = False
        if self.shop is not shop:
            raise Stale()
        await self.hold(0.5 if self.fast else 1.5)

    async def open_phase(self, shop: LiveShop, c: int) -> None:
        with LOCK:
            shop.sales_since, shop.asked_none = {}, {}
            wishes = shop.regulars(c)
        rng = shop.rng_bots
        actors = [("regular", k) for k in wishes] + [("bot", i) for i in range(len(shop.bots))]
        seconds = 15 if self.fast else self.open_seconds
        due = sorted((rng.uniform(0.3, seconds * 0.85), a) for a in actors)
        self.phase = "open"
        self.skip = False
        self.deadline = time.time() + seconds
        frozen = None  # the time left when the pause began: the clock stands still until it ends
        while True:
            if self.paused:
                frozen = self.deadline - time.time() if frozen is None else frozen
                self.deadline = time.time() + frozen
            else:
                frozen = None
                now = seconds - (self.deadline - time.time())
                while due and due[0][0] <= now:
                    self.act(shop, due.pop(0)[1], c)
            if self.skip or time.time() >= self.deadline:
                break
            await asyncio.sleep(0.2)
        for _, a in due:
            self.act(shop, a, c)
        away = [pid for pid in shop.flats if time.time() - self.players.get(pid, {}).get("seen", 0) > self.away_seconds]
        for pid in away:  # phones put away shop on autopilot
            self.bot_shop(shop, shop.flats[pid], "eager", c, self.name_of(pid))

    def act(self, shop: LiveShop, actor: tuple, c: int) -> None:
        kind, x = actor
        if kind == "regular":
            with LOCK:
                b = shop.buy(x)
                if b:
                    shop.event("buy", product=x, who=None, percent=b.percent, price=b.price())
        else:
            h, style = shop.bots[x] if x < len(shop.bots) else (None, None)
            if h:
                self.bot_shop(shop, h, style, c, None)

    def bot_shop(self, shop: LiveShop, h: Household, style: str, c: int, who: str | None) -> None:
        with LOCK:
            before = len(h.fridge)
            shop_once(shop, h, style, c, shop.rng_bots)
            for key, exp in h.fridge[before:]:
                shop.event("buy", product=key, who=who, percent=None, price=None)

    def cook(self, shop: LiveShop) -> None:
        flats = list(shop.flats.values()) + [h for h, _ in shop.bots]
        res = [h.cook(shop.day) for h in flats]
        t = shop.today
        t["households"] = len(flats)
        t["dinners"] = sum(1 for r in res if r["dinner"])
        t["breakfasts"] = sum(1 for r in res if r["breakfast"])
        t["spoiled"] = sum(len(r["spoiled"]) for r in res)
        t["spoiled_kg"] = round(sum(BY_KEY[k].kg for r in res for k in r["spoiled"]), 2)

    # ------------------------------------------------------------ approval
    def wait_for_owner(self, action: dict) -> bool:
        shop = self.shop
        self.approval_seq += 1
        req = {"id": self.approval_seq, **action, "decision": None, "day": shop.day if shop else None}
        ev = threading.Event()
        self.pending, self.pending_event = req, ev
        LOCK.release()
        try:
            ev.wait(timeout=300)
        finally:
            LOCK.acquire()
            self.pending = self.pending_event = None
        req["decision"] = req["decision"] or "reject"
        self.decisions.append(req)
        if self.shop is not shop:
            raise Stale()
        return req["decision"] == "approve"

    # ------------------------------------------------------------ views
    def approval_flag(self, shop: Shop) -> bool:
        """Whether risky actions need approval tonight, or from the next evening when the shop is closed."""
        if shop.approval_from is None:
            return False
        return shop.approval_from <= shop.day + (1 if self.phase in ("night", "lobby") or shop.day < 0 else 0)

    def time_left(self) -> float:
        if self.phase != "open":
            return 0.0
        return max(0.0, self.deadline - time.time())

    def clock(self) -> tuple[str, float]:
        """Game time and the share of the evening that has passed (20:00 = 0, 22:00 = 1)."""
        s = self.shop
        if not s or self.phase in ("lobby", "morning"):
            return "20:00", 0.0
        if self.phase in ("night", "week_over"):
            return "22:00", 1.0
        if self.phase == "checkin":
            return CHECKINS[s.checkin], s.checkin / 2
        total = 15 if self.fast else self.open_seconds
        part = min(1.0, max(0.0, 1 - self.time_left() / total))
        minutes = int(part * 60)
        return f"{20 + s.checkin}:{minutes:02d}" if minutes < 60 else f"{21 + s.checkin}:00", (s.checkin + part) / 2

    def shelf(self, shop: Shop) -> list[dict]:
        out = []
        for p in PRODUCTS:
            bs = []
            for b in shop.stock(p.key):
                bs.append({"qty": b.qty, "label": shop.label(b), "urgency": urgency(shop, b.date, p.kind),
                           "when": when(shop, b.date, p.kind),
                           "price": b.price(), "full": p.price, "percent": b.percent, "sellable": shop.sellable(b)})
            c = shop.cheapest(p.key)
            out.append({"key": p.key, "name": p.name, "groups": list(p.groups), "full": p.price, "batches": bs,
                        "total": sum(b["qty"] for b in bs),
                        "offer": {"price": c.price(), "percent": c.percent, "label": shop.label(c),
                                  "urgency": urgency(shop, c.date, p.kind),
                                  "left": sum(b.qty for b in shop.stock(p.key) if shop.sellable(b))} if c else None})
        return out

    def preview_shelf(self) -> list[dict]:
        """Before the week: the shelf as Monday's delivery will fill it, for the guided tour."""
        shop = Shop(scale=self.town / 30)
        shop.start_day()
        return self.shelf(shop)

    def step_view(self, s: dict) -> dict:
        out = {k: s[k] for k in ("i", "day", "checkin", "name", "args", "result") if k in s}
        if s["name"] == "food_bank_form":
            hidden = next((t for t in INJECTIONS.values() if t in s.get("result", "")), None)
            if hidden:
                out["result"] = s["result"].replace("\n\n" + hidden, "")
                out["hidden"] = hidden
        return out

    def totals(self, shop: Shop) -> dict:
        t = shop.today if shop.days else {}
        w = shop.week() if shop.days else {}
        booked = sum(BY_KEY[k].kg * q for k, q in shop.bookings.items()) + t.get("food_bank_kg", 0)
        return {
            "today": {"revenue": round(t.get("revenue", 0), 2), "profit": round(t.get("revenue", 0) - t.get("cost", 0), 2),
                      "sold": t.get("sold", 0), "food_bank_kg": round(t.get("food_bank_kg", 0), 1),
                      "booked_kg": round(booked, 1), "booked": bool(shop.bookings) or t.get("food_bank_kg", 0) > 0,
                      "cashier": shop.cashier_today, "cashier_limit": CASHIER_LIMIT,
                      "bin_kg": round(t.get("binned_kg", 0) + t.get("auto_binned_kg", 0), 1)},
            "week": {"revenue": w.get("revenue", 0), "profit": w.get("profit", 0), "sold": w.get("sold", 0),
                     "food_bank_kg": round(w.get("food_bank_kg", 0), 1), "cashier_kg": round(w.get("cashier_kg", 0), 1),
                     "bin_kg": round(w.get("binned_kg", 0) + w.get("auto_binned_kg", 0), 1)},
        }

    def nights(self, shop: Shop) -> list[dict]:
        rows = []
        for d in shop.days:
            if "dinners" not in d:
                continue
            rows.append({"day": DAYS[d["day"]], "profit": round(d["revenue"] - d["cost"], 2),
                         "saved_kg": round(d["food_bank_kg"] + d["cashier_kg"], 1),
                         "bin_kg": round(d["binned_kg"] + d["auto_binned_kg"], 1),
                         "dinners": d["dinners"], "breakfasts": d["breakfasts"], "households": d["households"],
                         "spoiled": d["spoiled"], "spoiled_kg": d["spoiled_kg"], "missed": d["asked_none"]})
        return rows

    def board(self, shop: LiveShop | None) -> list[dict]:
        if not shop:
            return []
        # only the phones: simulated students are not in shop.flats
        rows = sorted(((self.name_of(pid), h) for pid, h in shop.flats.items()), key=lambda x: (-x[1].points, x[0].lower()))
        out, rank, last = [], 0, None
        for i, (name, h) in enumerate(rows):
            if h.points != last:
                rank, last = i + 1, h.points
            out.append({"rank": rank, "name": name, "points": h.points, "meals": h.meals,
                        "breakfasts": h.breakfasts, "spoiled": h.spoiled, "budget": round(h.budget, 2)})
        return out

    def week_end(self, shop: LiveShop) -> dict:
        flats = list(shop.flats.values()) + [h for h, _ in shop.bots]
        w = shop.week()
        home = sum(h.spoiled_kg for h in flats)
        # every kilo delivered this week ends up in exactly one of these: the shares on the projector add up to 100 %
        return {"delivered_kg": round(w["delivered_kg"], 1),
                "eaten_kg": round(w["sold_kg"] - home, 1), "home_bin_kg": round(home, 1),
                "food_bank_kg": round(w["food_bank_kg"], 1), "cashier_kg": round(w["cashier_kg"], 1),
                "shop_bin_kg": round(w["binned_kg"] + w["auto_binned_kg"], 1),
                "shelf_kg": round(sum(b.qty * b.product.kg for b in shop.batches if b.qty > 0), 1),
                "saved_kg": round(w["food_bank_kg"] + w["cashier_kg"], 1),
                "profit": w["profit"], "revenue": w["revenue"], "households": len(flats)}

    def view(self, since: int, epoch: int) -> dict:
        shop = self.shop
        clock, progress = self.clock()
        mode = "llm" if isinstance(self.manager, LLMManager) else "rule" if self.manager else self.mode
        base = {"epoch": self.epoch, "phase": self.phase, "paused": self.paused, "fast": self.fast, "mode": mode,
                "model": self.manager.model if isinstance(self.manager, LLMManager) else self.model,
                "clock": clock, "progress": round(progress, 3), "time_left": round(self.time_left(), 1),
                "open_seconds": 15 if self.fast else self.open_seconds, "goal": GOAL, "players": len(self.players),
                "budget": self.budget,
                "control": self.control, "tools": [{"name": t["name"], "description": t["description"]} for t in TOOLS],
                "policy": POLICY}
        if not shop:
            return {**base, "day": -1, "days": [{"name": DAYS[i], "note": DAY_NOTE[i], "state": "future"} for i in range(SHOP_DAYS)],
                    "shelf": self.preview_shelf()}
        with LOCK:
            if epoch != self.epoch:
                since = 0
            day = max(shop.day, 0)
            out = {
                **base,
                "day": shop.day, "day_name": DAYS[day], "checkin": shop.checkin,
                "days": [{"name": DAYS[i], "note": DAY_NOTE[i],
                          "ordered": i < len(shop.days) and shop.days[i].get("ordered", False),
                          "state": "past" if i < shop.day else "today" if i == shop.day else "future"} for i in range(SHOP_DAYS)],
                "shelf": self.shelf(shop),
                "bookings": {BY_KEY[k].name: q for k, q in shop.bookings.items() if q},
                **self.totals(shop),
                "steps": [self.step_view(s) for s in shop.steps if s["day"] == shop.day],
                "busy": self.busy, "thinking": self.busy and isinstance(self.manager, LLMManager) and time.time() - self.last_step_t > 0.9 and not self.paused,
                "model_down": isinstance(self.manager, LLMManager) and self.manager.down,
                "waiting": self.waiting,
                "note": {"text": shop.note, "day": day_name(shop.note_day)} if shop.note else None,
                "approval": self.approval_flag(shop),
                "approval_from": shop.approval_from, "injection_day": shop.injection_day,
                "pending": self.pending, "decisions": self.decisions[-6:],
                "events": [{"n": since + i, **e} for i, e in enumerate(shop.events[since:])],
                "n_events": len(shop.events),
                "nights": self.nights(shop),
                "can_replay": self.snap is not None,
            }
            if self.control.get("overlay") == "ranking":
                out["ranking"] = self.board(shop)
            if self.phase == "week_over" or self.control.get("overlay") in ("board", "week"):
                out["board"] = self.board(shop)[:10]
                out["week_end"] = self.week_end(shop)
        return out

    def phone(self, pid: str) -> dict:
        p = self.players[pid]
        shop = self.shop
        out = {"name": p["name"], "phase": self.phase, "paused": self.paused, "rule": RULE_TEXT, "budget_start": self.budget,
               "epoch": self.epoch}
        if not shop or pid not in shop.flats:
            return out
        h = shop.flats[pid]
        clock, _ = self.clock()
        with LOCK:
            day = max(shop.day, 0)
            board = self.board(shop)
            me = next((r for r in board if r["name"] == p["name"]), None)
            out.update({
                "day": shop.day, "day_name": DAYS[day], "clock": clock, "time_left": round(self.time_left(), 1),
                "open_seconds": 15 if self.fast else self.open_seconds, "checkin": shop.checkin,
                "budget": round(h.budget, 2), "points": h.points, "rank": me["rank"] if me else None, "of": len(board),
                "shelf": [{k: v for k, v in s.items() if k != "batches"} for s in self.shelf(shop)],
                "fridge": self.fridge(h, day),
                "meals": self.meals(h, day),
                "agent_line": self.agent_line(shop),
                "tonight": h.log[-1] if h.log and h.log[-1]["day"] == shop.day and self.phase in ("night", "week_over") else None,
                "week": {"meals": h.meals, "breakfasts": h.breakfasts, "spoiled": h.spoiled},
            })
            if self.phase == "week_over":
                out["board"] = board[:10]
        return out

    def fridge(self, h: Household, day: int) -> list[dict]:
        groups: dict[tuple, int] = {}
        for key, exp in h.fridge:
            tag = "tonight" if exp <= day else "tomorrow" if exp == day + 1 else "keeps"
            groups[(key, tag)] = groups.get((key, tag), 0) + 1
        order = {"tonight": 0, "tomorrow": 1, "keeps": 2}
        return [{"key": k, "name": BY_KEY[k].name, "tag": t, "n": n}
                for (k, t), n in sorted(groups.items(), key=lambda kv: (order[kv[0][1]], kv[0][0]))]

    def meals(self, h: Household, day: int) -> dict:
        """What the student would cook tonight from the fridge as it is now."""
        trial = copy.deepcopy(h)
        r = trial.cook(day)

        def slots(groups, got):
            used = list(got or [])
            out = {}
            for g in groups:
                k = next((x for x in used if g in BY_KEY[x].groups), None)
                if k:
                    used.remove(k)
                out[g] = k
            return out

        dinner = slots(("main", "side", "fresh"), r["dinner"])
        breakfast = slots(("dairy", "morning"), r["breakfast"])
        if not r["dinner"]:  # show what is there already
            have = [k for k, e in h.fridge if e >= day]
            for g in dinner:
                dinner[g] = next((k for k in have if g in BY_KEY[k].groups), None)
                if dinner[g]:
                    have.remove(dinner[g])
        if not r["breakfast"]:
            have = [k for k, e in h.fridge if e >= day and k not in (dinner.values() if r["dinner"] else [])]
            for g in breakfast:
                breakfast[g] = next((k for k in have if g in BY_KEY[k].groups), None)
                if breakfast[g]:
                    have.remove(breakfast[g])
        return {"dinner": dinner, "breakfast": breakfast, "dinner_ok": bool(r["dinner"]),
                "breakfast_ok": bool(r["breakfast"]), "group_names": GROUP_NAMES}

    def demo_phone(self) -> dict:
        """A sample phone for the guided tour: Monday 20:25, three things bought, dinner still lacks something fresh."""
        shop = Shop(scale=self.town / 30)
        shop.start_day()
        h = Household("Your nickname", budget=self.budget)
        for key in ("beef", "pasta", "yoghurt"):
            b = shop.buy(key)
            h.budget = round(h.budget - b.price(), 2)
            h.fridge.append([key, fridge_expiry(b)])
        for b in shop.stock("sushi"):
            b.percent = 50
        sushi = shop.cheapest("sushi")
        day = max(shop.day, 0)
        return {"name": h.name, "phase": "open", "paused": False, "rule": RULE_TEXT, "budget_start": self.budget, "epoch": -1,
                "demo": True, "day": shop.day, "day_name": DAYS[day], "clock": "20:25", "time_left": 24, "open_seconds": 40, "checkin": 0,
                "budget": h.budget, "points": 0, "rank": None, "of": 0,
                "shelf": [{k: v for k, v in s.items() if k != "batches"} for s in self.shelf(shop)],
                "fridge": self.fridge(h, day), "meals": self.meals(h, day),
                "agent_line": f"Sushi box (use by today) now 50 % off: {sushi.price():.2f} EUR.", "tonight": None,
                "week": {"meals": 0, "breakfasts": 0, "spoiled": 0}}

    def agent_line(self, shop: LiveShop) -> str | None:
        for s in reversed(shop.steps):
            if s["day"] != shop.day:
                break
            if s["name"] in ("mark_down", "bin", "give_to_cashier", "send_to_food_bank", "food_bank_form", "order"):
                return s["result"].split("\n")[0][:140]
        return None


live = Live()


def need_admin(key: str | None) -> None:
    if not key or not secrets.compare_digest(key, ADMIN_KEY):
        raise HTTPException(403, "director key required")


# ---------------------------------------------------------------- pages
@app.get("/")
def phone_page():
    return FileResponse(STATIC / "phone.html", headers=NO_CACHE)


@app.get("/beamer")
def beamer_page():
    return FileResponse(STATIC / "beamer.html", headers=NO_CACHE)


@app.get("/director")
def director_page():
    return FileResponse(STATIC / "director.html", headers=NO_CACHE)


# ---------------------------------------------------------------- phones
@app.post("/api/join")
async def api_join(request: Request):
    body = await request.json() if (await request.body()) else {}
    pid = live.join(body.get("name", ""), body.get("pid"))
    return {"pid": pid, "name": live.players[pid]["name"]}


@app.get("/api/demo_phone")
def api_demo_phone():
    return live.demo_phone()


@app.get("/api/me")
def api_me(pid: str):
    if pid not in live.players:
        raise HTTPException(404, "unknown player")
    live.players[pid]["seen"] = time.time()
    return live.phone(pid)


@app.post("/api/buy")
async def api_buy(request: Request):
    body = await request.json()
    pid = body.get("pid", "")
    if pid not in live.players:
        raise HTTPException(404, "unknown player")
    live.players[pid]["seen"] = time.time()
    shop = live.shop
    if not shop or live.phase != "open" or live.paused:
        raise HTTPException(409, "The shop is not open right now.")
    if pid not in shop.flats:
        live.add_flat(shop, pid)
    h = shop.flats[pid]
    key = str(body.get("product", ""))
    if key not in BY_KEY:
        raise HTTPException(400, "unknown product")
    with LOCK:
        c = shop.cheapest(key)
        if not c:
            raise HTTPException(409, f"{BY_KEY[key].name} is sold out.")
        if c.price() > h.budget + 1e-9:
            raise HTTPException(409, "Not enough money left for this.")
        b = shop.buy(key)
        h.budget = round(h.budget - b.price(), 2)
        h.fridge.append([key, fridge_expiry(b)])
        shop.event("buy", product=key, who=live.players[pid]["name"], percent=b.percent, price=b.price())
    return live.phone(pid)


# ------------------------------------------------------------- projector
@app.get("/api/view")
def api_view(since: int = 0, epoch: int = 0):
    return live.view(since, epoch)


# -------------------------------------------------------------- director
def status() -> dict:
    shop = live.shop
    return {"phase": live.phase, "paused": live.paused, "fast": live.fast, "mode": live.mode, "effort": live.effort,
            "model": live.model, "models": MODELS, "model_problems": MODEL_PROBLEMS,
            "has_key": bool(os.environ.get("OPENAI_API_KEY")), "players": len(live.players),
            "day": shop.day if shop else -1, "day_name": DAYS[shop.day] if shop and shop.day >= 0 else None,
            "approval": bool(shop and live.approval_flag(shop)),
            "injection_day": shop.injection_day if shop else 4, "pending": live.pending,
            "can_replay": live.snap is not None, "open_seconds": live.open_seconds, "step_pause": live.step_pause, "budget": live.budget,
            "town": live.town, "control": live.control, "waiting": live.waiting, "holds": live.holds,
            "busy": live.busy, "time_left": round(live.time_left(), 1), "clock": live.clock()[0],
            "player_list": sorted(p["name"] for p in live.players.values())}


@app.get("/api/admin/status")
def admin_status(x_admin_key: str | None = Header(None)):
    need_admin(x_admin_key)
    return status()


@app.get("/api/admin/context")
def admin_context(x_admin_key: str | None = Header(None)):
    """What the language model receives with its next request this evening, as the API gets it."""
    need_admin(x_admin_key)
    m = live.manager
    if isinstance(m, LLMManager):
        return {"request": m.request(), "input_tokens": m.last_input_tokens, "tokens": m.tokens, "mode": "llm"}
    return {"request": request_body(live.model, live.effort, []), "input_tokens": 0, "tokens": 0,
            "mode": "rule" if m else live.mode}


@app.post("/api/admin/{action}")
async def admin(action: str, request: Request, x_admin_key: str | None = Header(None)):
    need_admin(x_admin_key)
    body = await request.json() if (await request.body()) else {}
    shop = live.shop
    if action == "start_week":
        if "mode" in body:
            if body["mode"] == "llm" and not os.environ.get("OPENAI_API_KEY"):
                raise HTTPException(409, "A model API key is required for the language model. Choose the fixed rule.")
            live.mode = body["mode"]
        await live.start_week()
    elif action == "continue":
        live.go = True
        if live.waiting and live.control.get("overlay") == "ranking":
            live.control["overlay"] = None  # the evening goes on, so the shop shows again
    elif action == "pause":
        live.paused = bool(body.get("on", not live.paused))
    elif action == "skip":
        live.skip = True
    elif action == "fast":
        live.fast = bool(body.get("on", not live.fast))
    elif action == "approval":
        if not shop:
            raise HTTPException(409, "Start the week first.")
        on = bool(body.get("on"))
        # tonight if the evening is still running, otherwise from the next evening on
        first = shop.day + 1 if live.phase in ("night", "lobby") or shop.day < 0 else shop.day
        shop.approval_from = first if on else None
    elif action == "decide":
        if live.pending and live.pending_event and body.get("id") == live.pending["id"]:
            live.pending["decision"] = "approve" if body.get("decision") == "approve" else "reject"
            live.pending_event.set()
    elif action == "injection":
        if not shop:
            raise HTTPException(409, "Start the week first.")
        d = body.get("day")
        shop.injection_day = None if d is None else int(d)
    elif action == "replay":
        await live.replay_evening()
    elif action == "control":
        for k in ("overlay", "reveal", "join_url", "tour"):
            if k in body:
                live.control[k] = body[k]
        for k in ("music", "fx"):
            if k in body:
                live.control[k] = bool(body[k])
        if not isinstance(live.control["tour"], int) or isinstance(live.control["tour"], bool):
            live.control["tour"] = None
        if "music" in body or "fx" in body:
            live.save_settings()
    elif action == "settings":
        for k, lo, hi in (("open_seconds", 5, 300), ("town", 1, 200)):
            if k in body:
                setattr(live, k, max(lo, min(hi, int(body[k]))))
        if "step_pause" in body:
            live.step_pause = max(0.0, min(10.0, float(body["step_pause"])))
        if "mode" in body:
            if body["mode"] == "llm" and not os.environ.get("OPENAI_API_KEY"):
                raise HTTPException(409, "A model API key is required for the language model. Choose the fixed rule.")
            live.mode = str(body["mode"])
        if "effort" in body:
            live.effort = str(body["effort"])
        if "model" in body:
            m = str(body["model"])
            if m not in MODELS:
                raise HTTPException(400, f"Unknown model {m}.")
            if not os.environ.get("OPENAI_API_KEY"):
                raise HTTPException(409, "A model API key is required for the language model.")
            problem = await asyncio.to_thread(check_model, m)
            if problem:
                MODEL_PROBLEMS[m] = problem
                raise HTTPException(409, problem)
            MODEL_PROBLEMS.pop(m, None)
            live.model = m
            if isinstance(live.manager, LLMManager):
                live.manager.model = m  # from the next request on, even in the middle of the week
        if "holds" in body:
            live.holds = bool(body["holds"])
        if "budget" in body:
            new = round(max(5.0, min(200.0, float(body["budget"]))), 2)
            delta = new - live.budget
            live.budget = new
            if live.shop and delta:
                # during a week everyone gets the difference, so the director can top up or take away
                with LOCK:
                    for h in [*live.shop.flats.values(), *(b for b, _ in live.shop.bots)]:
                        h.budget = max(0.0, round(h.budget + delta, 2))
        live.save_settings()
    elif action == "reset":
        await live.stop()
        live.shop = live.manager = live.snap = None
        live.phase = "lobby"
        live.epoch += 1
        live.control.update(overlay=None, reveal=False, tour=None)
        if body.get("players"):
            live.players.clear()
    else:
        raise HTTPException(404, "unknown action")
    return status()


@app.get("/api/qr.svg")
def api_qr(u: str):
    """QR code for the join link, made on the server so the projector needs no internet."""
    import io

    import segno

    buf = io.BytesIO()
    segno.make(u[:300], error="m").save(buf, kind="svg", scale=10, border=1, dark="#000", light="#fff")
    return Response(buf.getvalue(), media_type="image/svg+xml")


@app.exception_handler(Exception)
async def on_error(request: Request, exc: Exception):
    return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500)


def lan_address() -> str:
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "localhost"


def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8010"))
    if not live.control.get("join_url"):
        live.control["join_url"] = f"http://{lan_address()}:{port}/"
    if os.environ.get("OPENAI_API_KEY"):
        threading.Thread(target=probe_models, daemon=True).start()
    print(f"Shelf Life on http://localhost:{port}  ·  projector: http://localhost:{port}/beamer#key={ADMIN_KEY}  ·  "
          f"director: http://localhost:{port}/director#key={ADMIN_KEY}", flush=True)
    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
