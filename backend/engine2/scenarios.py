"""
scenarios.py — a scenario is a set of edits to a COPY of the snapshot's inputs.

Phase 1 kinds (both are pure input edits; Engine 1's simulator runs unchanged):

  ElevateOrders   "what if these orders were the top priority?" — the chosen
                  orders (in the planner's order) rank above every other order
                  at every operation; Engine 1's own batching/release/no-
                  preemption rules then form the batches around them. Done by
                  overriding the CDD USED FOR RANKING on the copied WIP rows.
                  Impact is always measured against the real CDD
                  (Snapshot.real_cdd). An overridden CDD is far in the past, so
                  the order is also in-window for §E.6/§E.16 — an expedited
                  order should be able to anchor/consolidate.

  MachinePriority "what if this combo+task ranked its machines this way (or had
                  this extra machine)?" — an edited copy of
                  MCH_MACHINE_FNL_MASTER. Powers the Priority Shuffler's
                  "Preview impact"; it never writes the master (that is the
                  Shuffler's own audited path).

`apply(snapshot)` returns (raw_rows, fnl_df): the inputs the runner simulates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from engine2.snapshot import Snapshot

# Elevated orders get CDD = ELEVATION_BASE + k days (k = position in the
# planner's list): earlier than any real CDD, so rank 0..n-1, in list order.
ELEVATION_BASE = date(2000, 1, 1)


class ScenarioError(ValueError):
    """A scenario the planner can fix (unknown order, bad machine list, …)."""


def _norm(v) -> str:
    """Canonical key text: NULL/NaN -> '', 8.0 -> '8', trimmed."""
    if v is None:
        return ""
    if isinstance(v, float):
        if math.isnan(v):
            return ""
        if v.is_integer():
            return str(int(v))
    return str(v).strip()


# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ElevateOrders:
    orders: tuple[str, ...]
    kind: str = "elevate"

    def describe(self) -> str:
        text = "Elevate " + ", ".join(self.orders)
        return text if len(text) <= 190 else text[:187] + "..."

    def elevated_orders(self) -> frozenset[str]:
        return frozenset(self.orders)

    def apply(self, snap: Snapshot):
        if not self.orders:
            raise ScenarioError("Pick at least one order to elevate.")
        if len(set(self.orders)) != len(self.orders):
            raise ScenarioError("An order was listed more than once.")
        known = {r.production_order for r in snap.raw_rows}
        missing = [o for o in self.orders if o not in known]
        if missing:
            raise ScenarioError(f"Unknown order(s): {', '.join(missing)}")
        rank = {order: ELEVATION_BASE + timedelta(days=k) for k, order in enumerate(self.orders)}
        rows = [replace(r, cdd=rank[r.production_order]) if r.production_order in rank else r for r in snap.raw_rows]
        return rows, snap.fnl_df


# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class AddedMachine:
    work_center: str
    fixture_change_time: Optional[float] = None
    locator_change_time: Optional[float] = None
    load_unload_time: Optional[float] = None


@dataclass(frozen=True)
class MachinePriority:
    size_inch: str
    class_val: str
    moc: str
    design: str
    task: str
    ordering: tuple[str, ...]  # machines, most preferred first; includes any added machines
    added: tuple[AddedMachine, ...] = field(default_factory=tuple)
    kind: str = "machine_priority"

    def describe(self) -> str:
        combo = f"{self.size_inch}~{self.class_val}~{self.moc or '(blank)'}~{self.design or '(blank)'} {self.task}"
        text = f"Machine priority {combo}: " + " > ".join(self.ordering)
        return text if len(text) <= 190 else text[:187] + "..."

    def elevated_orders(self) -> frozenset[str]:
        return frozenset()

    def apply(self, snap: Snapshot):
        df = snap.fnl_df
        key = tuple(_norm(v) for v in (self.size_inch, self.class_val, self.moc, self.design, self.task))
        mask = (
            (df["SIZE_INCH"].map(_norm) == key[0])
            & (df["CLASS"].map(_norm) == key[1])
            & (df["MOC"].map(_norm) == key[2])
            & (df["DESIGN"].map(_norm) == key[3])
            & (df["TASK"].map(_norm) == key[4])
        )
        combo = df[mask]
        if combo.empty:
            raise ScenarioError("No MCH_MACHINE_FNL_MASTER rows for this Size~Class~MOC~Design + Task.")

        existing = [_norm(w) for w in combo["WORK_CENTER"]]
        added_names = [_norm(a.work_center) for a in self.added]
        ordering = [_norm(w) for w in self.ordering]
        if len(set(ordering)) != len(ordering):
            raise ScenarioError("A machine appears more than once in the ordering.")
        if set(ordering) != set(existing) | set(added_names):
            raise ScenarioError("The ordering must list every machine of this combo (plus any added machine) exactly once.")
        for name in added_names:
            if name in existing:
                raise ScenarioError(f"{name} is already a capable machine for this combo+task.")
            if name not in snap.known_machines:
                raise ScenarioError(f"{name} has no availability data in the ERP — the engine could never place work on it.")

        edited = df.copy()
        if self.added:
            template = combo.sort_values("PRIORITY").iloc[0]
            new_rows = []
            for a in self.added:
                row = template.copy()
                row["WORK_CENTER"] = _norm(a.work_center)
                for col, value in (
                    ("FIXTURE_CHANGE_TIME", a.fixture_change_time),
                    ("LOCATOR_CHANGE_TIME", a.locator_change_time),
                    ("LOAD_UNLOAD_TIME", a.load_unload_time),
                ):
                    if value is not None and col in row.index:
                        row[col] = float(value)
                new_rows.append(row)
            edited = pd.concat([edited, pd.DataFrame(new_rows)], ignore_index=True)
            mask = (
                (edited["SIZE_INCH"].map(_norm) == key[0])
                & (edited["CLASS"].map(_norm) == key[1])
                & (edited["MOC"].map(_norm) == key[2])
                & (edited["DESIGN"].map(_norm) == key[3])
                & (edited["TASK"].map(_norm) == key[4])
            )

        rank = {name: i + 1 for i, name in enumerate(ordering)}
        edited.loc[mask, "PRIORITY"] = edited.loc[mask, "WORK_CENTER"].map(lambda w: rank[_norm(w)])
        return snap.raw_rows, edited
