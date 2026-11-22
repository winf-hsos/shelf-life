"""Simulated students for tests without phones: each feeds a flat for the week on a budget."""

from __future__ import annotations

import random

from .shop import BY_KEY, PRODUCTS, SHOP_DAYS, Household, Shop, fridge_expiry

STYLES = ("eager", "patient", "patient", "eager", "hunter")


def covered(h: Household, day: int) -> set:
    """Meal groups the fridge already covers for tonight (rough: one item counts for one group)."""
    have, used = set(), []
    for g in ("main", "side", "fresh", "dairy", "morning"):
        for it in h.fridge:
            if it[1] >= day and it not in used and g in BY_KEY[it[0]].groups:
                have.add(g)
                used.append(it)
                break
    return have


def best(shop: Shop, group: str):
    options = [(b.price(), b.key) for p in PRODUCTS if group in p.groups
               for b in [shop.cheapest(p.key)] if b is not None]
    return min(options) if options else None


def take(shop: Shop, h: Household, key: str) -> bool:
    b = shop.buy(key)
    if b is None:
        return False
    h.budget = round(h.budget - b.price(), 2)
    h.fridge.append([key, fridge_expiry(b)])
    return True


def shop_once(shop: Shop, h: Household, style: str, phase: int, rng: random.Random) -> None:
    day = shop.day
    left = SHOP_DAYS - day
    allowance = h.budget / left
    have = covered(h, day)
    plan = []
    for g in ("main", "side", "fresh"):
        if g not in have:
            o = best(shop, g)
            if o is None:
                plan = None
                break
            plan.append(o)
    if plan:
        cost = sum(p for p, _ in plan)
        discounted = any(p < BY_KEY[k].price for p, k in plan)
        wait = style == "patient" and phase == 0 and not discounted and rng.random() < 0.8
        if not wait and cost <= h.budget and cost <= allowance * 1.6:
            for _, k in plan:
                take(shop, h, k)
    have = covered(h, day)
    b_plan = [best(shop, g) for g in ("dairy", "morning") if g not in have]
    if b_plan and all(b_plan):
        cost = sum(p for p, _ in b_plan)
        if h.budget - cost >= 4.5 * (left - 1) and (phase == 1 or style == "eager"):
            for _, k in b_plan:
                take(shop, h, k)
    if style == "hunter":
        for p in PRODUCTS:
            c = shop.cheapest(p.key)
            if c and c.percent >= 50 and c.price() <= h.budget - 4.0 * (left - 1) and rng.random() < 0.5:
                take(shop, h, p.key)
