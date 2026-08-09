# SPDX-License-Identifier: GPL-3.0-or-later
"""HOU custom property: one JSON string on each part with all Housei metadata.

A part is anything whose object carries a non-empty ``HOU`` string. Collection
membership is not required. Binary blocks (pattern coordinates, etc.) are
stored as base64-encoded NPY payloads inside the JSON.
"""

from __future__ import annotations

import base64
import io
import json
from typing import Any

import bpy
import numpy as np

HOU_KEY = "HOU"
HOU_SCHEMA = "housei-hou/1.0.0"


def is_hou_part(obj: bpy.types.Object | None) -> bool:
    """True when the object is a mesh with a HOU string (this program's part)."""
    if obj is None or obj.type != "MESH":
        return False
    raw = obj.get(HOU_KEY)
    return isinstance(raw, str) and bool(raw.strip())


def read_hou(obj: bpy.types.Object) -> dict[str, Any]:
    """Parse HOU JSON. Empty dict when missing or invalid."""
    raw = obj.get(HOU_KEY)
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_hou(obj: bpy.types.Object, data: dict[str, Any]) -> None:
    """Replace the HOU string. Always schema-stamped."""
    payload = dict(data)
    payload.setdefault("schema", HOU_SCHEMA)
    obj[HOU_KEY] = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def npy_to_b64(array: np.ndarray) -> str:
    buffer = io.BytesIO()
    np.save(buffer, np.ascontiguousarray(array))
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def b64_to_npy(text: str) -> np.ndarray:
    raw = base64.b64decode(text.encode("ascii"))
    return np.load(io.BytesIO(raw), allow_pickle=False)


def mesh_float_vector_attribute(mesh: bpy.types.Mesh, name: str) -> np.ndarray | None:
    attribute = mesh.attributes.get(name)
    if (
        attribute is None
        or attribute.domain != "POINT"
        or attribute.data_type != "FLOAT_VECTOR"
        or len(attribute.data) != len(mesh.vertices)
    ):
        return None
    block = np.empty((len(mesh.vertices), 3), dtype=np.float64)
    attribute.data.foreach_get("vector", block.ravel())
    return block


def pack_part_hou(
    obj: bpy.types.Object,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a HOU dict from current object custom props and mesh attributes."""
    mesh = obj.data
    pattern = mesh_float_vector_attribute(mesh, "housei_pattern_position")
    construction = mesh_float_vector_attribute(mesh, "housei_construction_position")
    sewing_labels: list[str] = []
    for attribute in mesh.attributes:
        if attribute.domain == "EDGE" and attribute.name.startswith("sewing_"):
            label = attribute.name[len("sewing_") :]
            if label:
                sewing_labels.append(label)
    data: dict[str, Any] = {
        "schema": HOU_SCHEMA,
        "role": "part",
        "panel_id": str(obj.get("housei_panel_id", obj.name)),
        "panel_label": str(obj.get("housei_panel_label", "")),
        "panel_instance": str(obj.get("housei_panel_instance", "")),
        "panel_index": int(obj.get("housei_panel_index", 0)),
        "mirror_side": str(obj.get("housei_mirror_side", "")),
        "ring_closed": bool(obj.get("housei_ring_closed", False)),
        "mesh_spacing_m": float(obj.get("housei_mesh_spacing_m", 0.005)),
        "cut_scheme": int(obj.get("housei_cut_scheme", 0) or 0),
        "source_svg": str(obj.get("housei_source_svg", "")),
        "collection": str(obj.get("housei_collection", "")),
        "sewing_labels": sorted(set(sewing_labels)),
    }
    if pattern is not None:
        data["pattern_position_npy_b64"] = npy_to_b64(pattern)
    if construction is not None:
        data["construction_position_npy_b64"] = npy_to_b64(construction)
    if extra:
        data.update(extra)
    return data


def sync_hou_from_object(obj: bpy.types.Object, *, extra: dict[str, Any] | None = None) -> None:
    """Rewrite HOU from the object's current Blender state."""
    write_hou(obj, pack_part_hou(obj, extra=extra))
