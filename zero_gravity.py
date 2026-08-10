# SPDX-License-Identifier: GPL-3.0-or-later
"""Zero GRAVITY: sew the panels with the ZOZO Contact Solver.

Zero GRAVITY closes every seam of a garment whose panels are still flat and
still outside the Body.  That is the whole job, so this runs it as one solve
rather than as the repeated nudges Housei's own square-lattice solver used to
take, which is why that solver is no longer here.

Sewing this way is not the same trade.  A positional projection reaches a
seam by iterating, so its stiffness is a function of how many iterations a
click can afford, and buying speed costs correctness.  The contact solver
closes a seam with an implicit force solved inside its Newton step, so the
result is the converged one at any step count, and the answer to "make it
faster" stops being "make it worse".  What it costs instead is wall clock: a
press is a job of a few seconds, not a button that answers in one frame.

Two things make that affordable, and both come from what Housei can promise
about its own state.  The Body never moves, so it is handed over as a
static collider: no degrees of freedom, uploaded to the device once.  The
panels start flat and outside the Body, so the scene begins free of
intersection, which is the state the solver requires and the state the
existing ZOZO hand-off cannot reach from already-draped cloth.

Because the panels are flat at the start, their placed position is also
their stress-free shape, and the solver takes the geometry it is given as
rest.  So a press always sews from flat; pressing again re-sews rather than
advancing, and never mistakes stretched cloth for the pattern.

Which solver does the sewing is not this module's business.  It gathers the
job, hands it to a backend through the contract in `solver_backend`, and
judges the answer; where that answer came from, and every setting that made
it, belong to the adapter.
"""

from __future__ import annotations

import bpy
import numpy as np

from .i18n import msg
from .kitsuke import (
    KitsukeError,
    _PartRange,
    _body_snapshot,
    _seam_constraints_from_parts,
    _transform_points,
    _world_vertices,
    part_ranges,
)
from . import solver_backend
from .solver_backend import SolveJob, SolverBackendError


def _cloth_geometry(parts: list[_PartRange]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Concatenated world vertices, triangles, and per-vertex Lock flags.

    The contact solver simulates triangles, so the panel quads are read
    through Blender's own triangulation rather than through the grainline
    quad map, which describes a material metric rather than a surface.
    """
    position_blocks: list[np.ndarray] = []
    face_blocks: list[np.ndarray] = []
    locked_blocks: list[np.ndarray] = []
    pattern_blocks: list[np.ndarray] = []
    for part in parts:
        mesh = part.obj.data
        pattern_blocks.append(_pattern_coordinates(part))
        mesh.calc_loop_triangles()
        triangles = np.empty((len(mesh.loop_triangles), 3), dtype=np.int64)
        mesh.loop_triangles.foreach_get("vertices", triangles.ravel())
        if not len(triangles):
            raise KitsukeError(msg("zg_no_triangles", name=part.obj.name))
        matrix = part.obj.matrix_world
        block = _world_vertices(part.obj)
        # A reflected Object transform reverses winding once the vertices
        # reach world space; restore the authored outward side so contact
        # pushes cloth away from the Body rather than into it.
        if matrix.to_3x3().determinant() < 0.0:
            triangles = triangles[:, (0, 2, 1)]
        position_blocks.append(block)
        face_blocks.append(triangles + part.start)
        locked_blocks.append(
            np.full(part.count, 1 if part.locked else 0, dtype=np.int64)
        )
    return (
        np.concatenate(position_blocks).astype(np.float64),
        np.concatenate(face_blocks),
        np.concatenate(locked_blocks),
        np.concatenate(pattern_blocks).astype(np.float64),
    )


def _pattern_coordinates(part: _PartRange) -> np.ndarray:
    """The panel's authored flat pattern coordinates, per vertex.

    This is the domain the solver's mesh is rebuilt in, because it is the one
    thing about a panel that stays flat: the cloth curves as it is sewn, and a
    sleeve is a tube before anything is sewn at all.
    """
    mesh = part.obj.data
    attribute = mesh.attributes.get("housei_pattern_position")
    if (
        attribute is None
        or attribute.domain != "POINT"
        or attribute.data_type != "FLOAT_VECTOR"
        or len(attribute.data) != len(mesh.vertices)
    ):
        raise KitsukeError(msg("zg_pattern_missing", name=part.obj.name))
    block = np.empty((len(mesh.vertices), 3), dtype=np.float64)
    attribute.data.foreach_get("vector", block.ravel())
    if not np.all(np.isfinite(block)):
        raise KitsukeError(msg("zg_pattern_nonfinite", name=part.obj.name))
    return block


def _validate(
    parts: list[_PartRange],
    positions: np.ndarray,
    seams: np.ndarray,
    locked: np.ndarray,
) -> None:
    if not len(seams):
        raise KitsukeError(msg("zg_no_seams"))
    if seams.min() < 0 or seams.max() >= len(positions):
        raise KitsukeError(msg("zg_seam_mismatch"))
    if not np.all(np.isfinite(positions)):
        raise KitsukeError(msg("zg_nonfinite_panels"))
    if np.all(locked == 1):
        raise KitsukeError(msg("zg_all_locked"))


def _scatter(parts: list[_PartRange], positions: np.ndarray) -> None:
    for part in parts:
        obj = part.obj
        inverse = obj.matrix_world.inverted_safe()
        block = positions[part.start : part.start + part.count]
        local = _transform_points(block.astype(np.float32), inverse)
        obj.data.vertices.foreach_set("co", local.ravel())
        obj.data.update()
        obj.hide_set(False)
        obj.hide_render = False


def sew_zero_gravity(
    context,
    collection: bpy.types.Collection,
    body: bpy.types.Object,
) -> str:
    """Sew every seam of the collection and write the result back to Blender."""
    if collection is None or collection.get("housei_role") != "clothes":
        raise KitsukeError(msg("zg_need_clothes"))

    parts = part_ranges(collection, "無重力着付")
    positions, faces, locked, pattern = _cloth_geometry(parts)
    seams = _seam_constraints_from_parts(collection, parts)
    _validate(parts, positions, seams, locked)
    body_snapshot = _body_snapshot(context, body)

    job = SolveJob(
        session_name=f"housei_{collection.name}",
        cloth_vertices=positions,
        cloth_pattern=pattern,
        cloth_faces=faces,
        seam_pairs=seams.astype(np.int64),
        locked=locked,
        body_vertices=body_snapshot.vertices.astype(np.float64),
        body_faces=body_snapshot.faces.astype(np.int64),
    )
    # Everything the operator has to be told about a failed press already
    # reads as one line, so it travels the way every other Housei failure
    # does and the N-panel needs to know nothing about backends.
    try:
        outcome = solver_backend.run(solver_backend.default_backend(), job)
    except SolverBackendError as exc:
        raise KitsukeError(str(exc)) from exc
    sewn = outcome.cloth_vertices
    report = outcome.report

    if sewn.shape != positions.shape:
        raise KitsukeError(msg("zg_solver_vertex_count"))
    if not np.all(np.isfinite(sewn)):
        raise KitsukeError(msg("zg_solver_nonfinite"))
    # Sewing moves cloth a garment's width at most. A result that throws a
    # vertex far past the Body is not cloth, it is a rebuilt panel that failed
    # to locate one of its vertices, and writing it back would scatter the
    # garment across the scene with nothing to say why.
    body_size = float(
        np.linalg.norm(body_snapshot.bounds_maximum - body_snapshot.bounds_minimum)
    )
    travelled = np.linalg.norm(sewn - positions, axis=1)
    if travelled.max() > body_size:
        raise KitsukeError(
            msg(
                "zg_solver_travelled",
                travelled=float(travelled.max()),
                body_size=body_size,
            )
        )

    _scatter(parts, sewn)
    context.view_layer.update()
    return msg(
        "zero_g_ok",
        pairs=len(seams),
        panels=len(parts),
        frames=report["frames_written"],
        seconds=float(report["solve_seconds"]),
        gap_mean=float(report["seam_gap_mean_mm"]),
        gap_max=float(report["seam_gap_max_mm"]),
        residual=float(report["residual_motion_mm"]),
    )
