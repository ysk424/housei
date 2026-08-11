# SPDX-License-Identifier: GPL-3.0-or-later
"""Persist the verified sewing plan so a reader needs no Housei code.

Everything else a downstream exporter wants is already in the `.blend`: the
panels carry HOU, the drape is the mesh itself, and the flat pattern is a
vertex attribute.  The one thing that existed only as code was the seam
pairing -- which vertex is sewn to which.  Deriving it takes the whole of
``build_sewing_plan`` (ring pairing, composite loops, the sewing-group
completeness rules), and an exporter that reimplements those is the situation
kitsuke.py warns about: two readings of the same garment sewing two different
garments.

So the plan a successful Zero GRAVITY press sewed is written down on the
collection, as one JSON string next to ``housei_document_json``.  Pairs are
stored per label as ``[part slot, local vertex, part slot, local vertex]``,
so a reader never needs Housei's panel concatenation order; each part is
fingerprinted (vertex count, cut scheme, pitch) because vertex indices do not
survive a re-cut, and telling a stale plan from a current one is the reader's
one duty -- it refuses, it never repairs.

Nothing in Housei reads the property back.  The ZOZO hand-off keeps deriving
its pairs live, exactly as before; this write is additive, and the contract
for readers is `SEWING_PLAN_DESIGN.md`.
"""

from __future__ import annotations

from bisect import bisect_right
import json

from .mesh_loader import build_sewing_plan, part_spacing_m


SEWING_PLAN_PROPERTY = "housei_sewing_plan_json"
SEWING_PLAN_SCHEMA = "housei-sewing-plan/1.0.0"


def build_plan_payload(collection) -> dict:
    """The sewing plan as portable JSON data, from the state a press sews.

    Call this on the state ``sew_zero_gravity`` is about to read -- after
    seam counts are adapted and the preview removed.  ``build_sewing_plan``
    is deterministic in that state, so the payload names exactly the pairs
    the solver is given, not a pairing re-derived later from moved cloth.
    """
    plan = build_sewing_plan(collection)
    parts: list[dict] = []
    starts: list[int] = []
    offset = 0
    for slot, obj in enumerate(plan.parts):
        starts.append(offset)
        offset += len(obj.data.vertices)
        parts.append(
            {
                "object": obj.name,
                "instance": str(
                    obj.get("housei_panel_instance", obj.get("housei_panel_label", ""))
                ),
                "panel_id": str(obj.get("housei_panel_id", obj.name)),
                "panel_index": int(obj.get("housei_panel_index", slot)),
                "vertices": len(obj.data.vertices),
                "cut_scheme": int(obj.get("housei_cut_scheme", 0) or 0),
                "mesh_spacing_m": float(part_spacing_m(obj)),
            }
        )

    def local(global_index: int) -> tuple[int, int]:
        slot = bisect_right(starts, global_index) - 1
        return slot, global_index - starts[slot]

    pairs: dict[str, list[list[int]]] = {label: [] for label in plan.labels}
    for label, a, b in plan.connections:
        slot_a, vertex_a = local(int(a))
        slot_b, vertex_b = local(int(b))
        pairs[label].append([slot_a, vertex_a, slot_b, vertex_b])

    return {
        "schema": SEWING_PLAN_SCHEMA,
        "collection": collection.name,
        "labels": list(plan.labels),
        "parts": parts,
        "pairs": pairs,
        "pair_count": len(plan.connections),
    }


def store_plan_payload(collection, payload: dict) -> int:
    """Write the payload onto the collection.  Returns the pair count."""
    collection[SEWING_PLAN_PROPERTY] = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    )
    return int(payload.get("pair_count", 0))
