"""Play one week of Shelf Life without phones: the manager plus simulated students and regulars.

    python scripts/run_week.py --manager rule
    python scripts/run_week.py --manager llm --name w1

Prints every check-in step and a summary per day; writes runs/week-<name>.jsonl with all events.
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shelflife.agent import LLMManager, RuleManager  # noqa: E402
from shelflife.bots import STYLES, shop_once  # noqa: E402
from shelflife.shop import BY_KEY, CHECKINS, DAYS, SHOP_DAYS, Household, Shop  # noqa: E402


class Idle:
    """A manager who never acts: the baseline for waste."""

    def new_day(self):
        pass

    def checkin(self, shop, on_step=None):
        return []


def show(step: dict) -> None:
    a = dict(step.get("args") or {})
    why = a.pop("why", "")
    if why:
        print(f'      "{why}"')
    print(f"    > {step['name']}({json.dumps(a, ensure_ascii=False)[1:-1]})")
    res = step.get("result", "").replace("\n", " | ")
    print(f"      = {res[:300]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manager", choices=["llm", "rule", "idle"], default="rule")
    ap.add_argument("--supply", type=float, default=1.0)
    ap.add_argument("--llm-from", type=int, default=0, help="with --manager llm: days before this use the rule")
    ap.add_argument("--phones", type=int, default=30)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--injection", default="recall", help="injection text key, or none")
    ap.add_argument("--injection-day", type=int, default=4)
    ap.add_argument("--approval-from", type=int, default=5, help="first day with owner approval; -1 for never")
    ap.add_argument("--effort", default="low")
    ap.add_argument("--days", type=int, default=SHOP_DAYS)
    ap.add_argument("--name", default="")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--replay-with-approval", type=int, default=-1,
                    help="play this day twice, the second time with owner approval (who rejects)")
    a = ap.parse_args()

    shop = Shop(scale=a.phones / 30, seed=a.seed, supply=a.supply, injection_day=a.injection_day if a.injection != "none" else None,
                injection=a.injection, approval_from=None if a.approval_from < 0 else a.approval_from)
    mgr = LLMManager(effort=a.effort) if a.manager == "llm" else RuleManager() if a.manager == "rule" else Idle()
    rule = RuleManager()
    rng = random.Random(a.seed + 100)
    flats = [Household(f"Student {i + 1}") for i in range(a.phones)]
    styles = [STYLES[i % len(STYLES)] for i in range(a.phones)]
    out = Path(__file__).resolve().parents[1] / "runs" / f"week-{a.name or a.manager}.jsonl"
    out.parent.mkdir(exist_ok=True)
    log = out.open("w", encoding="utf-8")

    day = 0
    snapshot = None
    while day < a.days:
        if day == a.replay_with_approval and snapshot is None:
            snapshot = copy.deepcopy((shop, flats, rng))
        shop.start_day()
        m = rule if a.manager == "llm" and shop.day < a.llm_from else mgr
        m.new_day()
        print(f"\n=== {DAYS[shop.day]} ===  delivery: " +
              ", ".join(f"{q} {BY_KEY[k].name.lower()}" for k, q in shop.delivered.items()))
        for c in range(3):
            shop.checkin = c
            shop.closing = c == 2
            if not a.quiet:
                print(f"  -- check-in {CHECKINS[c]}")
            steps = m.checkin(shop, on_step=None if a.quiet else show)
            log.write(json.dumps({"day": shop.day, "checkin": c, "steps": steps}, ensure_ascii=False) + "\n")
            if c < 2:
                shop.sales_since, shop.asked_none = {}, {}
                actors = [("reg", k) for k in shop.regulars(c)] + [("hh", i) for i in range(len(flats))]
                rng.shuffle(actors)
                for kind, x in actors:
                    if kind == "reg":
                        shop.buy(x)
                    else:
                        shop_once(shop, flats[x], styles[x], c, rng)
        shop.close()
        cooked = [h.cook(shop.day) for h in flats]
        d = shop.today
        dinners = sum(1 for c in cooked if c["dinner"])
        spoiled = sum(len(c["spoiled"]) for c in cooked)
        print(f"  day: revenue {d['revenue']:.2f} EUR, profit {d['revenue'] - d['cost']:.2f} EUR, sold {d['sold']} ({d['discounted']} marked down), "
              f"food bank {d['food_bank_kg']:.1f} kg, cashier {d['cashier_kg']:.1f} kg, binned by agent {d['binned_kg']:.1f} kg, "
              f"auto-binned {d['auto_binned_kg']:.1f} kg, kept {d['kept']}, sold out {d['asked_none']}")
        print(f"  students: {dinners}/{len(flats)} dinners, {spoiled} items spoiled; note: {shop.note[:160]!r}")
        log.write(json.dumps({"day": shop.day, "summary": d, "dinners": dinners, "spoiled": spoiled}) + "\n")
        if snapshot:
            shop, flats, rng = snapshot
            shop.approval_from = day
            snapshot = False
            print("\n##### the same day again, now with the owner's approval for risky actions #####")
            continue
        day += 1

    w = shop.week()
    pts = sorted(h.points for h in flats)
    print("\n=== week ===")
    print(json.dumps(w))
    print(f"students: points min {pts[0]}, median {pts[len(pts) // 2]}, max {pts[-1]}; "
          f"dinners {sum(h.meals for h in flats)}, breakfasts {sum(h.breakfasts for h in flats)}, "
          f"spoiled {sum(h.spoiled for h in flats)} items ({sum(h.spoiled_kg for h in flats):.1f} kg), "
          f"money left {sum(h.budget for h in flats):.2f} EUR")
    if a.manager == "llm":
        print(f"tokens {mgr.tokens}, model seconds {mgr.seconds:.0f}")
    log.write(json.dumps({"week": w, "events": shop.events}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
