# SPDX-License-Identifier: GPL-3.0-or-later
"""Create an initial cloth-ready Blender mesh from Housei pattern JSON."""

from __future__ import annotations

import math
import json
from dataclasses import dataclass
from itertools import permutations
from typing import Any, Iterable

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import barycentric_transform, delaunay_2d_cdt

from .i18n import msg


# How far a sleeve's two RING edges stand apart before they are sewn. Wide
# enough that an arm goes in and that the two sides are plainly separate
# surfaces at rest, small enough that closing it is a short seam.
RING_OPENING_M = 0.03
# A RING seam closes one part onto itself, so its label carries the part it
# belongs to and never matches a group authored in the pattern.
RING_LABEL_PREFIX = "RING_"
# The lattice pitch, and there is only one of it.
#
# It was 10 mm, with 5 mm kept for panels whose shorter pattern-page side fell
# under 5 cm so small pieces still read as cloth. The reason given for holding
# the large panels at 10 mm was that a full 5 mm mesh "tends to break the
# square-lattice solve" -- and that solver was Housei's own, gone since 0.13.0
# along with `native/` and `SOLVER_DESIGN.md`. Sewing is the contact solver's
# job now. The reason left with the solver it was about.
#
# What is left is a reason pointing the other way, and it is under the arm.
# Two panels have to slide past each other there, and at 10 mm they do not:
# a facet that coarse stands too far off the surface it approximates, so the
# two sheets catch on each other's ridges instead of passing. That standoff is
# the chord's sagitta and it falls with the square of the pitch, so halving the
# pitch quarters the ridge, and the cloth gets through.
#
# One pitch for every panel also means every seam is a same-pitch seam, which
# is the only case `compute_seam_count_overrides` has to reason about for a
# garment cut by this build.
MESH_SPACING_M = 0.005
# 0 = interior, 1 = a stitch-row vertex on a sewing boundary. Value 2 marked
# the proximity row of the seam paving band; the band is gone -- its cells were
# twice the interior size and its Delaunay transition strip put slivers right
# on every seam, the one place uniformity matters most -- so 2 is retired and
# must not be reused.
VERTEX_KIND_NORMAL = 0
VERTEX_KIND_EDGE = 1
VERTEX_KIND_ATTRIBUTE = "housei_vertex_kind"
# The shape of the triangulation this build cuts, recorded on every part as
# `housei_cut_scheme`. Bump it when the builder's output changes shape, so the
# ZOZO hand-off can tell a panel cut by an older builder from a current one
# and re-cut it. Scheme 1 (implicit on parts that never recorded one): square
# axis-aligned lattice with a 10 mm one-layer seam band. Scheme 2: uniform
# equilateral triangular lattice, no band, arc-length outline sampling --
# retired within a day, because its upgrade pass measured seam-count targets
# on the stale meshes it was about to replace, and a seam the old doubling bug
# had blown up to 16x its natural density became the target the clean side was
# forced to match. Scheme 3 cuts the same lattice but upgrades first and
# matches counts on what this build actually cuts.
CUT_SCHEME = 3
CUT_SCHEME_KEY = "housei_cut_scheme"
SHORTENABLE_EDGE_ATTRIBUTE = "housei_shortenable"
PANEL_GAP_M = 0.10
WORLD_Y_M = -1.0
BOTTOM_Z_M = 0.01
COLLECTION_PREFIX = "CLOTHES_"
CUTTING_PREFIX = "CUTTINGCLOTH_"
GRAINLINE_EDGE_FAMILY_ATTRIBUTE = "housei_grainline_family"
GRAINLINE_FACE_QUAD_ATTRIBUTE = "housei_grainline_quad"
GRAINLINE_EDGE_PROXY = 0
GRAINLINE_EDGE_WARP = 1
GRAINLINE_EDGE_WEFT = 2
GRAINLINE_EDGE_TRANSITION = 3
LOAD_MATRIX_KEY = "housei_load_matrix"
LOCKED_OBJECT_KEY = "housei_kitsuke_locked"
# World-Z lift applied by 裁断 so new copies are easy to grab.
CUT_OUT_Z_OFFSET_M = 0.30
GRAVITY_STATE_KEY = "housei_gravity_state"
GRAVITY_STATE_PLACED = "PLACED"
GRAVITY_STATE_PENDING = "PENDING"
GRAVITY_STATE_DONE = "DONE"
GRAVITY_STATES = frozenset((
    GRAVITY_STATE_PLACED,
    GRAVITY_STATE_PENDING,
    GRAVITY_STATE_DONE,
))
_PLACEMENT_TOLERANCE = 1.0e-6


class MeshLoadError(ValueError):
    """Validated JSON cannot be converted into the initial Blender mesh."""


class SewingError(ValueError):
    """Loaded pattern parts cannot be converted into an unambiguous sewn mesh."""


class UpdateError(ValueError):
    """A revised pattern cannot atomically replace the current panel meshes."""


def _matrix_tuple(matrix) -> tuple[float, ...]:
    return tuple(float(value) for row in matrix for value in row)


def part_moved_from_load(obj: bpy.types.Object) -> bool:
    """Return whether a part's Object Mode transform differs from its Load pose."""
    try:
        loaded = tuple(float(value) for value in obj[LOAD_MATRIX_KEY])
    except (KeyError, TypeError, ValueError):
        # No recorded Load pose: treat the part as moved so it stays eligible
        # rather than becoming unusable.
        return True
    current = _matrix_tuple(obj.matrix_world)
    return len(loaded) != 16 or any(
        abs(before - after) > _PLACEMENT_TOLERANCE
        for before, after in zip(loaded, current)
    )


def part_gravity_state(obj: bpy.types.Object) -> str:
    """Return a part's monotonic placement -> pending -> done state."""
    state = str(obj.get(GRAVITY_STATE_KEY, ""))
    if state in GRAVITY_STATES:
        return state

    # No recorded state: derive one.  A locked moved part has already completed
    # a simulation step; other moved parts are waiting for their first GRAVITY
    # click.
    if not part_moved_from_load(obj):
        state = GRAVITY_STATE_PLACED
    elif bool(obj.get(LOCKED_OBJECT_KEY, False)):
        state = GRAVITY_STATE_DONE
    else:
        state = GRAVITY_STATE_PENDING
    obj[GRAVITY_STATE_KEY] = state
    return state


def mark_moved_parts_pending(collection: bpy.types.Collection | None) -> tuple[bpy.types.Object, ...]:
    """Legacy no-op kept for import compatibility. Selection drives free/fixed now."""
    del collection
    return ()


def mark_pending_parts_done(parts: Iterable[bpy.types.Object]) -> None:
    """Legacy no-op kept for import compatibility."""
    del parts


def apply_auto_lock(collection: bpy.types.Collection | None, enabled: bool) -> None:
    """Legacy no-op; Existing Lock UI was removed (非選択固定 replaces it)."""
    del collection, enabled


def hou_parts_in_collection(collection: bpy.types.Collection | None) -> tuple[bpy.types.Object, ...]:
    """HOU-bearing mesh parts linked to the collection, stable panel order."""
    from .hou import is_hou_part

    if collection is None:
        return ()
    return tuple(sorted(
        (
            obj
            for obj in collection.objects
            if is_hou_part(obj)
        ),
        key=lambda obj: (
            int(obj.get("housei_panel_index", 0)),
            obj.name,
        ),
    ))


def apply_nonselected_fixed(
    collection: bpy.types.Collection,
    selected: Iterable[bpy.types.Object],
) -> tuple[tuple[bpy.types.Object, ...], tuple[bpy.types.Object, ...]]:
    """Mirror the old Existing Lock + DONE/PENDING split without states.

    Selected HOU parts in the work collection deform (unlocked). Non-selected
    HOU parts stay deformation-locked anchors — the same ``LOCKED_OBJECT_KEY``
    pin the ZOZO hand-off already used for Existing Lock / DONE parts.
    """
    selected_names = {obj.name for obj in selected}
    free: list[bpy.types.Object] = []
    fixed: list[bpy.types.Object] = []
    for obj in hou_parts_in_collection(collection):
        if obj.name in selected_names:
            obj[LOCKED_OBJECT_KEY] = False
            free.append(obj)
        else:
            obj[LOCKED_OBJECT_KEY] = True
            fixed.append(obj)
    return tuple(free), tuple(fixed)


def participating_parts(collection: bpy.types.Collection) -> tuple[bpy.types.Object, ...]:
    """All HOU parts in the work collection (selected free + non-selected fixed)."""
    return hou_parts_in_collection(collection)


@dataclass(frozen=True)
class EdgeMeta:
    sewing_group: str | None = None
    fold: bool = False
    ring: bool = False


@dataclass
class PanelGeometry:
    panel_id: str
    update_label: str | None
    instance_id: str
    mirror_side: str
    vertices: list[Vector]
    construction_vertices: list[Vector]
    pattern_vertices: list[Vector]
    edges: list[tuple[int, int]]
    faces: list[tuple[int, ...]]
    edge_meta: dict[tuple[int, int], EdgeMeta]
    edge_rest: dict[tuple[int, int], float]
    edge_family: dict[tuple[int, int], int]
    quads: list[tuple[int, int, int, int]]
    face_quads: dict[tuple[int, ...], int]
    ring_closed: bool
    spacing_m: float = MESH_SPACING_M
    # 0=N interior, 1=E stitch row (2=P belonged to the removed paving band).
    vertex_kinds: list[int] | None = None
    # Outer sewing-row edges whose rest length may absorb gather.
    shortenable_edges: set[tuple[int, int]] | None = None


def _point(value: object, field: str) -> Vector:
    if not isinstance(value, list) or len(value) != 2:
        raise MeshLoadError(f"{field} must be a two-number array.")
    try:
        result = Vector((float(value[0]), float(value[1])))
    except (TypeError, ValueError) as exc:
        raise MeshLoadError(f"{field} contains an invalid coordinate.") from exc
    if not all(math.isfinite(component) for component in result):
        raise MeshLoadError(f"{field} contains a non-finite coordinate.")
    return result


def part_spacing_m(obj: bpy.types.Object) -> float:
    """The pitch a part was actually cut at, which need not be this build's.

    Every path that writes a panel mesh records `housei_mesh_spacing_m`, so a
    part cut by an older build still answers 10 mm here long after
    `MESH_SPACING_M` became 5 mm. That is the point: a garment carried across a
    version keeps its own pitch until it is re-cut, and the seam-count pass and
    the ZOZO quality floor both have to judge it on the pitch it has.
    """
    try:
        spacing = float(obj.get("housei_mesh_spacing_m", MESH_SPACING_M))
    except (TypeError, ValueError):
        return MESH_SPACING_M
    return spacing if spacing > 0.0 and math.isfinite(spacing) else MESH_SPACING_M


def _part_cut_scheme(obj: bpy.types.Object) -> int:
    """The triangulation scheme a part was actually cut with.

    Parts from builds before the marker existed answer 1, the band-and-square
    scheme every such build cut, so they read as stale and get re-cut once.
    """
    try:
        return int(obj.get(CUT_SCHEME_KEY, 1))
    except (TypeError, ValueError):
        return 1


def _distance(a: Vector, b: Vector) -> float:
    return (a - b).length


def _cubic(start: Vector, control1: Vector, control2: Vector, end: Vector, t: float) -> Vector:
    inverse = 1.0 - t
    return (
        start * inverse**3
        + control1 * (3.0 * inverse**2 * t)
        + control2 * (3.0 * inverse * t**2)
        + end * t**3
    )


def _segment_points(segment: dict[str, Any], spacing: float, count: int | None = None) -> list[Vector]:
    start = _point(segment.get("start"), "segment.start")
    end = _point(segment.get("end"), "segment.end")
    kind = segment.get("type")
    if kind == "line":
        length = _distance(start, end)
        sample_count = count if count is not None else max(1, math.ceil(length / spacing))
        return [start.lerp(end, index / sample_count) for index in range(sample_count + 1)]
    if kind != "cubic":
        raise MeshLoadError(f"Unsupported JSON segment type: {kind!r}")
    control1 = _point(segment.get("control1"), "segment.control1")
    control2 = _point(segment.get("control2"), "segment.control2")

    def _measure(subdivisions: int) -> tuple[list[Vector], list[float]]:
        estimates = [
            _cubic(start, control1, control2, end, index / subdivisions)
            for index in range(subdivisions + 1)
        ]
        cumulative = [0.0]
        for a, b in zip(estimates, estimates[1:]):
            cumulative.append(cumulative[-1] + _distance(a, b))
        return estimates, cumulative

    fine = 128
    _estimates, cumulative = _measure(fine)
    length = cumulative[-1]
    if length <= 0.0:
        raise MeshLoadError("A panel contains a zero-length cubic segment.")
    sample_count = count if count is not None else max(1, math.ceil(length / spacing))
    if 4 * sample_count > fine:
        fine = 4 * sample_count
        _estimates, cumulative = _measure(fine)
        length = cumulative[-1]
    # Sample at equal arc length rather than equal parameter: a Bezier's speed
    # varies along the curve, so uniform-t points bunch where the control
    # points do. A stitch row sampled that way carries uneven cells and pairs
    # unevenly with its partner edge; equal arc keeps the row uniform.
    points = [start.copy()]
    position = 1
    for index in range(1, sample_count):
        target = length * index / sample_count
        while position < fine and cumulative[position] < target:
            position += 1
        span = cumulative[position] - cumulative[position - 1]
        factor = 0.0 if span <= 0.0 else (target - cumulative[position - 1]) / span
        points.append(_cubic(start, control1, control2, end, (position - 1 + factor) / fine))
    points.append(end.copy())
    return points


def _segment_meta(segment: dict[str, Any]) -> EdgeMeta:
    label = segment.get("sewing_group")
    if label is not None:
        if not isinstance(label, str) or len(label) != 1 or not label.isascii() or not label.isalpha():
            raise MeshLoadError(f"Invalid sewing group: {label!r}")
        label = label.upper()
    fold = bool(segment.get("fold", False))
    ring = bool(segment.get("ring", False))
    if ring and (label is not None or fold):
        raise MeshLoadError("A RING edge cannot also be a sewing or fold edge.")
    return EdgeMeta(label, fold, ring)


def _sample_segment(
    segment: dict[str, Any], spacing: float, count: int | None = None
) -> tuple[list[Vector], list[EdgeMeta]]:
    points = _segment_points(segment, spacing, count)
    if len(points) < 2 or any(_distance(a, b) <= 1.0e-10 for a, b in zip(points, points[1:])):
        raise MeshLoadError("A panel contains a zero-length sampled edge.")
    return points, [_segment_meta(segment)] * (len(points) - 1)


def _reflect(point: Vector, line_start: Vector, line_end: Vector) -> Vector:
    axis = line_end - line_start
    length_squared = axis.length_squared
    if length_squared <= 1.0e-16:
        raise MeshLoadError("A fold edge has zero length.")
    projection = line_start + axis * ((point - line_start).dot(axis) / length_squared)
    return projection * 2.0 - point


def _signed_area(points: list[Vector]) -> float:
    return 0.5 * sum(
        point.x * points[(index + 1) % len(points)].y - points[(index + 1) % len(points)].x * point.y
        for index, point in enumerate(points)
    )


def _reverse_loop(points: list[Vector], metadata: list[EdgeMeta]) -> tuple[list[Vector], list[EdgeMeta]]:
    count = len(points)
    reversed_points = list(reversed(points))
    reversed_meta = [metadata[(count - 2 - index) % count] for index in range(count)]
    return reversed_points, reversed_meta


def _segment_length(segment: dict[str, Any], spacing: float) -> float:
    points = _segment_points(segment, spacing)
    return sum(_distance(a, b) for a, b in zip(points, points[1:]))


def _distribute_edges(total: int, lengths: list[float]) -> list[int]:
    """Split ``total`` edges over segments in proportion to their arc lengths,
    at least one edge each, by largest remainder."""
    count = len(lengths)
    total = max(total, count)
    reference = sum(lengths)
    if reference <= 0.0:
        shares = [total // count] * count
        for index in range(total - sum(shares)):
            shares[index] += 1
        return shares
    raw = [total * value / reference for value in lengths]
    shares = [max(1, math.floor(value)) for value in raw]
    order = sorted(range(count), key=lambda index: raw[index] - shares[index], reverse=True)
    surplus = total - sum(shares)
    position = 0
    while surplus > 0:
        shares[order[position % count]] += 1
        surplus -= 1
        position += 1
    # Flooring with a one-edge minimum can overshoot when tiny segments were
    # bumped up; shave the largest shares back down.
    while surplus < 0:
        largest = max(range(count), key=lambda index: shares[index])
        if shares[largest] <= 1:
            break
        shares[largest] -= 1
        surplus += 1
    return shares


def _forced_segment_counts(
    segments: list[dict[str, Any]],
    spacing: float,
    seam_counts: dict[str, int] | None,
    fold_index: int | None,
) -> dict[int, int]:
    """Per-segment sample counts that give each sewing run its forced chain count.

    ``compute_seam_count_overrides`` speaks in mesh-chain edges: the number of
    edges the label's continuous path carries on the finished panel. A label is
    free to span several authored segments, and on a fold panel a run that ends
    at the fold continues into its own mirror image, so the mesh chain is twice
    the authored run. Applying the forced count to every segment separately --
    what this file used to do -- overshot by exactly those factors, the next
    override pass measured the overshoot as the new longer side and forced the
    partner up to match, and every pass doubled the seam: the reference
    garment's side seams (label B, two segments per chain) went 110 -> 220 ->
    440 edges on a boundary whose natural pitch is 110. The two sides never
    agreed, so the stitches paired by arc-walk ladder instead of 1:1, which is
    what a puckered, dirty seam line is.

    The count is distributed over the run's segments in proportion to their
    arc lengths (each at least one edge), and a fold-adjacent run takes half,
    rounding up, because its mirror contributes the other half of the chain.
    """
    if not seam_counts:
        return {}
    count = len(segments)
    metas = [_segment_meta(segment) for segment in segments]
    # Start the cyclic walk at a label change so a run that wraps the end of
    # the segment list is still seen as one run.
    start = 0
    for index in range(count):
        if metas[index].sewing_group != metas[(index - 1) % count].sewing_group:
            start = index
            break
    runs: list[list[int]] = []
    for offset in range(count):
        index = (start + offset) % count
        if runs and metas[index].sewing_group == metas[runs[-1][-1]].sewing_group:
            runs[-1].append(index)
        else:
            runs.append([index])
    forced: dict[int, int] = {}
    for run in runs:
        label = metas[run[0]].sewing_group
        if label is None or label not in seam_counts:
            continue
        edges = max(1, int(seam_counts[label]))
        if fold_index is not None and (
            fold_index == (run[0] - 1) % count or fold_index == (run[-1] + 1) % count
        ):
            edges = (edges + 1) // 2
        lengths = [_segment_length(segments[index], spacing) for index in run]
        for index, share in zip(run, _distribute_edges(edges, lengths)):
            forced[index] = share
    return forced


def _panel_outline(
    panel: dict[str, Any], spacing: float, seam_counts: dict[str, int] | None = None
) -> tuple[list[Vector], list[EdgeMeta], list[Vector]]:
    segments = panel.get("segments")
    if not isinstance(segments, list) or len(segments) < 3:
        raise MeshLoadError(f"Panel {panel.get('id')!r} needs at least three segments.")

    fold_indices = [index for index, segment in enumerate(segments) if bool(segment.get("fold", False))]
    ring_indices = [index for index, segment in enumerate(segments) if bool(segment.get("ring", False))]
    if len(fold_indices) > 1:
        raise MeshLoadError(f"Panel {panel.get('id')!r} has more than one fold segment.")
    if ring_indices and len(ring_indices) != 2:
        raise MeshLoadError(f"Panel {panel.get('id')!r} must have exactly two RING segments.")
    if ring_indices and fold_indices:
        raise MeshLoadError(f"Panel {panel.get('id')!r} cannot combine RING and @W in one panel.")

    # A sewing run is resampled to the matched chain count shared with its
    # partner edge so the two seam boundaries carry equal, uniformly spaced
    # vertices and pair 1:1 (the gather is absorbed as the longer edge bunches
    # between its matched vertices).
    forced_counts = _forced_segment_counts(
        segments, spacing, seam_counts, fold_indices[0] if fold_indices else None
    )

    if not fold_indices:
        ring_count = None
        if ring_indices:
            ring_count = max(
                len(_segment_points(segments[index], spacing)) - 1
                for index in ring_indices
            )
        points: list[Vector] = []
        metadata: list[EdgeMeta] = []
        for segment_index, segment in enumerate(segments):
            default_count = ring_count if segment_index in ring_indices else None
            sampled, sampled_meta = _sample_segment(
                segment, spacing, forced_counts.get(segment_index, default_count)
            )
            if not points:
                points.extend(sampled)
            else:
                if _distance(points[-1], sampled[0]) > 1.0e-8:
                    raise MeshLoadError(f"Panel {panel.get('id')!r} segments are not continuous.")
                points.extend(sampled[1:])
            metadata.extend(sampled_meta)
        if _distance(points[-1], points[0]) > 1.0e-8:
            raise MeshLoadError(f"Panel {panel.get('id')!r} is not closed.")
        points.pop()
        if len(points) != len(metadata):
            raise MeshLoadError("Internal boundary sampling error.")
        if _signed_area(points) < 0.0:
            points, metadata = _reverse_loop(points, metadata)
        return points, metadata, []

    fold_index = fold_indices[0]
    fold_segment = segments[fold_index]
    fold_points, _fold_metadata = _sample_segment(fold_segment, spacing)
    if fold_segment.get("type") != "line":
        raise MeshLoadError(f"Panel {panel.get('id')!r} fold segment must be straight in version 1.")
    fold_start, fold_end = fold_points[0], fold_points[-1]

    # Follow the authored non-fold boundary from the fold end back to its start.
    nonfold_points = [fold_end]
    nonfold_metadata: list[EdgeMeta] = []
    for offset in range(1, len(segments)):
        segment_index = (fold_index + offset) % len(segments)
        segment = segments[segment_index]
        sampled, sampled_meta = _sample_segment(segment, spacing, forced_counts.get(segment_index))
        if _distance(nonfold_points[-1], sampled[0]) > 1.0e-8:
            raise MeshLoadError(f"Panel {panel.get('id')!r} segments are not continuous.")
        nonfold_points.extend(sampled[1:])
        nonfold_metadata.extend(sampled_meta)
    if _distance(nonfold_points[-1], fold_start) > 1.0e-8:
        raise MeshLoadError(f"Panel {panel.get('id')!r} fold does not close the boundary.")

    reflected = [_reflect(point, fold_start, fold_end) for point in nonfold_points]
    mirrored_points = list(reversed(reflected))  # fold start -> fold end
    mirrored_metadata = [EdgeMeta(meta.sewing_group, False, False) for meta in reversed(nonfold_metadata)]

    # Close the original non-fold path with its mirrored counterpart. Endpoints
    # lie on the fold and are welded by using only one copy of each.
    points = nonfold_points + mirrored_points[1:-1]
    metadata = nonfold_metadata + mirrored_metadata
    if len(points) != len(metadata):
        raise MeshLoadError("Internal fold expansion error.")
    if _signed_area(points) < 0.0:
        points, metadata = _reverse_loop(points, metadata)
    return points, metadata, fold_points


def _point_segment_distance(point: Vector, start: Vector, end: Vector) -> float:
    delta = end - start
    if delta.length_squared <= 1.0e-20:
        return _distance(point, start)
    factor = max(0.0, min(1.0, (point - start).dot(delta) / delta.length_squared))
    return _distance(point, start + delta * factor)


def _point_in_polygon(point: Vector, polygon: list[Vector]) -> bool:
    inside = False
    previous = polygon[-1]
    for current in polygon:
        if (current.y > point.y) != (previous.y > point.y):
            crossing_x = (previous.x - current.x) * (point.y - current.y) / (previous.y - current.y) + current.x
            if point.x < crossing_x:
                inside = not inside
        previous = current
    return inside


def _interior_grid(
    polygon: list[Vector],
    spacing: float,
    *,
    exclude_segments: list[tuple[Vector, Vector]] | None = None,
) -> list[Vector]:
    """An equilateral triangular lattice covering the panel.

    A triangular (row-offset) lattice rather than a square one, for the same
    reason `ppf_remesh` uses it: its Delaunay triangulation is already
    equilateral, so every interior triangle comes out the same size and shape,
    and there is no diagonal direction for a drape to crease along. Nothing
    special happens near a sewing edge any more -- the seam neighbourhood is
    meshed exactly like the rest of the panel, which is what makes a sewn seam
    read as cloth rather than as a seam.

    Points are held half a pitch clear of the outline and of every interior
    constraint line (the fold row), so the boundary vertices -- which carry the
    seams and are kept exactly -- are never crowded into thin triangles against
    a lattice point. Rows are anchored to the pattern page rather than to the
    panel, so re-cutting a panel lands it on the lattice it was cut on.
    """
    min_x = min(point.x for point in polygon)
    max_x = max(point.x for point in polygon)
    min_y = min(point.y for point in polygon)
    max_y = max(point.y for point in polygon)
    clearance = spacing * 0.5
    row_height = spacing * math.sqrt(3.0) / 2.0
    result: list[Vector] = []
    first_row = math.ceil(min_y / row_height)
    last_row = math.floor(max_y / row_height)
    for row in range(first_row, last_row + 1):
        y = row * row_height
        offset = spacing * 0.5 if row % 2 else 0.0
        first_x = math.ceil((min_x - offset) / spacing)
        last_x = math.floor((max_x - offset) / spacing)
        for column in range(first_x, last_x + 1):
            point = Vector((offset + column * spacing, y))
            if not _point_in_polygon(point, polygon):
                continue
            if min(
                _point_segment_distance(point, polygon[index], polygon[(index + 1) % len(polygon)])
                for index in range(len(polygon))
            ) <= clearance:
                continue
            if exclude_segments and min(
                _point_segment_distance(point, start, end)
                for start, end in exclude_segments
            ) <= clearance:
                continue
            result.append(point)
    return result


def _edge_key(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def _face_key(face: Iterable[int]) -> tuple[int, ...]:
    return tuple(sorted(face))


def _grid_coordinate(value: float, spacing: float) -> int | None:
    coordinate = int(round(value / spacing))
    # mathutils.Vector stores float32 components, so page coordinates near a
    # metre need a sub-micron tolerance to round-trip a lattice index.
    if abs(value - coordinate * spacing) <= spacing * 1.0e-4:
        return coordinate
    return None


def _grainline_topology(
    pattern_vertices: list[Vector],
    edges: list[tuple[int, int]],
    faces: list[tuple[int, ...]],
    spacing: float,
) -> tuple[
    dict[tuple[int, int], int],
    list[tuple[int, int, int, int]],
    dict[tuple[int, ...], int],
]:
    """Separate square material cells from the triangulated proxy surface."""
    grid_vertices: dict[tuple[int, int], int] = {}
    ambiguous_grid_vertices: set[tuple[int, int]] = set()
    for vertex_index, point in enumerate(pattern_vertices):
        grid_x = _grid_coordinate(point.x, spacing)
        grid_y = _grid_coordinate(point.y, spacing)
        if grid_x is None or grid_y is None:
            continue
        key = (grid_x, grid_y)
        if key in ambiguous_grid_vertices:
            continue
        if key in grid_vertices:
            # A RING seam may weld coincident pattern coordinates.  Such a
            # boundary location is deliberately left in the transition strip.
            grid_vertices.pop(key)
            ambiguous_grid_vertices.add(key)
            continue
        grid_vertices[key] = vertex_index

    edge_keys = {_edge_key(*edge) for edge in edges}
    face_indices = {
        _face_key(face): index
        for index, face in enumerate(faces)
        if len(face) == 3
    }
    quads: list[tuple[int, int, int, int]] = []
    face_quads: dict[tuple[int, ...], int] = {}
    proxy_diagonals: set[tuple[int, int]] = set()
    for grid_x, grid_y in sorted(grid_vertices):
        corner_keys = (
            (grid_x, grid_y),
            (grid_x + 1, grid_y),
            (grid_x + 1, grid_y + 1),
            (grid_x, grid_y + 1),
        )
        if not all(key in grid_vertices for key in corner_keys):
            continue
        bottom_left, bottom_right, top_right, top_left = (
            grid_vertices[key] for key in corner_keys
        )
        sides = (
            _edge_key(bottom_left, bottom_right),
            _edge_key(bottom_right, top_right),
            _edge_key(top_right, top_left),
            _edge_key(top_left, bottom_left),
        )
        if not all(side in edge_keys for side in sides):
            continue

        first_diagonal = _edge_key(bottom_left, top_right)
        second_diagonal = _edge_key(bottom_right, top_left)
        if (first_diagonal in edge_keys) == (second_diagonal in edge_keys):
            continue
        if first_diagonal in edge_keys:
            triangle_keys = (
                _face_key((bottom_left, bottom_right, top_right)),
                _face_key((bottom_left, top_right, top_left)),
            )
            diagonal = first_diagonal
        else:
            triangle_keys = (
                _face_key((bottom_left, bottom_right, top_left)),
                _face_key((bottom_right, top_right, top_left)),
            )
            diagonal = second_diagonal
        if not all(key in face_indices for key in triangle_keys):
            continue
        if any(key in face_quads for key in triangle_keys):
            raise MeshLoadError("A proxy triangle was assigned to more than one grainline quad.")

        quad_index = len(quads)
        quads.append((bottom_left, bottom_right, top_right, top_left))
        for key in triangle_keys:
            face_quads[key] = quad_index
        proxy_diagonals.add(diagonal)

    tolerance = spacing * 1.0e-4
    edge_family: dict[tuple[int, int], int] = {}
    for edge in edges:
        key = _edge_key(*edge)
        if key in proxy_diagonals:
            family = GRAINLINE_EDGE_PROXY
        else:
            a, b = (pattern_vertices[index] for index in edge)
            delta = b - a
            if abs(delta.x) <= tolerance and abs(delta.y) > tolerance:
                family = GRAINLINE_EDGE_WARP
            elif abs(delta.y) <= tolerance and abs(delta.x) > tolerance:
                family = GRAINLINE_EDGE_WEFT
            else:
                family = GRAINLINE_EDGE_TRANSITION
        edge_family[key] = family
    return edge_family, quads, face_quads


def _pattern_tolerance(spacing: float) -> float:
    """How close two authored pattern points have to be to be the same point.

    Pattern coordinates run to about 1.5 m across the page and `mathutils.Vector`
    stores them as float32, whose step up there is 60-120 nm. A tolerance below
    that cannot recognise a point as itself, so one authored point becomes two
    vertices a tenth of a micron apart -- and the triangle between them has a
    rest area of 4e-12 m². A shell element's Hessian scales with 1/rest area, so
    that one triangle contributes terms around 1e11 and takes the first solve to
    NaN: the solver stops after frame 0 having written nothing.

    Tying the tolerance to the lattice pitch keeps it meaningful at any page
    scale, and the 1 um floor is what keeps it above the float32 step when the
    pitch is small. At the 5 mm pitch the floor is the live term -- a ten
    thousandth of the pitch would be 500 nm, inside the float32 step where two
    copies of a point cannot be told apart. 1 um is ten times that step and five
    thousand times finer than the mesh, so it can only ever merge points that
    were never distinct.

    `_grid_coordinate` rounds on the pitch-relative term alone, without this
    floor: 500 nm at 5 mm, four float32 steps rather than eight. It still tells
    a lattice point from noise, and a point it fails to recognise costs a
    grainline quad, not a solve.
    """
    return max(spacing * 1.0e-4, 1.0e-6)


def _lattice_minimum(spacing: float) -> float:
    """How close a generated point may come to one that is already there.

    Only points Housei generates are subject to this. The outline is authored,
    carries the seams, and is never moved or merged; the fold row and the
    interior lattice are this file's own construction, and a second point
    within a twentieth of the pitch adds nothing a solver can use.

    The lattice itself never gets near this limit -- it keeps half a pitch
    clear of the outline and the fold -- so in practice the merger guards the
    fold row, whose endpoints land on outline vertices and whose interior
    points can land on lattice sites. With the seam band gone the fold row is
    also the only interior constraint left, so `delaunay_2d_cdt` no longer has
    crossing constraints to make micro-vertices out of; the sub-pitch edges
    the band's spokes used to produce are gone with it.
    """
    return spacing * 0.05


def _nearest_vertex(points: list[Vector], target: Vector, tolerance: float) -> int:
    """Index of the closest point within ``tolerance``, or -1.

    Closest rather than first: at these tolerances two candidates mean the mesh
    already has a duplicate, and picking the nearer one cannot make it worse.
    """
    best_index = -1
    best_distance = tolerance
    for index, point in enumerate(points):
        distance = _distance(point, target)
        if distance <= best_distance:
            best_index = index
            best_distance = distance
    return best_index


def _find_vertex(points: list[Vector], target: Vector, tolerance: float) -> int:
    index = _nearest_vertex(points, target, tolerance)
    if index < 0:
        raise MeshLoadError("A fold endpoint was not found on the expanded boundary.")
    return index


class _VertexMerger:
    """Collect a panel's pattern vertices, reusing one that is already there.

    Every stage of the panel build produces points on its own and none of them
    look at what the others made: the outline is sampled from the authored
    segments, the seam band is offset inward from it, the fold line is sampled
    along its own axis, and the interior lattice is stamped on page multiples of
    the pitch. Two stages landing on the same place is not unusual -- measured on
    the reference pattern, a lattice point and a seam-band point coincided
    exactly on each fold panel -- and the triangle spanning the two copies has
    zero rest area. A shell element's Hessian scales with 1/rest area, so that
    single triangle is enough to take the first solve to NaN.

    Merging on the way in costs one bucket lookup and removes the whole class.
    Buckets are a tolerance wide, so the nine around a point cover everything
    within the tolerance and the pass stays linear in the vertex count.
    """

    def __init__(self, points: list[Vector], roles: list[int], tolerance: float) -> None:
        self.points = points
        self.roles = roles
        self.tolerance = tolerance
        self.buckets: dict[tuple[int, int], list[int]] = {}
        for index, point in enumerate(points):
            self.buckets.setdefault(self._cell(point), []).append(index)
        self.merged = 0

    def _cell(self, point: Vector) -> tuple[int, int]:
        return (
            int(math.floor(point.x / self.tolerance)),
            int(math.floor(point.y / self.tolerance)),
        )

    def find(self, point: Vector) -> int:
        cell_x, cell_y = self._cell(point)
        best_index = -1
        best_distance = self.tolerance
        for offset_x in (-1, 0, 1):
            for offset_y in (-1, 0, 1):
                for index in self.buckets.get((cell_x + offset_x, cell_y + offset_y), ()):
                    distance = _distance(self.points[index], point)
                    if distance <= best_distance:
                        best_index = index
                        best_distance = distance
        return best_index

    def add(self, point: Vector, role: int) -> int:
        existing = self.find(point)
        if existing >= 0:
            self.merged += 1
            return existing
        index = len(self.points)
        self.points.append(point.copy())
        self.roles.append(role)
        self.buckets.setdefault(self._cell(point), []).append(index)
        return index


def _marked_edge_chains(
    edges: list[tuple[int, int]], edge_meta: dict[tuple[int, int], EdgeMeta], marker: str
) -> list[list[int]]:
    marked = [edge for edge in edges if bool(getattr(edge_meta.get(_edge_key(*edge)), marker, False))]
    adjacency: dict[int, set[int]] = {}
    for a, b in marked:
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)
    if any(len(neighbors) > 2 for neighbors in adjacency.values()):
        raise MeshLoadError(f"{marker.upper()} edges form a branching path.")
    chains: list[list[int]] = []
    remaining = set(adjacency)
    while remaining:
        component: set[int] = set()
        pending = [next(iter(remaining))]
        while pending:
            vertex = pending.pop()
            if vertex in component:
                continue
            component.add(vertex)
            remaining.discard(vertex)
            pending.extend(adjacency[vertex] - component)
        endpoints = [vertex for vertex in component if len(adjacency[vertex]) == 1]
        if len(endpoints) != 2:
            raise MeshLoadError(f"{marker.upper()} edges must form open paths before welding.")
        ordered = [min(endpoints)]
        previous = None
        current = ordered[0]
        while current not in endpoints or len(ordered) == 1:
            following = adjacency[current] - ({previous} if previous is not None else set())
            if not following:
                break
            vertex = next(iter(following))
            ordered.append(vertex)
            previous, current = current, vertex
        chains.append(ordered)
    return chains


def _boundary_x_at_y(points: list[Vector], y: float) -> float:
    candidates: list[tuple[float, float]] = []
    for start, end in zip(points, points[1:]):
        delta = end.y - start.y
        if abs(delta) <= 1.0e-12:
            candidates.append((abs(y - start.y), (start.x + end.x) * 0.5))
            continue
        factor = (y - start.y) / delta
        clamped = max(0.0, min(1.0, factor))
        projected_y = start.y + delta * clamped
        candidates.append((abs(y - projected_y), start.x + (end.x - start.x) * clamped))
    if not candidates:
        raise MeshLoadError("A RING boundary has no usable edges.")
    return min(candidates, key=lambda item: item[0])[1]


def _ring_construction_vertices(
    pattern_vertices: list[Vector], ring_chains: list[list[int]], top: Vector
) -> list[Vector]:
    left_indices, right_indices = sorted(
        ring_chains, key=lambda chain: sum(pattern_vertices[index].x for index in chain) / len(chain)
    )
    direct = sum(abs(pattern_vertices[a].y - pattern_vertices[b].y) for a, b in zip(left_indices, right_indices))
    reverse = sum(
        abs(pattern_vertices[a].y - pattern_vertices[b].y)
        for a, b in zip(left_indices, reversed(right_indices))
    )
    if reverse < direct:
        right_indices = list(reversed(right_indices))
    left = [pattern_vertices[index] for index in left_indices]
    right = [pattern_vertices[index] for index in right_indices]
    widths = [(right_point - left_point).length for left_point, right_point in zip(left, right)]
    circumference = sum(widths) / len(widths)
    if circumference <= 1.0e-10:
        raise MeshLoadError("RING edges do not enclose a usable sleeve width.")
    # A sleeve is built as a C rather than a closed tube: its two RING edges
    # stop short of meeting, so the arm goes in through the gap and the seam
    # is left for the solver to close. Cloth does not stretch to allow that,
    # so the arc still has to be the pattern's full width -- open it by
    # widening the curve instead. With the gap subtending its own arc,
    # radius * (2*pi) - gap == circumference, so the cloth is unchanged and
    # only the ends are apart.
    opening = min(RING_OPENING_M, 0.5 * circumference)
    radius = (circumference + opening) / (2.0 * math.pi)
    sweep = 2.0 * math.pi - opening / radius
    axis_center = sum(point.y for point in pattern_vertices) / len(pattern_vertices)

    top_left = _boundary_x_at_y(left, top.y)
    top_right = _boundary_x_at_y(right, top.y)
    if top_right - top_left <= 1.0e-10:
        raise MeshLoadError("@TOP cannot define the sleeve's upward direction.")
    top_u = (top.x - top_left) / (top_right - top_left)

    result: list[Vector] = []
    for point in pattern_vertices:
        left_x = _boundary_x_at_y(left, point.y)
        right_x = _boundary_x_at_y(right, point.y)
        if right_x - left_x <= 1.0e-10:
            raise MeshLoadError("RING boundaries cross while constructing the sleeve tube.")
        u = (point.x - left_x) / (right_x - left_x)
        angle = sweep * (u - top_u)
        result.append(Vector((point.y - axis_center, radius * math.sin(angle), radius * math.cos(angle))))
    return result


def _seam_ring(
    panel_id: str,
    pattern_vertices: list[Vector],
    construction_vertices: list[Vector],
    edges: list[tuple[int, int]],
    faces: list[tuple[int, ...]],
    edge_meta: dict[tuple[int, int], EdgeMeta],
    ring_chains: list[list[int]],
    vertex_kinds: list[int] | None = None,
    shortenable_edges: set[tuple[int, int]] | None = None,
) -> tuple[
    list[Vector], list[Vector], list[tuple[int, int]], list[tuple[int, ...]],
    dict[tuple[int, int], EdgeMeta], dict[tuple[int, int], float],
    list[int], set[tuple[int, int]],
]:
    """Make a sleeve's two RING edges a seam instead of welding them shut.

    Welding closed the sleeve while the mesh was being built, which put the
    mesh builder in the business of sewing and left the panel a tube. A tube
    is not a simple region of its own pattern -- it is a cylinder, and its
    boundary is the two open ends rather than the outline the pattern was cut
    to -- so anything that works in pattern coordinates has no domain to work
    in, and welding two edges onto each other is exactly what leaves vertices
    sharing one pattern coordinate.

    Leaving the seam for the solver keeps every panel a plain flat cut with
    an outline, and puts the closing of it where the rest of the sewing
    already happens. The two chains carry matching vertex counts by the time
    they get here, which is what pairing them 1:1 needs.
    """
    if len(ring_chains) != 2 or len(ring_chains[0]) != len(ring_chains[1]):
        raise MeshLoadError("The two RING boundaries must produce matching vertex counts.")
    # Unique to the panel: two sleeves in one garment must not pair with
    # each other, and this seam never crosses to another piece of cloth.
    label = f"{RING_LABEL_PREFIX}{panel_id}"
    new_meta = dict(edge_meta)
    for chain in ring_chains:
        for a, b in zip(chain, chain[1:]):
            key = _edge_key(a, b)
            existing = new_meta.get(key, EdgeMeta())
            if existing.sewing_group and existing.sewing_group != label:
                raise MeshLoadError(
                    f"Panel {panel_id!r} marks a RING edge as sewing group "
                    f"{existing.sewing_group!r} as well."
                )
            new_meta[key] = EdgeMeta(label, existing.fold, True)
    rest = {
        _edge_key(a, b): (pattern_vertices[a] - pattern_vertices[b]).length
        for a, b in edges
        if a != b
    }
    kinds = (
        list(vertex_kinds)
        if vertex_kinds is not None
        else [VERTEX_KIND_NORMAL] * len(pattern_vertices)
    )
    return (
        pattern_vertices,
        construction_vertices,
        edges,
        faces,
        new_meta,
        rest,
        kinds,
        set(shortenable_edges or set()),
    )


def _triangulate_panel(
    panel: dict[str, Any], spacing: float, mirror_side: str = "", seam_counts: dict[str, int] | None = None
) -> PanelGeometry:
    panel_id = str(panel.get("id", "panel"))
    outline, outline_meta, fold_points = _panel_outline(panel, spacing, seam_counts)
    if len(outline) < 3 or abs(_signed_area(outline)) <= 1.0e-12:
        raise MeshLoadError(f"Panel {panel_id!r} has a degenerate expanded outline.")

    outline_count = len(outline)
    sewing_edge_flags = [bool(meta.sewing_group) for meta in outline_meta]
    sewing_vertex_flags = [
        sewing_edge_flags[(index - 1) % outline_count] or sewing_edge_flags[index]
        for index in range(outline_count)
    ]

    input_vertices = [point.copy() for point in outline]
    # Per input-vertex role before CDT (outline indices 0..outline_count-1).
    input_vertex_role = [
        VERTEX_KIND_EDGE if sewing_vertex_flags[index] else VERTEX_KIND_NORMAL
        for index in range(outline_count)
    ]
    input_edges = [(index, (index + 1) % outline_count) for index in range(outline_count)]
    input_meta = list(outline_meta)
    # Input edge index -> shortenable (outer sewing row only).
    input_shortenable = list(sewing_edge_flags)

    tolerance = _pattern_tolerance(spacing)
    merger = _VertexMerger(input_vertices, input_vertex_role, _lattice_minimum(spacing))

    if fold_points:
        # The fold line runs through the panel, so its interior points can land
        # on a vertex the boundary or the seam band already put there. Appending
        # them regardless is what produced the sub-micron needles: the same
        # authored point twice, a float32 step apart. Reuse instead.
        fold_indices = [_find_vertex(input_vertices, fold_points[0], tolerance)]
        for point in fold_points[1:-1]:
            fold_indices.append(merger.add(point, VERTEX_KIND_NORMAL))
        fold_indices.append(_find_vertex(input_vertices, fold_points[-1], tolerance))
        for start, end in zip(fold_indices, fold_indices[1:]):
            # Two fold points that resolved to one vertex leave nothing to draw.
            if start == end:
                continue
            input_edges.append((start, end))
            input_meta.append(EdgeMeta(None, True, False))
            input_shortenable.append(False)

    # The fold row is the only interior constraint, so it is the only thing
    # besides the outline the lattice has to stand clear of.
    fold_segments = (
        [(a, b) for a, b in zip(fold_points, fold_points[1:])] if fold_points else None
    )
    grid_points = _interior_grid(outline, spacing, exclude_segments=fold_segments)
    for point in grid_points:
        merger.add(point, VERTEX_KIND_NORMAL)

    try:
        output = delaunay_2d_cdt(
            input_vertices,
            input_edges,
            [list(range(outline_count))],
            1,
            # The CDT's own merge epsilon. It was 1e-9, which is below the
            # float32 step these coordinates are stored at, so it could not
            # recognise two copies of a point as one either -- the same mistake
            # as the tolerances above, in the library rather than in this file.
            tolerance,
            True,
        )
    except Exception as exc:
        raise MeshLoadError(f"Panel {panel_id!r} triangulation failed: {exc}") from exc
    vertices, edges, faces, orig_vertices, original_edges, _original_faces = output
    if any(len(face) != 3 for face in faces):
        raise MeshLoadError(f"Panel {panel_id!r} triangulation produced a non-triangular proxy face.")
    triangles = [tuple(face) for face in faces]
    if not triangles:
        raise MeshLoadError(f"Panel {panel_id!r} triangulation produced no faces.")

    def _as_index_list(value: object) -> list[int]:
        if value is None:
            return []
        if isinstance(value, int):
            return [value]
        try:
            return [int(item) for item in value]
        except TypeError:
            return []

    # Map CDT output vertices back to E / N (E wins on merges).
    vertex_kinds = [VERTEX_KIND_NORMAL] * len(vertices)
    for output_index, sources in enumerate(orig_vertices):
        for source in _as_index_list(sources):
            if (
                0 <= source < len(input_vertex_role)
                and input_vertex_role[source] == VERTEX_KIND_EDGE
            ):
                vertex_kinds[output_index] = VERTEX_KIND_EDGE
                break

    edge_meta: dict[tuple[int, int], EdgeMeta] = {}
    shortenable_edges: set[tuple[int, int]] = set()
    for edge, origins in zip(edges, original_edges):
        labels: set[str] = set()
        fold = False
        ring = False
        shortenable = False
        for origin in _as_index_list(origins):
            if 0 <= origin < len(input_meta):
                meta = input_meta[origin]
                if meta.sewing_group:
                    labels.add(meta.sewing_group)
                fold = fold or meta.fold
                ring = ring or meta.ring
                if origin < len(input_shortenable) and input_shortenable[origin]:
                    shortenable = True
        if len(labels) > 1:
            raise MeshLoadError(f"Panel {panel_id!r} triangulation merged conflicting sewing edges.")
        key = _edge_key(*edge)
        if labels or fold or ring:
            edge_meta[key] = EdgeMeta(next(iter(labels), None), fold, ring)
        if shortenable:
            shortenable_edges.add(key)

    update_label = panel.get("label")
    if update_label is not None and (not isinstance(update_label, str) or not update_label):
        raise MeshLoadError(f"Panel {panel_id!r} has an invalid update label.")
    pattern_vertices = list(vertices)
    result_edges = list(edges)
    result_faces = triangles
    ring_closed = any(meta.ring for meta in edge_meta.values())
    if ring_closed:
        top = _point(panel.get("top"), "panel.top")
        ring_chains = _marked_edge_chains(result_edges, edge_meta, "ring")
        if len(ring_chains) != 2:
            raise MeshLoadError(f"Panel {panel_id!r} must triangulate to two RING boundary paths.")
        construction_vertices = _ring_construction_vertices(pattern_vertices, ring_chains, top)
        (
            pattern_vertices,
            construction_vertices,
            result_edges,
            result_faces,
            edge_meta,
            edge_rest,
            vertex_kinds,
            shortenable_edges,
        ) = _seam_ring(
            # Mirrored sleeves are two instances of one cut and share a panel
            # id. Left and right must not become each other's seam, so the
            # label carries the side as well.
            f"{panel_id}:{mirror_side}" if mirror_side else panel_id,
            pattern_vertices,
            construction_vertices,
            result_edges,
            result_faces,
            edge_meta,
            ring_chains,
            vertex_kinds,
            shortenable_edges,
        )
        for face in result_faces:
            a, b, c = (construction_vertices[index] for index in face[:3])
            normal = (b - a).cross(c - a)
            center = (a + b + c) / 3.0
            radial = Vector((0.0, center.y, center.z))
            if normal.length_squared > 1.0e-16 and radial.length_squared > 1.0e-16:
                if normal.dot(radial) < 0.0:
                    result_faces = [tuple(reversed(item)) for item in result_faces]
                break
    else:
        construction_vertices = [Vector((point.x, 0.0, point.y)) for point in pattern_vertices]
        edge_rest = {
            _edge_key(a, b): (pattern_vertices[a] - pattern_vertices[b]).length
            for a, b in result_edges
        }

    edge_family, quads, face_quads = _grainline_topology(
        pattern_vertices,
        result_edges,
        result_faces,
        spacing,
    )

    mirrored = mirror_side == "RIGHT"
    if mirrored:
        center_x = (min(point.x for point in pattern_vertices) + max(point.x for point in pattern_vertices)) * 0.5
        pattern_vertices = [Vector((2.0 * center_x - point.x, point.y)) for point in pattern_vertices]
        construction_vertices = [Vector((-point.x, point.y, point.z)) for point in construction_vertices]
        quads = [(bottom_right, bottom_left, top_left, top_right) for bottom_left, bottom_right, top_right, top_left in quads]
        result_faces = [tuple(reversed(face)) for face in result_faces]

    if len(vertex_kinds) != len(pattern_vertices):
        vertex_kinds = [VERTEX_KIND_NORMAL] * len(pattern_vertices)

    base_instance = str(update_label or panel_id)
    instance_id = f"{base_instance}:{mirror_side}" if mirror_side else base_instance
    return PanelGeometry(
        panel_id=panel_id,
        update_label=update_label,
        instance_id=instance_id,
        mirror_side=mirror_side,
        vertices=[point.copy() for point in construction_vertices],
        construction_vertices=[point.copy() for point in construction_vertices],
        pattern_vertices=[point.copy() for point in pattern_vertices],
        edges=result_edges,
        faces=result_faces,
        edge_meta=edge_meta,
        edge_rest=edge_rest,
        edge_family=edge_family,
        quads=quads,
        face_quads=face_quads,
        ring_closed=ring_closed,
        spacing_m=spacing,
        vertex_kinds=list(vertex_kinds),
        shortenable_edges=set(shortenable_edges),
    )


def _panel_geometries(
    panels: list[dict[str, Any]],
    seam_counts_by_panel: dict[str, dict[str, int]] | None = None,
) -> list[PanelGeometry]:
    result: list[PanelGeometry] = []
    spacing = MESH_SPACING_M
    for panel in panels:
        seam_counts = (seam_counts_by_panel or {}).get(str(panel.get("id", "")))
        if bool(panel.get("mirror", False)):
            result.append(_triangulate_panel(panel, spacing, "LEFT", seam_counts))
            result.append(_triangulate_panel(panel, spacing, "RIGHT", seam_counts))
        else:
            result.append(_triangulate_panel(panel, spacing, seam_counts=seam_counts))
    return result


def _pack_panels(panels: list[PanelGeometry], gap: float) -> None:
    bounds = [
        (
            min(vertex.x for vertex in panel.vertices),
            max(vertex.x for vertex in panel.vertices),
            min(vertex.z for vertex in panel.vertices),
            (min(vertex.y for vertex in panel.vertices) + max(vertex.y for vertex in panel.vertices)) * 0.5,
        )
        for panel in panels
    ]
    total_width = sum(max_x - min_x for min_x, max_x, _min_z, _center_y in bounds) + gap * max(0, len(panels) - 1)
    cursor = -total_width / 2.0
    for panel, (min_x, max_x, min_z, center_y) in zip(panels, bounds):
        shift = Vector((cursor - min_x, WORLD_Y_M - center_y, BOTTOM_Z_M - min_z))
        for vertex in panel.vertices:
            vertex += shift
        cursor += max_x - min_x + gap


def _next_prefixed_name(prefix: str) -> str:
    index = 1
    while (
        f"{prefix}{index:03d}" in bpy.data.collections
        or f"{prefix}{index:03d}" in bpy.data.objects
    ):
        index += 1
    return f"{prefix}{index:03d}"


def _next_clothes_name() -> str:
    return _next_prefixed_name(COLLECTION_PREFIX)


def _next_cutting_name() -> str:
    return _next_prefixed_name(CUTTING_PREFIX)


def ensure_work_collection(
    context,
    collection: bpy.types.Collection | None,
) -> bpy.types.Collection:
    """Return a clothes work collection, creating CLOTHES_NNN when needed."""
    if collection is not None and collection.get("housei_role") == "clothes":
        return collection
    name = _next_clothes_name()
    created = bpy.data.collections.new(name)
    context.scene.collection.children.link(created)
    created["housei_schema"] = "housei-pattern/1.0.0"
    created["housei_role"] = "clothes"
    created["housei_sewing_verified"] = False
    return created


def _set_boolean_edge_attribute(mesh: bpy.types.Mesh, name: str, edge_indices: Iterable[int]) -> None:
    attribute = mesh.attributes.new(name=name, type="BOOLEAN", domain="EDGE")
    for index in edge_indices:
        attribute.data[index].value = True


def _write_panel_mesh_attributes(
    mesh: bpy.types.Mesh,
    panel: PanelGeometry,
    panel_index: int,
) -> None:
    mesh_edge_lookup = {_edge_key(*edge.vertices): edge.index for edge in mesh.edges}
    sewing_edges: dict[str, list[int]] = {}
    fold_edges: list[int] = []
    for key, meta in panel.edge_meta.items():
        edge_index = mesh_edge_lookup.get(key)
        if edge_index is None:
            raise MeshLoadError("A constrained metadata edge was lost while creating the Blender mesh.")
        if meta.sewing_group:
            sewing_edges.setdefault(meta.sewing_group, []).append(edge_index)
        if meta.fold:
            fold_edges.append(edge_index)
    for label, indices in sorted(sewing_edges.items()):
        _set_boolean_edge_attribute(mesh, f"sewing_{label}", indices)
    _set_boolean_edge_attribute(mesh, "fold", fold_edges)

    # Every mesh edge must get a rest length. Blender FLOAT attributes default
    # to 0; leaving holes makes GRAVITY report "zero-length material edge" on
    # non-proxy edges that were never written (e.g. edges only from faces after
    # validate/calc_edges).
    rest_attribute = mesh.attributes.new(name="housei_pattern_edge_rest", type="FLOAT", domain="EDGE")
    pattern_points = panel.pattern_vertices
    for edge in mesh.edges:
        key = _edge_key(*edge.vertices)
        if key in panel.edge_rest:
            value = float(panel.edge_rest[key])
        else:
            a, b = (int(v) for v in edge.vertices)
            if 0 <= a < len(pattern_points) and 0 <= b < len(pattern_points):
                value = float((pattern_points[a] - pattern_points[b]).length)
            else:
                value = 0.0
        rest_attribute.data[edge.index].value = value

    family_attribute = mesh.attributes.new(
        name=GRAINLINE_EDGE_FAMILY_ATTRIBUTE, type="INT", domain="EDGE"
    )
    for edge in mesh.edges:
        key = _edge_key(*edge.vertices)
        family_attribute.data[edge.index].value = panel.edge_family.get(
            key, GRAINLINE_EDGE_TRANSITION
        )

    panel_attribute = mesh.attributes.new(name="panel_index", type="INT", domain="FACE")
    quad_attribute = mesh.attributes.new(
        name=GRAINLINE_FACE_QUAD_ATTRIBUTE, type="INT", domain="FACE"
    )
    for polygon in mesh.polygons:
        panel_attribute.data[polygon.index].value = panel_index
        quad_attribute.data[polygon.index].value = panel.face_quads.get(
            _face_key(polygon.vertices), -1
        )

    pattern_attribute = mesh.attributes.new(name="housei_pattern_position", type="FLOAT_VECTOR", domain="POINT")
    for item, point in zip(pattern_attribute.data, panel.pattern_vertices):
        item.vector = (point.x, point.y, 0.0)

    construction_attribute = mesh.attributes.new(
        name="housei_construction_position", type="FLOAT_VECTOR", domain="POINT"
    )
    for item, point in zip(construction_attribute.data, panel.construction_vertices):
        item.vector = point

    kinds = panel.vertex_kinds
    if kinds is None or len(kinds) != len(mesh.vertices):
        kinds = [VERTEX_KIND_NORMAL] * len(mesh.vertices)
    kind_attribute = mesh.attributes.new(name=VERTEX_KIND_ATTRIBUTE, type="INT", domain="POINT")
    for item, kind in zip(kind_attribute.data, kinds):
        item.value = int(kind)

    shortenable = panel.shortenable_edges or set()
    shortenable_attribute = mesh.attributes.new(
        name=SHORTENABLE_EDGE_ATTRIBUTE, type="BOOLEAN", domain="EDGE"
    )
    for edge in mesh.edges:
        key = _edge_key(*edge.vertices)
        shortenable_attribute.data[edge.index].value = key in shortenable


def _sewing_signature(document: dict[str, Any]) -> str:
    groups = document.get("sewing_groups")
    if not isinstance(groups, dict):
        raise MeshLoadError("Housei JSON has no sewing_groups object.")
    normalized: dict[str, list[tuple[str, int]]] = {}
    for label, references in groups.items():
        if not isinstance(label, str) or not isinstance(references, list):
            raise MeshLoadError("Housei JSON has an invalid sewing group.")
        values: list[tuple[str, int]] = []
        for reference in references:
            if not isinstance(reference, dict):
                raise MeshLoadError("Housei JSON has an invalid sewing reference.")
            try:
                values.append((str(reference["panel"]), int(reference["segment"])))
            except (KeyError, TypeError, ValueError) as exc:
                raise MeshLoadError("Housei JSON has an invalid sewing reference.") from exc
        normalized[label.upper()] = sorted(values)
    panels = document.get("panels")
    if not isinstance(panels, list):
        raise MeshLoadError("Housei JSON has no panels array.")
    construction: list[dict[str, object]] = []
    for panel in panels:
        if not isinstance(panel, dict):
            raise MeshLoadError("Housei JSON contains an invalid panel.")
        segments = panel.get("segments")
        if not isinstance(segments, list):
            raise MeshLoadError("Housei JSON contains an invalid panel segment array.")
        construction.append({
            "id": str(panel.get("id", "")),
            "mirror": bool(panel.get("mirror", False)),
            "top": panel.get("top"),
            "ring": [index for index, segment in enumerate(segments) if bool(segment.get("ring", False))],
        })
    return json.dumps(
        {"groups": normalized, "construction": construction}, sort_keys=True, separators=(",", ":")
    )


def create_cuttingcloth_mesh(context, document: dict[str, Any]) -> bpy.types.Collection:
    """Load pattern panels into a new CUTTINGCLOTH_NNN data collection with HOU."""
    from .hou import sync_hou_from_object

    if document.get("schema") != "housei-pattern" or document.get("version") != "1.0.0":
        raise MeshLoadError("Unsupported Housei JSON schema.")
    if document.get("units") != "m":
        raise MeshLoadError("Housei mesh loading requires meter units.")
    source = document.get("source")
    panels_json = document.get("panels")
    if not isinstance(source, dict) or not isinstance(panels_json, list) or not panels_json:
        raise MeshLoadError("Housei JSON has no valid source or panels.")

    if not all(isinstance(panel, dict) for panel in panels_json):
        raise MeshLoadError("Housei JSON contains an invalid panel.")
    panels = _panel_geometries(panels_json)
    _pack_panels(panels, PANEL_GAP_M)

    name = _next_cutting_name()
    collection = bpy.data.collections.new(name)
    created_objects: list[bpy.types.Object] = []
    sewing_groups = document.get("sewing_groups") or {}
    try:
        context.scene.collection.children.link(collection)
        for panel_index, panel in enumerate(panels):
            object_name = f"{name}_PART_{panel_index + 1:03d}"
            mesh = bpy.data.meshes.new(object_name)
            obj = bpy.data.objects.new(object_name, mesh)
            collection.objects.link(obj)
            created_objects.append(obj)
            center = Vector((
                (min(vertex.x for vertex in panel.vertices) + max(vertex.x for vertex in panel.vertices)) / 2.0,
                (min(vertex.y for vertex in panel.vertices) + max(vertex.y for vertex in panel.vertices)) / 2.0,
                (min(vertex.z for vertex in panel.vertices) + max(vertex.z for vertex in panel.vertices)) / 2.0,
            ))
            vertices = [tuple(vertex - center) for vertex in panel.vertices]
            mesh.from_pydata(vertices, panel.edges, panel.faces)
            mesh.validate(verbose=False, clean_customdata=False)
            mesh.update(calc_edges=True, calc_edges_loose=True)
            _write_panel_mesh_attributes(mesh, panel, panel_index)
            obj.location = center

            obj["housei_schema"] = "housei-pattern/1.0.0"
            obj["housei_role"] = "part"
            obj["housei_collection"] = name
            obj["housei_source_svg"] = str(source.get("svg_path", ""))
            obj["housei_mesh_spacing_m"] = panel.spacing_m
            obj[CUT_SCHEME_KEY] = CUT_SCHEME
            obj["housei_panel_id"] = panel.panel_id
            obj["housei_panel_label"] = panel.update_label or ""
            obj["housei_panel_instance"] = panel.instance_id
            obj["housei_panel_index"] = panel_index
            obj["housei_mirror_side"] = panel.mirror_side
            obj["housei_ring_closed"] = panel.ring_closed
            obj[LOCKED_OBJECT_KEY] = False

        collection["housei_schema"] = "housei-pattern/1.0.0"
        collection["housei_role"] = "cutting"
        collection["housei_source_svg"] = str(source.get("svg_path", ""))
        collection["housei_sewing_signature"] = _sewing_signature(document)
        _store_document(collection, document)
        context.view_layer.update()
        for obj in created_objects:
            obj[LOAD_MATRIX_KEY] = list(_matrix_tuple(obj.matrix_world))
            sync_hou_from_object(
                obj,
                extra={
                    "source_collection_role": "cutting",
                    "sewing_groups": sewing_groups,
                },
            )

        for selected in context.selected_objects:
            selected.select_set(False)
        for obj in created_objects:
            obj.select_set(True)
        context.view_layer.objects.active = created_objects[0]
        return collection
    except Exception:
        for obj in created_objects:
            mesh = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)
        if collection.name in bpy.data.collections:
            bpy.data.collections.remove(collection)
        raise


def create_clothes_mesh(context, document: dict[str, Any]) -> bpy.types.Collection:
    """Compatibility alias: Load creates a cutting-cloth data collection."""
    return create_cuttingcloth_mesh(context, document)


def cut_out_parts_to_work(
    context,
    work: bpy.types.Collection,
    sources: Iterable[bpy.types.Object],
    *,
    z_offset_m: float = CUT_OUT_Z_OFFSET_M,
) -> tuple[bpy.types.Object, ...]:
    """Copy HOU parts into the work collection and lift them for placement.

    Source objects are left unchanged. Copies keep mesh data, custom props, and
    HOU; sewing on the work collection is marked dirty.
    """
    from .hou import is_hou_part, sync_hou_from_object

    if work.get("housei_role") != "clothes":
        raise MeshLoadError(msg("cut_need_clothes_role"))
    copies: list[bpy.types.Object] = []
    source_list = list(sources)
    for source in source_list:
        if not is_hou_part(source):
            continue
        # Avoid linking the same mesh datablock twice; each cut-out is independent.
        new_obj = source.copy()
        new_obj.data = source.data.copy()
        # Unlink from any collections Blender attached on copy, then own work only.
        for coll in list(new_obj.users_collection):
            coll.objects.unlink(new_obj)
        work.objects.link(new_obj)
        new_obj.location = new_obj.location.copy()
        new_obj.location.z += float(z_offset_m)
        new_obj["housei_role"] = "part"
        new_obj["housei_collection"] = work.name
        new_obj[LOCKED_OBJECT_KEY] = False
        sync_hou_from_object(
            new_obj,
            extra={
                "source_object": source.name,
                "work_collection": work.name,
            },
        )
        copies.append(new_obj)
    if not copies:
        return ()
    work["housei_sewing_verified"] = False
    # Prefer a stored pattern document so multi-panel sewing groups know which
    # partners are still missing (SODE/ERI etc.). First available source wins.
    if not _stored_document(work):
        for source in source_list:
            for coll in source.users_collection:
                document = _stored_document(coll)
                if document is not None:
                    _store_document(work, document)
                    work["housei_source_svg"] = coll.get(
                        "housei_source_svg", work.get("housei_source_svg", "")
                    )
                    break
            if _stored_document(work):
                break
    context.view_layer.update()
    for selected in list(context.selected_objects):
        selected.select_set(False)
    for obj in copies:
        obj.select_set(True)
    context.view_layer.objects.active = copies[0]
    return tuple(copies)


def _pattern_positions(obj: bpy.types.Object) -> list[Vector]:
    attribute = obj.data.attributes.get("housei_pattern_position")
    if attribute is None or attribute.domain != "POINT" or len(attribute.data) != len(obj.data.vertices):
        raise UpdateError(msg("remesh_pattern_missing", name=obj.name))
    return [Vector((item.vector[0], item.vector[1])) for item in attribute.data]


def _construction_positions(obj: bpy.types.Object) -> list[Vector]:
    attribute = obj.data.attributes.get("housei_construction_position")
    if attribute is None or attribute.domain != "POINT" or len(attribute.data) != len(obj.data.vertices):
        raise UpdateError(msg("remesh_construction_missing", name=obj.name))
    return [Vector(item.vector) for item in attribute.data]


def _transfer_deformation(obj: bpy.types.Object, panel: PanelGeometry) -> list[Vector]:
    old_flat = _pattern_positions(obj)
    old_faces = [tuple(polygon.vertices) for polygon in obj.data.polygons]
    if not old_faces:
        raise UpdateError(f"{obj.name} has no faces for deformation transfer.")
    old_world = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
    if panel.ring_closed:
        if not bool(obj.get("housei_ring_closed", False)):
            raise UpdateError(f"Panel #{panel.update_label} changed from flat to RING construction; load it again.")
        old_construction = _construction_positions(obj)
        bvh = BVHTree.FromPolygons(old_construction, old_faces, all_triangles=False)
        transferred: list[Vector] = []
        for point in panel.construction_vertices:
            location, _normal, face_index, _distance = bvh.find_nearest(point)
            if face_index is None or location is None:
                raise UpdateError(f"Panel #{panel.update_label} could not transfer its RING deformation.")
            face = old_faces[int(face_index)]
            if len(face) != 3:
                raise UpdateError(f"{obj.name} contains a non-triangular face.")
            a, b, c = face
            transferred.append(
                barycentric_transform(
                    location,
                    old_construction[a], old_construction[b], old_construction[c],
                    old_world[a], old_world[b], old_world[c],
                )
            )
        return transferred
    if bool(obj.get("housei_ring_closed", False)):
        raise UpdateError(f"Panel #{panel.update_label} removed its RING construction; load it again.")
    old_min = Vector((min(point.x for point in old_flat), min(point.y for point in old_flat)))
    old_max = Vector((max(point.x for point in old_flat), max(point.y for point in old_flat)))
    new_min = Vector((min(point.x for point in panel.pattern_vertices), min(point.y for point in panel.pattern_vertices)))
    new_max = Vector((max(point.x for point in panel.pattern_vertices), max(point.y for point in panel.pattern_vertices)))
    old_size = old_max - old_min
    new_size = new_max - new_min
    if min(old_size.x, old_size.y, new_size.x, new_size.y) <= 1.0e-10:
        raise UpdateError(f"Panel #{panel.update_label} has degenerate bounds.")

    flat3 = [Vector((point.x, point.y, 0.0)) for point in old_flat]
    bvh = BVHTree.FromPolygons(flat3, old_faces, all_triangles=False)
    transferred: list[Vector] = []
    for point in panel.pattern_vertices:
        normalized = Vector(((point.x - new_min.x) / new_size.x, (point.y - new_min.y) / new_size.y))
        old_point = Vector((old_min.x + normalized.x * old_size.x, old_min.y + normalized.y * old_size.y, 0.0))
        location, _normal, face_index, _distance = bvh.find_nearest(old_point)
        if face_index is None or location is None:
            raise UpdateError(f"Panel #{panel.update_label} could not transfer its deformation.")
        face = old_faces[int(face_index)]
        if len(face) != 3:
            raise UpdateError(f"{obj.name} contains a non-triangular face.")
        a, b, c = face
        transferred.append(
            barycentric_transform(location, flat3[a], flat3[b], flat3[c], old_world[a], old_world[b], old_world[c])
        )
    return transferred


def remove_sewn_preview(collection: bpy.types.Collection, reveal_parts: bool = False) -> None:
    """Remove transient Sewing meshes, optionally restoring their source parts."""
    for obj in list(collection.objects):
        if obj.get("housei_role") != "sewn":
            continue
        mesh = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    if reveal_parts:
        for obj in collection.objects:
            if obj.type == "MESH" and obj.get("housei_role") == "part":
                obj.hide_set(False)
                obj.hide_render = False


def update_clothes_mesh(context, collection: bpy.types.Collection, document: dict[str, Any]) -> tuple[bool, int]:
    """Recut all labeled panels, transfer their current pose, and atomically replace their meshes."""
    if collection is None or collection.get("housei_role") != "clothes":
        raise UpdateError("No loaded Housei clothes collection is selected.")
    source = document.get("source")
    panels_json = document.get("panels")
    if not isinstance(source, dict) or not isinstance(panels_json, list) or not panels_json:
        raise UpdateError("Updated Housei JSON has no valid source or panels.")
    old_source = str(collection.get("housei_source_svg", ""))
    new_source = str(source.get("svg_path", ""))
    if not old_source or not new_source or bpy.path.abspath(old_source) != bpy.path.abspath(new_source):
        raise UpdateError("Update must use the same PDF file as the selected Clothes collection.")

    parts = sorted(
        (obj for obj in collection.objects if obj.type == "MESH" and obj.get("housei_role") == "part"),
        key=lambda obj: int(obj.get("housei_panel_index", 0)),
    )
    if not all(isinstance(panel, dict) for panel in panels_json):
        raise UpdateError("Updated Housei JSON contains an invalid panel.")
    panels = _panel_geometries(panels_json)
    if len(parts) != len(panels):
        raise UpdateError(f"Panel object count changed: expected {len(parts)}, found {len(panels)}.")
    old_by_instance: dict[str, bpy.types.Object] = {}
    for obj in parts:
        label = str(obj.get("housei_panel_label", ""))
        if not label:
            raise UpdateError(f"{obj.name} has no # panel label. Load the labeled pattern again first.")
        instance_id = str(obj.get("housei_panel_instance", label))
        if instance_id in old_by_instance:
            raise UpdateError(f"Existing panel instance {instance_id!r} is duplicated.")
        old_by_instance[instance_id] = obj

    new_by_instance: dict[str, PanelGeometry] = {}
    for panel in panels:
        if not panel.update_label:
            raise UpdateError(f"Updated panel {panel.panel_id!r} has no # label.")
        if panel.instance_id in new_by_instance:
            raise UpdateError(f"Updated panel instance {panel.instance_id!r} is duplicated.")
        new_by_instance[panel.instance_id] = panel
    if set(old_by_instance) != set(new_by_instance):
        missing = sorted(set(old_by_instance) - set(new_by_instance))
        unexpected = sorted(set(new_by_instance) - set(old_by_instance))
        raise UpdateError(f"Panel labels changed or mirror instances changed; missing={missing}, unexpected={unexpected}.")

    prepared: list[tuple[bpy.types.Object, bpy.types.Mesh, PanelGeometry]] = []
    try:
        for instance_id, obj in old_by_instance.items():
            panel = new_by_instance[instance_id]
            world_positions = _transfer_deformation(obj, panel)
            inverse = obj.matrix_world.inverted_safe()
            local_positions = [inverse @ point for point in world_positions]
            mesh = bpy.data.meshes.new(f"{obj.name}_UPDATE")
            mesh.from_pydata(local_positions, panel.edges, panel.faces)
            mesh.validate(verbose=False, clean_customdata=False)
            mesh.update(calc_edges=True, calc_edges_loose=True)
            _write_panel_mesh_attributes(mesh, panel, int(obj.get("housei_panel_index", 0)))
            for material in obj.data.materials:
                mesh.materials.append(material)
            prepared.append((obj, mesh, panel))
    except Exception:
        for _obj, mesh, _panel in prepared:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        raise

    old_meshes: list[bpy.types.Mesh] = []
    for obj, mesh, panel in prepared:
        old_meshes.append(obj.data)
        obj.data = mesh
        obj["housei_source_svg"] = new_source
        obj["housei_mesh_spacing_m"] = panel.spacing_m
        obj[CUT_SCHEME_KEY] = CUT_SCHEME
        obj["housei_panel_id"] = panel.panel_id
        obj["housei_panel_label"] = panel.update_label
        obj["housei_panel_instance"] = panel.instance_id
        obj["housei_mirror_side"] = panel.mirror_side
        obj["housei_ring_closed"] = panel.ring_closed
        obj.hide_set(False)
        obj.hide_render = False
    remove_sewn_preview(collection)
    for mesh in old_meshes:
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)

    old_signature = str(collection.get("housei_sewing_signature", ""))
    new_signature = _sewing_signature(document)
    sewing_changed = old_signature != new_signature
    collection["housei_source_svg"] = new_source
    collection["housei_sewing_signature"] = new_signature
    if sewing_changed:
        collection["housei_sewing_verified"] = False
    _store_document(collection, document)
    context.view_layer.update()
    return sewing_changed, sum(len(obj.data.vertices) for obj in parts)


@dataclass(frozen=True)
class _SeamChain:
    obj: bpy.types.Object
    vertices: tuple[int, ...]
    world_points: tuple[Vector, ...]
    edge_lengths: tuple[float, ...]
    closed: bool


def _sewing_labels(mesh: bpy.types.Mesh) -> set[str]:
    # A pattern's own groups are single letters, but a RING seam names the part
    # it closes, so the label is whatever follows the prefix rather than one
    # character. Anything shorter drops the sleeve seam on the floor.
    labels: set[str] = set()
    for attribute in mesh.attributes:
        if not attribute.name.startswith("sewing_") or attribute.domain != "EDGE":
            continue
        label = attribute.name[len("sewing_"):]
        # A sewn mesh also carries sewing_spring_<label>; that is the seam's
        # springs, not another seam.
        if label and not label.startswith("spring_"):
            labels.add(label)
    return labels


def _self_closing_partners(obj: bpy.types.Object) -> dict[int, int]:
    """The vertex pairs a part's own RING seam brings together.

    A sleeve is cut as a C and sewn shut along its two RING edges, so its
    armhole and its cuff are each a ring the moment that seam exists: the two
    ends of either one are a single point of the finished garment. The cloth is
    still an open strip, so nothing reading the mesh alone can see it -- but the
    seam that closes it is not an assumption, it is a seam this very part
    carries. Only the ends of the RING edges decide it, so only they are
    matched.
    """
    partners: dict[int, int] = {}
    for label in sorted(_sewing_labels(obj.data)):
        if not label.startswith(RING_LABEL_PREFIX):
            continue
        chains = _raw_seam_chains(obj, label)
        if len(chains) != 2 or any(chain.closed for chain in chains):
            continue
        left, right = chains
        forward = _direction_cost(left, right, False)
        reverse = _direction_cost(left, right, True)
        if abs(forward - reverse) <= 1.0e-6:
            continue
        if reverse < forward:
            ends = ((left.vertices[0], right.vertices[-1]), (left.vertices[-1], right.vertices[0]))
        else:
            ends = ((left.vertices[0], right.vertices[0]), (left.vertices[-1], right.vertices[-1]))
        for a, b in ends:
            partners[a] = b
            partners[b] = a
    return partners


def _seam_chains(obj: bpy.types.Object, label: str) -> list[_SeamChain]:
    chains = _raw_seam_chains(obj, label)
    if label.startswith(RING_LABEL_PREFIX):
        return chains
    partners = _self_closing_partners(obj)
    if not partners:
        return chains
    return [_closed_by_own_seam(chain, partners) for chain in chains]


def _closed_by_own_seam(chain: _SeamChain, partners: dict[int, int]) -> _SeamChain:
    if chain.closed or partners.get(chain.vertices[0]) != chain.vertices[-1]:
        return chain
    # Closing costs no length: the two ends are one point once the RING seam is
    # sewn, the same virtual join a composite body loop already uses.
    return _SeamChain(
        chain.obj, chain.vertices, chain.world_points, chain.edge_lengths + (0.0,), True
    )


def _raw_seam_chains(obj: bpy.types.Object, label: str) -> list[_SeamChain]:
    mesh = obj.data
    attribute = mesh.attributes.get(f"sewing_{label}")
    if attribute is None or attribute.domain != "EDGE":
        return []
    marked_edges = [edge for edge in mesh.edges if bool(attribute.data[edge.index].value)]
    if not marked_edges:
        return []

    adjacency: dict[int, set[int]] = {}
    rest_attribute = mesh.attributes.get("housei_pattern_edge_rest")
    valid_rest = (
        rest_attribute is not None
        and rest_attribute.domain == "EDGE"
        and len(rest_attribute.data) == len(mesh.edges)
    )
    rest_by_edge: dict[tuple[int, int], float] = {}
    for edge in marked_edges:
        a, b = edge.vertices
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)
        rest_by_edge[_edge_key(a, b)] = (
            float(rest_attribute.data[edge.index].value)
            if valid_rest else (mesh.vertices[a].co - mesh.vertices[b].co).length
        )
    if any(len(neighbors) > 2 for neighbors in adjacency.values()):
        raise SewingError(msg("sew_branches", label=label, name=obj.name))

    chains: list[_SeamChain] = []
    remaining = set(adjacency)
    while remaining:
        component: set[int] = set()
        pending = [next(iter(remaining))]
        while pending:
            vertex = pending.pop()
            if vertex in component:
                continue
            component.add(vertex)
            remaining.discard(vertex)
            pending.extend(adjacency[vertex] - component)
        endpoints = sorted(vertex for vertex in component if len(adjacency[vertex]) == 1)
        closed = not endpoints
        if not closed and len(endpoints) != 2:
            raise SewingError(msg("sew_not_continuous", label=label, name=obj.name))
        if closed:
            if any(len(adjacency[vertex]) != 2 for vertex in component):
                raise SewingError(msg("sew_not_simple_closed", label=label, name=obj.name))
            start = min(component)
            ordered = [start]
            previous = None
            current = start
            while True:
                candidates = adjacency[current] - ({previous} if previous is not None else set())
                if previous is None:
                    following = min(candidates)
                else:
                    following = next(iter(candidates))
                if following == start:
                    break
                if following in ordered:
                    raise SewingError(msg("sew_cannot_order_closed", label=label, name=obj.name))
                ordered.append(following)
                previous, current = current, following
            if set(ordered) != component:
                raise SewingError(msg("sew_cannot_order_closed", label=label, name=obj.name))
        else:
            ordered = [endpoints[0]]
            previous = None
            current = endpoints[0]
            while current != endpoints[1]:
                candidates = adjacency[current] - ({previous} if previous is not None else set())
                if len(candidates) != 1:
                    raise SewingError(msg("sew_cannot_order", label=label, name=obj.name))
                following = next(iter(candidates))
                ordered.append(following)
                previous, current = current, following
        points = tuple(obj.matrix_world @ mesh.vertices[index].co for index in ordered)
        pairs = list(zip(ordered, ordered[1:]))
        if closed:
            pairs.append((ordered[-1], ordered[0]))
        edge_lengths = tuple(rest_by_edge[_edge_key(a, b)] for a, b in pairs)
        chains.append(_SeamChain(obj, tuple(ordered), points, edge_lengths, closed))
    return chains


def _direction_cost(left: _SeamChain, right: _SeamChain, reverse: bool) -> float:
    if left.closed or right.closed:
        raise SewingError(msg("sew_closed_need_circular"))
    right_start = right.world_points[-1] if reverse else right.world_points[0]
    right_end = right.world_points[0] if reverse else right.world_points[-1]
    return (left.world_points[0] - right_start).length + (left.world_points[-1] - right_end).length


def _pair_chains(left: list[_SeamChain], right: list[_SeamChain], label: str) -> list[tuple[_SeamChain, _SeamChain]]:
    if len(left) != len(right):
        raise SewingError(msg("sew_path_count_diff", label=label))
    if len(left) > 8:
        raise SewingError(msg("sew_too_many_paths", label=label))
    candidates: list[tuple[float, tuple[int, ...]]] = []
    for order in permutations(range(len(right))):
        cost = 0.0
        for left_chain, right_index in zip(left, order):
            right_chain = right[right_index]
            cost += min(_direction_cost(left_chain, right_chain, False), _direction_cost(left_chain, right_chain, True))
        candidates.append((cost, order))
    candidates.sort(key=lambda item: item[0])
    if len(candidates) > 1 and abs(candidates[1][0] - candidates[0][0]) <= 1.0e-6:
        raise SewingError(msg("sew_ambiguous_pair", label=label))
    return [(left_chain, right[right_index]) for left_chain, right_index in zip(left, candidates[0][1])]


def _cumulative_positions(edge_lengths: tuple[float, ...], vertex_count: int, closed: bool = False) -> list[float]:
    distances = [0.0]
    for length in edge_lengths[:vertex_count - 1]:
        distances.append(distances[-1] + length)
    total = sum(edge_lengths) if closed else distances[-1]
    if total <= 1.0e-10:
        raise SewingError(msg("sew_zero_length"))
    return [distance / total for distance in distances]


def _ordered_vertex_pairs(left: _SeamChain, right: _SeamChain, label: str) -> list[tuple[int, int]]:
    if left.closed or right.closed:
        raise SewingError(msg("sew_mixed_open_closed", label=label))
    forward_cost = _direction_cost(left, right, False)
    reverse_cost = _direction_cost(left, right, True)
    if abs(forward_cost - reverse_cost) <= 1.0e-6:
        raise SewingError(msg("sew_direction_ambiguous", label=label))
    right_vertices = list(right.vertices)
    right_points = right.world_points
    right_lengths = right.edge_lengths
    if reverse_cost < forward_cost:
        right_vertices.reverse()
        right_points = tuple(reversed(right_points))
        right_lengths = tuple(reversed(right_lengths))

    if len(left.vertices) == len(right_vertices):
        # Matched counts pair 1:1 by index once oriented, so the longer edge
        # gathers between its matched vertices instead of splaying into a ladder.
        return list(zip(left.vertices, right_vertices))

    left_positions = _cumulative_positions(left.edge_lengths, len(left.vertices))
    right_positions = _cumulative_positions(right_lengths, len(right_vertices))
    pairs = [(left.vertices[0], right_vertices[0])]
    left_index = right_index = 0
    while left_index < len(left.vertices) - 1 or right_index < len(right_vertices) - 1:
        next_left = left_positions[left_index + 1] if left_index + 1 < len(left_positions) else math.inf
        next_right = right_positions[right_index + 1] if right_index + 1 < len(right_positions) else math.inf
        if abs(next_left - next_right) <= 1.0e-9:
            left_index += 1
            right_index += 1
        elif next_left < next_right:
            left_index += 1
        else:
            right_index += 1
        pair = (left.vertices[left_index], right_vertices[right_index])
        if pair != pairs[-1]:
            pairs.append(pair)
    return pairs


@dataclass(frozen=True)
class _GlobalSeamPath:
    vertices: tuple[int, ...]
    world_points: tuple[Vector, ...]
    edge_lengths: tuple[float, ...]


def _closure_cost(first: _SeamChain, second: _SeamChain, reverse_second: bool) -> float:
    if first.closed or second.closed:
        raise SewingError(msg("sew_open_only_composite"))
    second_start = second.world_points[-1] if reverse_second else second.world_points[0]
    second_end = second.world_points[0] if reverse_second else second.world_points[-1]
    return (
        (first.world_points[-1] - second_start).length
        + (second_end - first.world_points[0]).length
    )


def _global_chain(chain: _SeamChain, offset: int) -> _GlobalSeamPath:
    return _GlobalSeamPath(
        tuple(offset + vertex for vertex in chain.vertices),
        chain.world_points,
        chain.edge_lengths,
    )


def _composite_loop(
    first: _SeamChain, second: _SeamChain, offsets: dict[bpy.types.Object, int]
) -> _GlobalSeamPath:
    reverse_second = _closure_cost(first, second, True) < _closure_cost(first, second, False)
    second_vertices = list(second.vertices)
    second_points = list(second.world_points)
    second_lengths = list(second.edge_lengths)
    if reverse_second:
        second_vertices.reverse()
        second_points.reverse()
        second_lengths.reverse()
    vertices = tuple(
        [offsets[first.obj] + vertex for vertex in first.vertices]
        + [offsets[second.obj] + vertex for vertex in second_vertices]
    )
    points = first.world_points + tuple(second_points)
    # The two zero-length entries are virtual joins at the already sewn body
    # endpoints. They close the parameter loop without adding pattern length.
    lengths = first.edge_lengths + (0.0,) + tuple(second_lengths) + (0.0,)
    return _GlobalSeamPath(vertices, points, lengths)


def _reorder_closed_path(path: _GlobalSeamPath, order: list[int]) -> _GlobalSeamPath:
    edge_lookup = {
        _edge_key(path.vertices[index], path.vertices[(index + 1) % len(path.vertices)]): path.edge_lengths[index]
        for index in range(len(path.vertices))
    }
    vertices = tuple(path.vertices[index] for index in order)
    points = tuple(path.world_points[index] for index in order)
    lengths = tuple(
        edge_lookup[_edge_key(vertices[index], vertices[(index + 1) % len(vertices)])]
        for index in range(len(vertices))
    )
    return _GlobalSeamPath(vertices, points, lengths)


def _normalized_closed_pairs(left: _GlobalSeamPath, right: _GlobalSeamPath) -> list[tuple[int, int]]:
    if len(left.vertices) == len(right.vertices):
        # Matched counts pair 1:1 by index; _circular_alignment rotates and
        # reflects ``right`` first, so the chosen ordering is the best offset and
        # this avoids the arc-length merge-walk splaying equal loops into a ladder.
        return [(left.vertices[index], right.vertices[index]) for index in range(len(left.vertices))]
    left_positions = _cumulative_positions(left.edge_lengths, len(left.vertices), True)
    right_positions = _cumulative_positions(right.edge_lengths, len(right.vertices), True)
    pairs = [(left.vertices[0], right.vertices[0])]
    left_index = right_index = 0
    while left_index < len(left.vertices) - 1 or right_index < len(right.vertices) - 1:
        next_left = left_positions[left_index + 1] if left_index + 1 < len(left_positions) else math.inf
        next_right = right_positions[right_index + 1] if right_index + 1 < len(right_positions) else math.inf
        if abs(next_left - next_right) <= 1.0e-9:
            left_index += 1
            right_index += 1
        elif next_left < next_right:
            left_index += 1
        else:
            right_index += 1
        pair = (left.vertices[left_index], right.vertices[right_index])
        if pair != pairs[-1]:
            pairs.append(pair)
    return pairs


def _circular_alignment(
    left: _GlobalSeamPath, right: _GlobalSeamPath
) -> tuple[float, list[tuple[int, int]]]:
    best: tuple[float, list[tuple[int, int]]] | None = None
    count = len(right.vertices)
    for reverse in (False, True):
        base = list(range(count)) if not reverse else list(reversed(range(count)))
        for rotation in range(count):
            order = base[rotation:] + base[:rotation]
            candidate = _reorder_closed_path(right, order)
            pairs = _normalized_closed_pairs(left, candidate)
            left_points = {vertex: point for vertex, point in zip(left.vertices, left.world_points)}
            right_points = {vertex: point for vertex, point in zip(candidate.vertices, candidate.world_points)}
            cost = sum((left_points[a] - right_points[b]).length for a, b in pairs) / len(pairs)
            if best is None or cost < best[0]:
                best = (cost, pairs)
    if best is None:
        raise SewingError(msg("sew_cannot_align_closed"))
    return best


def _multipart_closed_pairs(
    by_object: dict[bpy.types.Object, list[_SeamChain]],
    offsets: dict[bpy.types.Object, int],
    label: str,
) -> list[tuple[int, int]]:
    closed = [chain for chains in by_object.values() for chain in chains if chain.closed]
    open_by_object = {
        obj: [chain for chain in chains if not chain.closed]
        for obj, chains in by_object.items()
        if any(not chain.closed for chain in chains)
    }
    if not closed or len(open_by_object) != 2:
        raise SewingError(msg("sew_ring_need_body", label=label))
    count = len(closed)
    first_obj, second_obj = sorted(open_by_object, key=lambda obj: int(obj.get("housei_panel_index", 0)))
    first = open_by_object[first_obj]
    second = open_by_object[second_obj]
    if count == 1 and first and second:
        closed_loop = _global_chain(closed[0], offsets[closed[0].obj])
        partial_assignments: list[tuple[float, list[tuple[int, int]]]] = []
        for first_chain in first:
            for second_chain in second:
                body_loop = _composite_loop(first_chain, second_chain, offsets)
                alignment_cost, pairs = _circular_alignment(body_loop, closed_loop)
                join_cost = min(
                    _closure_cost(first_chain, second_chain, False),
                    _closure_cost(first_chain, second_chain, True),
                )
                partial_assignments.append((alignment_cost + join_cost, pairs))
        partial_assignments.sort(key=lambda item: item[0])
        return partial_assignments[0][1]
    if len(first) != count or len(second) != count or count > 8:
        raise SewingError(msg("sew_ring_cannot_pair", label=label, count=count))

    body_candidates: list[tuple[float, tuple[int, ...]]] = []
    for order in permutations(range(count)):
        cost = sum(
            min(_closure_cost(left, second[index], False), _closure_cost(left, second[index], True))
            for left, index in zip(first, order)
        )
        body_candidates.append((cost, order))
    body_candidates.sort(key=lambda item: item[0])
    body_order = body_candidates[0][1]
    body_loops = [
        _composite_loop(left, second[index], offsets)
        for left, index in zip(first, body_order)
    ]
    closed_loops = [_global_chain(chain, offsets[chain.obj]) for chain in closed]

    assignments: list[tuple[float, tuple[int, ...], list[list[tuple[int, int]]]]] = []
    for order in permutations(range(count)):
        total = 0.0
        pair_sets: list[list[tuple[int, int]]] = []
        for body_loop, closed_index in zip(body_loops, order):
            cost, pairs = _circular_alignment(body_loop, closed_loops[closed_index])
            total += cost
            pair_sets.append(pairs)
        assignments.append((total, order, pair_sets))
    assignments.sort(key=lambda item: item[0])
    return [pair for pair_set in assignments[0][2] for pair in pair_set]


@dataclass(frozen=True)
class SewingPlan:
    parts: tuple[bpy.types.Object, ...]
    labels: tuple[str, ...]
    connections: tuple[tuple[str, int, int], ...]


def _part_panel_id(obj: bpy.types.Object) -> str:
    return str(obj.get("housei_panel_id", obj.name))


def _present_panel_ids(parts: Iterable[bpy.types.Object]) -> set[str]:
    return {_part_panel_id(obj) for obj in parts}


def _authored_sewing_panel_ids(
    collection: bpy.types.Collection, label: str
) -> set[str] | None:
    """Panel ids the pattern JSON lists for this sewing letter, or None if unknown.

    Used so partial 裁断 (e.g. OMOTE+URA without SODE/ERI) does not invent
    body-to-body seams for labels that still wait on missing partners.
    """
    document = _stored_document(collection)
    if document is None:
        return None
    groups = document.get("sewing_groups")
    if not isinstance(groups, dict):
        return None
    refs = groups.get(label)
    if not isinstance(refs, list) or not refs:
        return None
    panels: set[str] = set()
    for ref in refs:
        if isinstance(ref, dict) and ref.get("panel") is not None:
            panels.add(str(ref["panel"]))
    return panels or None


def _sewing_label_partners_ready(
    collection: bpy.types.Collection,
    label: str,
    present_panels: set[str],
) -> bool:
    """False when the authored group still needs panels not in the work set."""
    required = _authored_sewing_panel_ids(collection, label)
    if required is None:
        return True
    return required.issubset(present_panels)


def build_sewing_plan(collection: bpy.types.Collection) -> SewingPlan:
    """Validate and return reusable global-index sewing connections for separate parts."""
    if collection is None or collection.get("housei_role") != "clothes":
        raise SewingError(msg("sew_need_clothes"))
    parts = participating_parts(collection)
    if len(parts) < 2:
        raise SewingError(msg("sew_need_two_parts"))
    all_parts = tuple(
        obj
        for obj in collection.objects
        if obj.type == "MESH" and obj.get("housei_role") == "part"
    )
    active = set(parts)
    present_panels = _present_panel_ids(parts)
    labels_by_part = {obj: _sewing_labels(obj.data) for obj in all_parts}
    active_labels = tuple(sorted(set().union(*(labels_by_part[obj] for obj in parts))))

    offsets: dict[bpy.types.Object, int] = {}
    offset = 0
    for obj in parts:
        offsets[obj] = offset
        offset += len(obj.data.vertices)
    connections: list[tuple[str, int, int]] = []
    spring_keys: set[tuple[int, int]] = set()
    resolved_labels: list[str] = []
    for label in active_labels:
        # Incomplete multi-panel groups (sleeve/collar not yet 裁断): skip.
        # Otherwise C/D would sew OMOTE–URA armhole/neck to each other and
        # collide with side-seam springs A/B at shared endpoints.
        if not _sewing_label_partners_ready(collection, label, present_panels):
            continue
        by_object = {obj: chains for obj in parts if (chains := _seam_chains(obj, label))}
        inactive = [
            obj for obj in all_parts
            if obj not in active and label in labels_by_part[obj]
        ]
        active_has_closed = any(chain.closed for chains in by_object.values() for chain in chains)
        inactive_has_closed = any(
            chain.closed
            for obj in inactive
            for chain in _seam_chains(obj, label)
        )
        if inactive_has_closed and not active_has_closed:
            continue
        try:
            if active_has_closed:
                pairs = _multipart_closed_pairs(by_object, offsets, label)
            elif len(by_object) == 1 and len(next(iter(by_object.values()))) == 2:
                # A seam that closes one panel onto itself: the two RING edges
                # of a sleeve, which meet to put the cloth round an arm. It is
                # a seam like any other -- two chains of matching vertices that
                # have to be brought together -- and the only thing unusual is
                # that both belong to the same piece of cloth.
                only_object, (left_chain, right_chain) = next(iter(by_object.items()))
                offset = offsets[only_object]
                pairs = [
                    (offset + left_vertex, offset + right_vertex)
                    for left_vertex, right_vertex in _ordered_vertex_pairs(
                        left_chain, right_chain, label
                    )
                ]
            else:
                if len(by_object) != 2:
                    raise SewingError(msg("sew_group_two_parts", label=label))
                first_obj, second_obj = sorted(
                    by_object, key=lambda obj: int(obj.get("housei_panel_index", 0))
                )
                pairs = [
                    (offsets[first_obj] + left_vertex, offsets[second_obj] + right_vertex)
                    for left_chain, right_chain in _pair_chains(
                        by_object[first_obj], by_object[second_obj], label
                    )
                    for left_vertex, right_vertex in _ordered_vertex_pairs(
                        left_chain, right_chain, label
                    )
                ]
        except SewingError:
            if inactive:
                continue
            raise
        resolved_labels.append(label)
        for a, b in pairs:
            key = _edge_key(a, b)
            if key in spring_keys:
                # Adjacent labels share junction vertices; one spring is enough.
                continue
            spring_keys.add(key)
            connections.append((label, a, b))
    if not resolved_labels:
        raise SewingError(msg("sew_no_group_yet"))
    return SewingPlan(parts, tuple(resolved_labels), tuple(connections))


def compute_seam_count_overrides(
    collection: bpy.types.Collection,
) -> dict[str, dict[str, int]]:
    """Return per-panel ``{label: forced edge count}`` that equalize each sewing
    seam's two sides so their boundaries carry matching, uniformly spaced
    vertices and pair 1:1 (the longer edge then gathers between its matched
    vertices).

    Same-resolution seams keep the longer side and resample the shorter side up
    so gather still works. Mixed 10 mm / 5 mm seams instead take the coarser
    (10 mm) side's count: the fine boundary is sparsified to match, which is
    equivalent to pairing every other fine vertex and avoids densifying the
    large panel. For an armhole the sleeve is a closed ring while the body
    armhole is the composite of the front and back open chains, so the ring's
    vertex budget is shared across the body panels in proportion to their arc
    lengths. Panels whose sides already match are omitted (idempotent).
    """
    parts = tuple(
        obj for obj in collection.objects
        if obj.type == "MESH" and obj.get("housei_role") == "part"
    )
    if len(parts) < 2:
        return {}
    labels: set[str] = set()
    for obj in parts:
        labels |= _sewing_labels(obj.data)

    overrides: dict[str, dict[str, int]] = {}

    def _panel_id(obj: bpy.types.Object) -> str:
        return str(obj.get("housei_panel_id", obj.name))

    def _add(pid: str, label: str, edges: int) -> None:
        if edges >= 1:
            overrides.setdefault(pid, {})[label] = edges

    def _rep_length(chains: list[_SeamChain]) -> float:
        # Symmetric mirror/fold instances share a length; average them.
        return sum(sum(chain.edge_lengths) for chain in chains) / len(chains)

    def _rep_verts(chains: list[_SeamChain]) -> int:
        return max(len(chain.vertices) for chain in chains)

    fold_vertex_sets: dict[str, set[int]] = {}

    def _fold_vertices(obj: bpy.types.Object) -> set[int]:
        cached = fold_vertex_sets.get(obj.name)
        if cached is None:
            cached = set()
            attribute = obj.data.attributes.get("fold")
            if attribute is not None and attribute.domain == "EDGE":
                for edge in obj.data.edges:
                    if bool(attribute.data[edge.index].value):
                        cached.add(int(edge.vertices[0]))
                        cached.add(int(edge.vertices[1]))
            fold_vertex_sets[obj.name] = cached
        return cached

    def _fold_merged(obj: bpy.types.Object, chains: list[_SeamChain]) -> bool:
        # A chain that passes through a fold vertex is the weld of an authored
        # run and its mirror image, so its edge count is even by construction.
        # Asking such a chain for an odd count would leave it one edge off
        # forever, and the override would never clear.
        folds = _fold_vertices(obj)
        if not folds:
            return False
        return any(vertex in folds for chain in chains for vertex in chain.vertices)

    present_panels = _present_panel_ids(parts)
    for label in sorted(labels):
        # Same gate as build_sewing_plan: do not recut armholes/necks against
        # each other when the sleeve/collar panel is not in the work collection.
        if not _sewing_label_partners_ready(collection, label, present_panels):
            continue
        by_obj: dict[bpy.types.Object, list[_SeamChain]] = {}
        for obj in parts:
            chains = _seam_chains(obj, label)
            if chains:
                by_obj[obj] = chains
        if len(by_obj) < 2:
            continue
        closed_objs = {

            obj: chains for obj, chains in by_obj.items()
            if any(chain.closed for chain in chains)
        }
        open_objs = {
            obj: [chain for chain in chains if not chain.closed]
            for obj, chains in by_obj.items()
            if any(not chain.closed for chain in chains)
        }

        if closed_objs:
            # Ring-composite armhole: closed sleeve ring <-> composite body loop.
            # Prefer the coarser closed path when a fine mesh is mixed in.
            closed_spacings = {part_spacing_m(obj) for obj in closed_objs}
            if max(closed_spacings) - min(closed_spacings) > 1.0e-12:
                coarse = max(closed_objs, key=part_spacing_m)
                target = _rep_verts(closed_objs[coarse])
            else:
                target = max(_rep_verts(chains) for chains in closed_objs.values())
            if not open_objs:
                continue
            current = sum(_rep_verts(chains) for chains in open_objs.values())
            if current == target:
                continue
            lengths = {obj: _rep_length(chains) for obj, chains in open_objs.items()}
            total = sum(lengths.values())
            if total <= 0.0:
                continue
            count = len(open_objs)
            budget = target - count  # composite verts = sum(edges_i + 1) = target
            if budget < count:
                continue
            raw = {obj: budget * lengths[obj] / total for obj in open_objs}
            edges = {obj: max(1, int(math.floor(value))) for obj, value in raw.items()}
            remainder = budget - sum(edges.values())
            order = sorted(open_objs, key=lambda obj: raw[obj] - edges[obj], reverse=True)
            for index in range(max(0, remainder)):
                edges[order[index % count]] += 1
            for obj in open_objs:
                desired = edges[obj]
                if _fold_merged(obj, open_objs[obj]):
                    desired = max(2, 2 * (desired // 2))
                actual = _rep_verts(open_objs[obj]) - 1
                if actual != desired:
                    _add(_panel_id(obj), label, desired)
        else:
            # Direct open <-> open seam on exactly two panels.
            if len(open_objs) != 2:
                continue
            verts = {obj: _rep_verts(chains) for obj, chains in open_objs.items()}
            spacings = {obj: part_spacing_m(obj) for obj in open_objs}
            spacing_values = list(spacings.values())
            if abs(spacing_values[0] - spacing_values[1]) > 1.0e-12:
                # Mixed resolution: keep the coarser side; sparsify/densify the
                # other so both boundaries share that count (1-skip equivalent).
                coarse = max(open_objs, key=part_spacing_m)
                target = verts[coarse]
            else:
                # Same pitch: longer side wins so gather still bunches fabric.
                target = max(verts.values())
            for obj, value in verts.items():
                desired = max(1, target - 1)
                if _fold_merged(obj, open_objs[obj]):
                    desired = max(2, 2 * (desired // 2))
                if value - 1 != desired:
                    _add(_panel_id(obj), label, desired)
    return overrides


_DOCUMENT_PROPERTY = "housei_document_json"


def _store_document(collection: bpy.types.Collection, document: dict[str, Any]) -> None:
    """Persist the parsed pattern on the collection so seams can be recut later
    without the source PDF or its external parser."""
    try:
        collection[_DOCUMENT_PROPERTY] = json.dumps(document, separators=(",", ":"))
    except (TypeError, ValueError):
        pass


def _stored_document(collection: bpy.types.Collection) -> dict[str, Any] | None:
    raw = collection.get(_DOCUMENT_PROPERTY)
    if not raw:
        return None
    try:
        document = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def remesh_with_seam_counts(
    context,
    collection: bpy.types.Collection,
    overrides: dict[str, dict[str, int]] | None = None,
) -> set[str]:
    """Recut the panels whose topology is not what this build would cut:
    panels still carrying an older builder's triangulation
    (``housei_cut_scheme``) first, then sewing seams that need matching vertex
    counts.  The current pose is transferred onto the new topology.  Returns
    the set of changed part object names (empty when nothing needed adapting).

    The order is the point.  Seam-count targets are measured on the meshes
    that exist, and a mesh cut by an older build carries that build's history:
    the pre-0.14.6 doubling bug left one reference side seam at 16x its
    natural density, and computing overrides before the upgrade adopted that
    as the target the clean side was forced up to -- 1760 stitches paired
    against 110, seventeen to a vertex, a fan at every stitch point.  So stale
    panels are re-cut clean at their natural counts before anything measures
    them, and the counts that are then matched are counts this build cut.
    """
    if collection is None or collection.get("housei_role") != "clothes":
        raise UpdateError("No loaded Housei clothes collection is selected.")
    stale = {
        obj.name
        for obj in collection.objects
        if obj.type == "MESH"
        and obj.get("housei_role") == "part"
        and _part_cut_scheme(obj) != CUT_SCHEME
    }
    changed: set[str] = set()
    if stale:
        changed |= _replace_part_meshes(context, collection, None, stale)
        context.view_layer.update()
        # Whatever the caller measured, it was measured on the stale meshes.
        overrides = None
    if overrides is None:
        overrides = compute_seam_count_overrides(collection)
    if overrides:
        changed |= _replace_part_meshes(context, collection, overrides, None)
        context.view_layer.update()
    return changed


def _replace_part_meshes(
    context,
    collection: bpy.types.Collection,
    overrides: dict[str, dict[str, int]] | None,
    force_names: set[str] | None,
) -> set[str]:
    """Recut panels from the stored pattern and swap their meshes in place.

    With ``force_names`` given, exactly those parts are re-cut (topology
    change or not) and every other part is left untouched; otherwise every
    part whose new topology differs from its current one is re-cut.
    """
    document = _stored_document(collection)
    if document is None:
        raise UpdateError(msg("remesh_no_document"))
    panels_json = document.get("panels")
    if not isinstance(panels_json, list) or not panels_json:
        raise UpdateError(msg("remesh_no_panels"))
    parts = sorted(
        (obj for obj in collection.objects if obj.type == "MESH" and obj.get("housei_role") == "part"),
        key=lambda obj: int(obj.get("housei_panel_index", 0)),
    )
    # Full pattern may list more panels than this work collection holds
    # (subset 裁断 is normal). Remesh only the instances that are present.
    panels = _panel_geometries(panels_json, overrides)
    old_by_instance = {
        str(obj.get("housei_panel_instance", obj.get("housei_panel_label", ""))): obj
        for obj in parts
    }
    new_by_instance = {panel.instance_id: panel for panel in panels}
    missing = sorted(set(old_by_instance) - set(new_by_instance))
    if missing:
        raise UpdateError(msg("remesh_missing_instances", shown=", ".join(missing[:8])))

    prepared: list[tuple[bpy.types.Object, bpy.types.Mesh, PanelGeometry]] = []
    try:
        for instance_id, obj in old_by_instance.items():
            panel = new_by_instance[instance_id]
            if force_names is not None:
                if obj.name not in force_names:
                    continue
            elif len(panel.vertices) == len(obj.data.vertices):
                continue  # unchanged topology keeps its current drape as-is
            world_positions = _transfer_deformation(obj, panel)
            inverse = obj.matrix_world.inverted_safe()
            local_positions = [inverse @ point for point in world_positions]
            mesh = bpy.data.meshes.new(f"{obj.name}_ADAPT")
            mesh.from_pydata(local_positions, panel.edges, panel.faces)
            mesh.validate(verbose=False, clean_customdata=False)
            mesh.update(calc_edges=True, calc_edges_loose=True)
            _write_panel_mesh_attributes(mesh, panel, int(obj.get("housei_panel_index", 0)))
            for material in obj.data.materials:
                mesh.materials.append(material)
            prepared.append((obj, mesh, panel))
    except Exception:
        for _obj, mesh, _panel in prepared:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        raise

    from .hou import is_hou_part, sync_hou_from_object

    changed: set[str] = set()
    old_meshes: list[bpy.types.Mesh] = []
    for obj, mesh, panel in prepared:
        old_meshes.append(obj.data)
        obj.data = mesh
        obj["housei_mesh_spacing_m"] = panel.spacing_m
        obj[CUT_SCHEME_KEY] = CUT_SCHEME
        if is_hou_part(obj) or obj.get("housei_role") == "part":
            sync_hou_from_object(obj)
        changed.add(obj.name)
    if changed:
        remove_sewn_preview(collection)
    for mesh in old_meshes:
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    context.view_layer.update()
    return changed


def create_sewn_mesh(
    context,
    collection: bpy.types.Collection,
) -> bpy.types.Object:
    """Merge positioned source parts and add loose sewing-spring edges."""
    if collection is None or collection.get("housei_role") != "clothes":
        raise SewingError(msg("sew_need_clothes"))
    if any(obj.get("housei_role") == "sewn" for obj in collection.objects):
        raise SewingError(msg("sew_already_sewn", name=collection.name))
    context.view_layer.update()
    plan = build_sewing_plan(collection)
    parts = list(plan.parts)
    labels = list(plan.labels)

    vertices: list[tuple[float, float, float]] = []
    edges: list[tuple[int, int]] = []
    faces: list[tuple[int, ...]] = []
    offsets: dict[bpy.types.Object, int] = {}
    boundary_attributes: dict[str, list[int]] = {label: [] for label in labels}
    fold_indices: list[int] = []
    face_panel_indices: list[int] = []
    for obj in parts:
        mesh = obj.data
        offsets[obj] = len(vertices)
        offset = offsets[obj]
        vertices.extend(tuple(obj.matrix_world @ vertex.co) for vertex in mesh.vertices)
        for edge in mesh.edges:
            new_index = len(edges)
            edges.append((edge.vertices[0] + offset, edge.vertices[1] + offset))
            for label in labels:
                attribute = mesh.attributes.get(f"sewing_{label}")
                if attribute is not None and bool(attribute.data[edge.index].value):
                    boundary_attributes[label].append(new_index)
            fold = mesh.attributes.get("fold")
            if fold is not None and bool(fold.data[edge.index].value):
                fold_indices.append(new_index)
        faces.extend(tuple(vertex + offset for vertex in polygon.vertices) for polygon in mesh.polygons)
        face_panel_indices.extend([int(obj.get("housei_panel_index", 0))] * len(mesh.polygons))

    spring_indices: dict[str, list[int]] = {label: [] for label in labels}
    for label, a, b in plan.connections:
        spring_indices[label].append(len(edges))
        edges.append((a, b))

    name = f"{collection.name}_SEWN"
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    try:
        collection.objects.link(obj)
        mesh.from_pydata(vertices, edges, faces)
        mesh.validate(verbose=False, clean_customdata=False)
        mesh.update(calc_edges=True, calc_edges_loose=True)
        for label in labels:
            _set_boolean_edge_attribute(mesh, f"sewing_{label}", boundary_attributes[label])
            _set_boolean_edge_attribute(mesh, f"sewing_spring_{label}", spring_indices[label])
        _set_boolean_edge_attribute(mesh, "fold", fold_indices)
        panel_attribute = mesh.attributes.new(name="panel_index", type="INT", domain="FACE")
        for polygon, panel_index in zip(mesh.polygons, face_panel_indices):
            panel_attribute.data[polygon.index].value = panel_index

        obj["housei_schema"] = "housei-pattern/1.0.0"
        obj["housei_role"] = "sewn"
        obj["housei_collection"] = collection.name
        obj["housei_source_svg"] = str(collection.get("housei_source_svg", ""))
        obj["housei_sewing_groups"] = labels
        obj["housei_source_parts"] = [part.name for part in parts]
        collection["housei_sewing_verified"] = True

        for selected in context.selected_objects:
            selected.select_set(False)
        for part in parts:
            part.hide_set(True)
            part.hide_render = True
            part.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        return obj
    except Exception:
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh.name in bpy.data.meshes:
            bpy.data.meshes.remove(mesh)
        raise
