# SPDX-License-Identifier: GPL-3.0-or-later
"""The solver backend contract: what Housei asks a solver for, and how.

Housei does not sew.  It states a sewing job -- flat panels, the pairs that
must end up coincident, the Body they must not enter -- and a backend answers
with the sewn positions.  This module is the whole of what the two sides
agree on, so a second solver is one adapter and one payload directory away,
and neither side has to read the other's code.

The boundary is a child process and three files, not a library call.  A GPU
solver that aborts takes its own process down; in Blender's it would take the
session.  That isolation is the reason the seam is here rather than behind a
C ABI, and it is not a detail to optimize away later.

Contract version 1
------------------

**Invocation.**  One temporary directory per press holds `scene.npz`,
`result.npz` and `error.json`; the adapter builds a command line that names
all three as `--input`, `--output`, `--error`, plus whatever else that
backend needs to launch.  Exit code 0 with the output file present is
success; anything else is failure.

**Input** (`scene.npz`, `np.savez`): `contract` (int64 scalar, 1),
`cloth_vertices` (float64 V,3), `cloth_pattern` (float64 V,3),
`cloth_faces` (int64 F,3), `seam_pairs` (int64 S,2), `locked` (int64 V),
`body_vertices` (float64 B,3), `body_faces` (int64 Fb,3), and `settings`,
a JSON string of the form::

    {"contract": 1, "session_name": "...", "backend": {...}}

The `backend` block is opaque here: it is written by the adapter and read by
its own driver.  All physics tuning lives in it, because every value Housei
currently sets is reasoned about in terms of one solver's behavior.  The
generic layer carries what to do, never how.

**Output** (`result.npz`): `contract` (1), `cloth_vertices` (float64 V,3),
and `report`, a JSON string carrying at least `frames_written`,
`solve_seconds`, `seam_gap_mean_mm`, `seam_gap_max_mm` and
`residual_motion_mm`.

**Error** (`error.json`): `contract`, `stage` (one of load, frontend,
remesh, clear, build, solve, readback, write), `message` (one user-facing
line), `detail` (optional).  A backend writes this for any failure it can
observe; a hard crash never reaches its own handler, so the runner falls
back to scraping the child's output.

**Invariants.**

- Units are meters, world space, Z-up.  Nothing converts at the boundary.
- Vertex count and order in = vertex count and order out.  Whatever the
  backend renumbered or remeshed internally, it undoes before answering.
- `locked == 1` vertices come back exactly where they were sent.
- The backend never sees Blender objects, names or files -- only the npz.
- It may write what it likes in its own scratch area; the three contract
  files are the only channel back.
- Scene semantics: panels arrive flat and intersection-free against the
  Body, their placed position is their stress-free shape, the Body is
  static, gravity is zero.  The job is "close every seam, then settle".

This module imports no bpy, so the contract can be exercised from plain
Python.  Nothing under `backends/` is importable from here or from Blender.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import subprocess
import tempfile

import numpy as np

from .i18n import msg


CONTRACT_VERSION = 1

# The core formats its status line out of these, so a backend that omits one
# has not finished the job it was given.
REQUIRED_REPORT_KEYS = (
    "frames_written",
    "solve_seconds",
    "seam_gap_mean_mm",
    "seam_gap_max_mm",
    "residual_motion_mm",
)


class SolverBackendError(RuntimeError):
    """A sewing job the backend could not complete.

    The message is already resolved for the operator, so callers surface it
    as it stands rather than adding a second layer of explanation.
    """


@dataclass(frozen=True)
class SolveJob:
    """One sewing job, in the units and order section 4.2 fixes."""

    session_name: str
    cloth_vertices: np.ndarray
    cloth_pattern: np.ndarray
    cloth_faces: np.ndarray
    seam_pairs: np.ndarray
    locked: np.ndarray
    body_vertices: np.ndarray
    body_faces: np.ndarray


@dataclass(frozen=True)
class SolveOutcome:
    """What a backend answers with: the sewn cloth, and what it measured."""

    cloth_vertices: np.ndarray
    report: dict


def run(backend, job: SolveJob) -> SolveOutcome:
    """Hand one job to one backend and bring the answer back.

    A backend is anything with `id`, `command(input, output, error)`,
    `cwd()`, `env()` and `settings(session_name)`; there is no base class to
    inherit, because the adapter is the descriptor.
    """
    # Cleaning up scratch files must never discard a finished solve, so the
    # directory is removed on a best-effort basis: a lingering handle here
    # would otherwise throw away half a minute of work over a temp file.
    with tempfile.TemporaryDirectory(
        prefix="housei_ppf_", ignore_cleanup_errors=True
    ) as workspace:
        input_path = os.path.join(workspace, "scene.npz")
        output_path = os.path.join(workspace, "result.npz")
        error_path = os.path.join(workspace, "error.json")
        np.savez(
            input_path,
            contract=np.int64(CONTRACT_VERSION),
            cloth_vertices=job.cloth_vertices.astype(np.float64),
            cloth_pattern=job.cloth_pattern.astype(np.float64),
            cloth_faces=job.cloth_faces.astype(np.int64),
            seam_pairs=job.seam_pairs.astype(np.int64),
            locked=job.locked.astype(np.int64),
            body_vertices=job.body_vertices.astype(np.float64),
            body_faces=job.body_faces.astype(np.int64),
            settings=json.dumps(backend.settings(job.session_name)),
        )
        completed = subprocess.run(
            backend.command(input_path, output_path, error_path),
            cwd=backend.cwd(),
            env=backend.env(),
            capture_output=True,
            text=True,
            # A solver writes progress bars and whatever its libraries print,
            # which on a Japanese Windows is not all decodable as cp932. The
            # strict default kills the reader thread and hands back no output
            # at all, so the one case that depends on scraping -- a crash the
            # backend never got to report -- loses its diagnosis.
            errors="replace",
        )
        if completed.returncode != 0 or not os.path.isfile(output_path):
            raise SolverBackendError(_failure(completed, error_path))
        # np.load on an npz is lazy and holds the file open, which on
        # Windows blocks the directory from being removed. Close it here.
        with np.load(output_path) as result:
            contract = int(result["contract"]) if "contract" in result else -1
            sewn = np.asarray(result["cloth_vertices"], dtype=np.float64)
            report = json.loads(str(result["report"]))

    if contract != CONTRACT_VERSION:
        raise SolverBackendError(
            msg(
                "zg_solver_failed_line",
                detail=(
                    f"{backend.id}: contract {contract} result "
                    f"for a contract {CONTRACT_VERSION} job"
                ),
            )
        )
    if sewn.shape != job.cloth_vertices.shape:
        raise SolverBackendError(msg("zg_solver_vertex_count"))
    missing = [key for key in REQUIRED_REPORT_KEYS if key not in report]
    if missing:
        raise SolverBackendError(
            msg(
                "zg_solver_failed_line",
                detail=f"{backend.id}: the report is missing {missing[0]}",
            )
        )
    return SolveOutcome(cloth_vertices=sewn, report=report)


def _failure(completed: subprocess.CompletedProcess, error_path: str) -> str:
    """What to tell the operator when the child came back unhappy.

    A backend that could see its own failure has already written it down, and
    that diagnosis beats anything read off a stream. Only a crash the backend
    never got to handle -- a CUDA abort, an out-of-memory kill -- leaves
    nothing but output to scrape.
    """
    try:
        with open(error_path, encoding="utf-8") as handle:
            reported = json.load(handle)
        message = str(reported["message"]).strip()
    except (OSError, ValueError, KeyError):
        message = ""
    if message:
        return msg("zg_solver_failed_line", detail=message)
    return _failure_message(completed)


def _failure_message(completed: subprocess.CompletedProcess) -> str:
    """Surface the solver's own rejection rather than a generic failure.

    Its messages are the only ground truth about why a shell was refused,
    so the last meaningful line is worth more here than the exit code.
    """
    # The solver draws progress bars on the same stream, and they arrive after
    # the traceback, so the last line is usually "build scene: 92%" and says
    # nothing. Prefer the last line that reads like a diagnosis.
    for stream in (completed.stderr, completed.stdout):
        lines = [line.strip() for line in (stream or "").splitlines() if line.strip()]
        informative = [
            line
            for line in lines
            if ("Error" in line or "error" in line or "FATAL" in line)
            and "%|" not in line
        ]
        if informative:
            return msg("zg_solver_failed_line", detail=informative[-1])
    for stream in (completed.stderr, completed.stdout):
        lines = [
            line.strip()
            for line in (stream or "").splitlines()
            if line.strip() and "%|" not in line
        ]
        if lines:
            return msg("zg_solver_failed_line", detail=lines[-1])
    return msg("zg_solver_failed_code", code=completed.returncode)


# One backend exists, so the registry is a dict and the choice is a default.
# When a second appears, this is where it registers and where a Preferences
# enum would read from; nothing above this line changes.
BACKENDS: dict[str, callable] = {}


def register(identifier: str, factory: callable) -> None:
    """Make a backend available by name."""
    BACKENDS[identifier] = factory


def default_backend():
    """The backend Zero GRAVITY sews with."""
    from . import backend_ppf  # noqa: F401 - imported for its registration

    return BACKENDS["ppf"]()
