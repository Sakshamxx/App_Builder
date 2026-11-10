"""Reference apps, their scopes and their approved criteria.

These are hand-written known-good applications (and one deliberately buggy variant) used to test the
trusted harness, the supplied components and the agent loops. They are not model output.
"""
from __future__ import annotations

import copy
import shutil
from pathlib import Path

APPS_DIR = Path(__file__).parent / "fixtures" / "apps"
REFERENCE_DATE = "2026-11-15T10:00:00"


ADV_DIR = Path(__file__).parent / "fixtures" / "adversarial"


def copy_adversarial(name: str, dest: Path) -> None:
    """Copy a hand-written adversarial app (right testids, wrong or hardcoded behaviour). See fixtures/adversarial/README.md."""
    dest.mkdir(parents=True, exist_ok=True)
    for f in (ADV_DIR / name).iterdir():
        (dest / f.name).write_text(f.read_text())


def copy_app(name: str, dest: Path, mutate: dict[str, tuple[str, str]] | None = None) -> None:
    """Copy a reference app. ``mutate`` maps file -> (old, new) text replacement (used for bug variants)."""
    dest.mkdir(parents=True, exist_ok=True)
    for f in (APPS_DIR / name).iterdir():
        text = f.read_text()
        if mutate and f.name in mutate:
            old, new = mutate[f.name]
            assert old in text, f"mutation target not found in {f.name}"
            text = text.replace(old, new)
        (dest / f.name).write_text(text)


def read_app(name: str, mutate: dict[str, tuple[str, str]] | None = None) -> dict[str, str]:
    out = {}
    for f in sorted((APPS_DIR / name).iterdir()):
        text = f.read_text()
        if mutate and f.name in mutate:
            text = text.replace(*mutate[f.name])
        out[f.name] = text
    return out


# the bug used for the repair demonstration: the dashboard adds up every month, not just this one
EXPENSE_BUG = {"dashboard.html": ("e.date.slice(0, 7) === month", "true")}


def _add(category: str, amount: str, date: str) -> list[dict]:
    return [
        {"action": "select", "target": "category", "value": category},
        {"action": "enter", "target": "amount", "value": amount},
        {"action": "enter", "target": "date", "value": date},
        {"action": "click", "target": "add-expense"},
    ]


# Food 25 on 2026-11-03, Travel 40 on 2026-11-10, Food 30 on 2026-10-28 (previous month).
# With the clock fixed at 2026-11-15 the dashboard total must be 65, and with a budget of 100 the remainder 35.
_THREE = _add("Food", "25", "2026-11-03") + _add("Travel", "40", "2026-11-10") + _add("Food", "30", "2026-10-28")

# A second, different set of inputs for the SAME behaviour (no new requirement): Food 10 and Food 5.50 this month, Travel 99 last month.
# Its monthly total is 15.50 and a 50 budget leaves 34.50; it is also inside the same week, so it stays valid if "monthly" changes to "weekly".
_VARIANT = _add("Food", "10", "2026-11-11") + _add("Food", "5.5", "2026-11-13") + _add("Travel", "99", "2026-10-20")

EXPENSE_LABELS = {
    "category": "Category", "amount": "Amount", "date": "Date", "add-expense": "Add expense button",
    "expense-item": "Expense in the list", "filter-category": "Category filter", "month-total": "This month's total",
    "budget": "Monthly budget", "save-budget": "Save budget button", "budget-remaining": "Budget remaining"}

EXPENSE_CRITERIA = {
    "fixed_time": REFERENCE_DATE,
    "labels": EXPENSE_LABELS,
    "criteria": [
        {"id": "C1", "title": "An added expense appears in the list", "requirement_ids": ["R1"],
         "steps": [{"action": "navigate", "page": "index.html"}] + _add("Food", "25", "2026-11-03")
                  + [{"action": "assert_count", "target": "expense-item", "count": 1}]},
        {"id": "C2", "title": "The dashboard total counts only this month", "requirement_ids": ["R2"],
         "steps": [{"action": "navigate", "page": "index.html"}] + _THREE
                  + [{"action": "navigate", "page": "dashboard.html"},
                     {"action": "assert_text", "target": "month-total", "equals": "65.00"}]},
        {"id": "C3", "title": "The category filter narrows the list", "requirement_ids": ["R3"],
         "steps": [{"action": "navigate", "page": "index.html"}] + _THREE
                  + [{"action": "filter", "target": "filter-category", "value": "Food"},
                     {"action": "assert_count", "target": "expense-item", "count": 2}]},
        {"id": "C4", "title": "The remaining budget is budget minus this month's total", "requirement_ids": ["R4"],
         "steps": [{"action": "navigate", "page": "settings.html"},
                   {"action": "enter", "target": "budget", "value": "100"},
                   {"action": "click", "target": "save-budget"},
                   {"action": "navigate", "page": "index.html"}] + _THREE
                  + [{"action": "navigate", "page": "dashboard.html"},
                     {"action": "assert_text", "target": "budget-remaining", "equals": "35.00"}]},
        {"id": "C5", "title": "Expenses survive a reload", "requirement_ids": ["R5"],
         "steps": [{"action": "navigate", "page": "index.html"}] + _add("Travel", "40", "2026-11-10")
                  + [{"action": "reload"}, {"action": "assert_count", "target": "expense-item", "count": 1}]},
        # second examples of the same behaviour, with different inputs and different expected results
        {"id": "C6", "title": "Two added expenses are listed and survive a reload", "requirement_ids": ["R1", "R5"],
         "steps": [{"action": "navigate", "page": "index.html"}] + _add("Food", "12", "2026-11-05") + _add("Travel", "8.5", "2026-11-06")
                  + [{"action": "assert_count", "target": "expense-item", "count": 2}, {"action": "reload"},
                     {"action": "assert_count", "target": "expense-item", "count": 2}]},
        {"id": "C7", "title": "The dashboard total with different expenses", "requirement_ids": ["R2"],
         "steps": [{"action": "navigate", "page": "index.html"}] + _VARIANT
                  + [{"action": "navigate", "page": "dashboard.html"},
                     {"action": "assert_text", "target": "month-total", "equals": "15.50"}]},
        {"id": "C8", "title": "Filtering by a different category", "requirement_ids": ["R3"],
         "steps": [{"action": "navigate", "page": "index.html"}] + _VARIANT
                  + [{"action": "filter", "target": "filter-category", "value": "Travel"},
                     {"action": "assert_count", "target": "expense-item", "count": 1}]},
        {"id": "C9", "title": "The remaining budget with a different budget and expenses", "requirement_ids": ["R4"],
         "steps": [{"action": "navigate", "page": "settings.html"},
                   {"action": "enter", "target": "budget", "value": "50"},
                   {"action": "click", "target": "save-budget"},
                   {"action": "navigate", "page": "index.html"}] + _VARIANT
                  + [{"action": "navigate", "page": "dashboard.html"},
                     {"action": "assert_text", "target": "budget-remaining", "equals": "34.50"}]},
    ],
}

EXPENSE_SCOPE = {
    "supported": True, "app_title": "Expense tracker",
    "summary": "Add expenses, see this month's total and the remaining budget, filter by category.",
    "pages": [{"filename": "index.html", "purpose": "Add and list expenses, filter by category"},
              {"filename": "dashboard.html", "purpose": "This month's total and remaining budget"},
              {"filename": "settings.html", "purpose": "Set the monthly budget"}],
    "requirements": [
        {"id": "R1", "text": "Users can add an expense with category, amount and date and see it listed."},
        {"id": "R2", "text": "The dashboard shows the total of expenses dated in the current month only."},
        {"id": "R3", "text": "Users can filter the list by category."},
        {"id": "R4", "text": "The dashboard shows the monthly budget minus the current month's total."},
        {"id": "R5", "text": "Expenses and the budget are saved in the browser and survive a reload."}],
    "unsupported": [], "proposed_alternative": None,
}

TIP_LABELS = {"bill": "Bill amount", "tip-percent": "Tip percentage", "people": "Number of people", "calculate": "Calculate button",
              "tip-amount": "Tip amount", "total-amount": "Total amount", "per-person": "Amount per person", "saved-tip": "Remembered tip note"}
TIP_CRITERIA = {"labels": TIP_LABELS, "criteria": [
    {"id": "C1", "title": "Tip, total and per-person amounts", "requirement_ids": ["R1"],
     "steps": [{"action": "navigate", "page": "index.html"},
               {"action": "enter", "target": "bill", "value": "80"},
               {"action": "select", "target": "tip-percent", "value": "15"},
               {"action": "enter", "target": "people", "value": "4"},
               {"action": "click", "target": "calculate"},
               {"action": "assert_text", "target": "tip-amount", "equals": "12.00"},
               {"action": "assert_text", "target": "total-amount", "equals": "92.00"},
               {"action": "assert_text", "target": "per-person", "equals": "23.00"}]},
    {"id": "C2", "title": "The last tip percentage is remembered", "requirement_ids": ["R2"],
     "steps": [{"action": "navigate", "page": "index.html"},
               {"action": "select", "target": "tip-percent", "value": "20"},
               {"action": "click", "target": "calculate"},
               {"action": "reload"},
               {"action": "assert_text", "target": "saved-tip", "equals": "Last tip: 20%"}]},
    {"id": "C3", "title": "Tip, total and per-person amounts with different values", "requirement_ids": ["R1"],
     "steps": [{"action": "navigate", "page": "index.html"},
               {"action": "enter", "target": "bill", "value": "60"},
               {"action": "select", "target": "tip-percent", "value": "10"},
               {"action": "enter", "target": "people", "value": "3"},
               {"action": "click", "target": "calculate"},
               {"action": "assert_text", "target": "tip-amount", "equals": "6.00"},
               {"action": "assert_text", "target": "total-amount", "equals": "66.00"},
               {"action": "assert_text", "target": "per-person", "equals": "22.00"}]},
    {"id": "C4", "title": "A different last tip percentage is remembered", "requirement_ids": ["R2"],
     "steps": [{"action": "navigate", "page": "index.html"},
               {"action": "select", "target": "tip-percent", "value": "15"},
               {"action": "click", "target": "calculate"},
               {"action": "reload"},
               {"action": "assert_text", "target": "saved-tip", "equals": "Last tip: 15%"}]}]}
TIP_SCOPE = {
    "supported": True, "app_title": "Tip calculator", "summary": "Split a bill with a tip.",
    "pages": [{"filename": "index.html", "purpose": "Calculator"}],
    "requirements": [{"id": "R1", "text": "Shows tip, total and per-person amounts."},
                     {"id": "R2", "text": "Remembers the last tip percentage in the browser."}],
    "unsupported": [], "proposed_alternative": None}

HABIT_LABELS = {"habit-name": "New habit name", "add-habit": "Add habit button", "habit-item": "Habit in the list",
                "done-count": "Number done", "open-count": "Number still open"}
HABIT_CRITERIA = {"labels": HABIT_LABELS, "criteria": [
    {"id": "C1", "title": "Stats count done and open habits", "requirement_ids": ["R1", "R2"],
     "steps": [{"action": "navigate", "page": "index.html"},
               {"action": "enter", "target": "habit-name", "value": "Read"},
               {"action": "click", "target": "add-habit"},
               {"action": "click", "target": "habit-item"},
               {"action": "navigate", "page": "stats.html"},
               {"action": "assert_text", "target": "done-count", "equals": "1"},
               {"action": "assert_text", "target": "open-count", "equals": "0"}]},
    {"id": "C2", "title": "Habits survive a reload", "requirement_ids": ["R3"],
     "steps": [{"action": "navigate", "page": "index.html"},
               {"action": "enter", "target": "habit-name", "value": "Run"},
               {"action": "click", "target": "add-habit"},
               {"action": "reload"},
               {"action": "assert_count", "target": "habit-item", "count": 1}]},
    {"id": "C3", "title": "Stats with two habits, one done", "requirement_ids": ["R1", "R2"],
     "steps": [{"action": "navigate", "page": "index.html"},
               {"action": "enter", "target": "habit-name", "value": "Stretch"},
               {"action": "click", "target": "add-habit"},
               {"action": "click", "target": "habit-item"},
               {"action": "enter", "target": "habit-name", "value": "Journal"},
               {"action": "click", "target": "add-habit"},
               {"action": "navigate", "page": "stats.html"},
               {"action": "assert_text", "target": "done-count", "equals": "1"},
               {"action": "assert_text", "target": "open-count", "equals": "1"}]},
    {"id": "C4", "title": "Two habits survive a reload", "requirement_ids": ["R3"],
     "steps": [{"action": "navigate", "page": "index.html"},
               {"action": "enter", "target": "habit-name", "value": "Walk"},
               {"action": "click", "target": "add-habit"},
               {"action": "enter", "target": "habit-name", "value": "Cook"},
               {"action": "click", "target": "add-habit"},
               {"action": "reload"},
               {"action": "assert_count", "target": "habit-item", "count": 2}]}]}
HABIT_SCOPE = {
    "supported": True, "app_title": "Habit tracker", "summary": "Track habits and see counts.",
    "pages": [{"filename": "index.html", "purpose": "Add, toggle and filter habits"},
              {"filename": "stats.html", "purpose": "Done and open counts"}],
    "requirements": [{"id": "R1", "text": "Users can add habits and mark them done."},
                     {"id": "R2", "text": "A stats page shows how many are done and open."},
                     {"id": "R3", "text": "Habits are saved in the browser."}],
    "unsupported": [], "proposed_alternative": None}

APPS = {
    "expense_tracker": (EXPENSE_SCOPE, EXPENSE_CRITERIA),
    "tip_calculator": (TIP_SCOPE, TIP_CRITERIA),
    "habit_tracker": (HABIT_SCOPE, HABIT_CRITERIA),
}


def single_example(name: str) -> dict:
    """The criteria as they were before example variation: ONE example per behaviour (kept to demonstrate what a hardcoded app can pass)."""
    d = copy.deepcopy(APPS[name][1])
    keep = {"expense_tracker": 5, "tip_calculator": 2, "habit_tracker": 2}[name]
    d["criteria"] = d["criteria"][:keep]
    return d


def scope(name: str) -> dict:
    return copy.deepcopy(APPS[name][0])


def criteria(name: str) -> dict:
    return copy.deepcopy(APPS[name][1])
