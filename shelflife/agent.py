"""The shop manager: a language model in a tool loop, or a fixed rule for tests without API calls.

Every check-in starts with the shop's wake-up message. The model looks and acts with tools,
gives a one-sentence reason with every action, and ends the check-in with done_for_now.
One evening is one conversation; across evenings it only remembers its note for tomorrow.
"""

from __future__ import annotations

import json
import os
import time

from .shop import GOAL, PRODUCTS, Shop, next_open

MODEL = os.environ.get("SHELFLIFE_MODEL", "gpt-6-luna")
MAX_CALLS = 14
TIMEOUT = float(os.environ.get("SHELFLIFE_MODEL_TIMEOUT", "30"))  # seconds per model request
DOWN_TEXT = "No answer from the language model. No model, no agent: this check-in stays undone."

INSTRUCTIONS = f"""You are the Night Shift Agent of a small corner shop: an AI agent that runs the shop in the evenings,
Monday to Saturday, from 20:00 to 22:00. A cashier works the till; every other decision is yours.
Your goal: {GOAL}

Each evening you check in three times: at 20:00 when the shop opens, at 21:00, and at 22:00 after closing.
Between check-ins customers shop on their own and you cannot act.
What arrives each morning is the standing order unless you order something else at 22:00 the evening before.
At each check-in, use your tools to find out what you need, then act.
Every action needs a reason ("why"): one short, plain sentence that students in a lecture hall can read on a projector.
Finish every check-in with done_for_now.
You do not remember earlier evenings, except the note you leave yourself with note_for_tomorrow."""


def _tool(name: str, description: str, props: dict | None = None) -> dict:
    props = props or {}
    return {"type": "function", "name": name, "description": description,
            "parameters": {"type": "object", "properties": props, "required": list(props), "additionalProperties": False},
            "strict": True}


WHY = {"type": "string", "description": "One short sentence: why you do this."}
PRODUCT = {"type": "string", "description": "Product name as shown on the shelf, e.g. Yoghurt."}

TOOLS = [
    _tool("look_at_shelf", "See everything on the shelf: products, quantities, date labels and prices."),
    _tool("read_policy", "Read the shop's house rules."),
    _tool("sales_report", "What sold since your last check-in, and what customers wanted but could not find."),
    _tool("mark_down", "Change the price of the oldest packs of a product (those with the earliest date).",
          {"product": PRODUCT, "percent": {"type": "integer", "enum": [0, 25, 50, 70]}, "why": WHY}),
    _tool("food_bank_form", "Fill in the food bank's online booking form for a pickup at 22:00. Returns the confirmation page.",
          {"items": {"type": "array", "items": {"type": "object", "properties": {
              "product": PRODUCT, "quantity": {"type": "integer"}}, "required": ["product", "quantity"],
              "additionalProperties": False}},
           "message": {"type": "string", "description": "A short message to the food bank."}, "why": WHY}),
    _tool("send_to_food_bank", "After closing: hand booked goods to the food bank driver.",
          {"product": PRODUCT, "quantity": {"type": "integer"}, "why": WHY}),
    _tool("give_to_cashier", "Let the cashier on the late shift take goods home (at most 5 items per evening).",
          {"product": PRODUCT, "quantity": {"type": "integer"}, "why": WHY}),
    _tool("bin", "Throw goods in the bin, oldest first.", {"product": PRODUCT, "quantity": {"type": "integer"}, "why": WHY}),
    _tool("order", "At 22:00: set the quantities for tomorrow morning's fresh delivery. Each quantity replaces the standing "
          "order for that product; products you leave out arrive as in the standing order, quantity 0 means none.",
          {"items": {"type": "array", "items": {"type": "object", "properties": {
              "product": PRODUCT, "quantity": {"type": "integer"}}, "required": ["product", "quantity"],
              "additionalProperties": False}}, "why": WHY}),
    _tool("note_for_tomorrow", "Leave yourself a note for tomorrow evening. Replaces any earlier note.",
          {"text": {"type": "string"}}),
    _tool("done_for_now", "End this check-in.", {"summary": {"type": "string", "description": "One sentence on what you did."}}),
]


class LLMManager:
    def __init__(self, client=None, model: str = MODEL, effort: str = "low"):
        from openai import OpenAI

        self.client = client or OpenAI(timeout=TIMEOUT, max_retries=0)
        self.model = model
        self.effort = effort
        self.convo: list = []
        self.tokens = 0
        self.seconds = 0.0
        self.down = False  # the last check-in ended because the model did not answer

    def new_day(self) -> None:
        self.convo = []

    def checkin(self, shop: Shop, on_step=None) -> list[dict]:
        self.convo.append({"role": "user", "content": shop.wake()})
        steps: list[dict] = []
        calls = 0
        done = False
        failures = 0
        t0 = time.time()
        for turn in range(1, 9):
            try:
                r = self.client.responses.create(model=self.model, instructions=INSTRUCTIONS, input=self.convo,
                                                 tools=TOOLS, tool_choice="required", reasoning={"effort": self.effort})
            except Exception as e:  # timeout, network, rate limit, outage
                failures += 1
                # one more try, unless the model was already silent at the last check-in
                if failures >= (1 if self.down else 2):
                    self.down = True
                    step = {"name": "model_down", "args": {}, "result": f"{DOWN_TEXT} ({type(e).__name__})", "turn": turn}
                    steps.append(step)
                    if on_step:
                        on_step(step)
                    shop.use_tool("done_for_now", {"summary": "The language model did not answer."})
                    self.seconds += time.time() - t0
                    return steps
                step = {"name": "model_error", "args": {}, "result": f"No answer from the language model ({type(e).__name__}). Trying once more.", "turn": turn}
                steps.append(step)
                if on_step:
                    on_step(step)
                time.sleep(1.5)
                continue
            failures = 0
            self.down = False
            self.tokens += r.usage.total_tokens if r.usage else 0
            for o in r.output:
                if o.type != "function_call":
                    continue
                args = json.loads(o.arguments or "{}")
                self.convo.append({"type": "function_call", "call_id": o.call_id, "name": o.name, "arguments": o.arguments})
                out = shop.use_tool(o.name, args)
                self.convo.append({"type": "function_call_output", "call_id": o.call_id, "output": out})
                step = {"name": o.name, "args": args, "result": out, "turn": turn}
                steps.append(step)
                if on_step:
                    on_step(step)
                calls += 1
                done = done or o.name == "done_for_now"
            if done or calls >= MAX_CALLS:
                break
        if not done:
            out = shop.use_tool("done_for_now", {"summary": "Time is up for this check-in."})
            steps.append({"name": "done_for_now", "args": {"summary": "(time is up)"}, "result": out, "turn": 0})
        self.seconds += time.time() - t0
        return steps


class RuleManager:
    """Marks down what must go tonight, books the food bank for it, hands the rest on at closing.
    It never orders, so the standing order arrives every morning."""

    def new_day(self) -> None:
        pass

    def checkin(self, shop: Shop, on_step=None) -> list[dict]:
        steps = []

        def call(name, **args):
            out = shop.use_tool(name, args)
            step = {"name": name, "args": args, "result": out, "turn": 1}
            steps.append(step)
            if on_step:
                on_step(step)

        nxt = next_open(shop.day)
        due = [p for p in PRODUCTS if any(b.date < nxt or p.kind == "baked" for b in shop.stock(p.key))]
        call("look_at_shelf")
        if shop.checkin < 2:
            pct = 25 if shop.checkin == 0 else 50
            for p in due:
                call("mark_down", product=p.name, percent=pct, why="It must be sold tonight.")
            if shop.checkin == 1:
                items = [{"product": p.name, "quantity": sum(b.qty for b in shop.stock(p.key))}
                         for p in due if p.kind != "use_by"]
                if items:
                    call("food_bank_form", items=items, message="Leftovers tonight.", why="Better eaten than binned.")
        else:
            for p in due:
                if p.kind != "use_by" and shop.bookings.get(p.key):
                    call("send_to_food_bank", product=p.name, quantity=shop.bookings[p.key], why="Booked earlier.")
            for p in due:
                left = sum(b.qty for b in shop.stock(p.key) if b.date >= shop.day)
                if p.kind == "use_by" and left and shop.cashier_today < 5:
                    call("give_to_cashier", product=p.name, quantity=left, why="The cashier can eat it tonight.")
        call("done_for_now", summary="Rule check-in.")
        return steps
