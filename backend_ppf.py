# SPDX-License-Identifier: GPL-3.0-or-later
"""The ZOZO Contact Solver behind Housei's solver backend contract.

Everything specific to that solver lives here: where its checkout is, which
interpreter owns its dependencies, and the tuning that makes it sew rather
than drift.  The payload it launches is `backends/ppf/driver.py`, which runs
in the solver's own interpreter and is never imported from Blender.

The tuning constants below are PPF's, not Housei's.  Their reasons are
written in terms of how that solver behaves -- where its seam force
saturates, how its damping fights its stitches -- so they belong to the
adapter, and a different backend would arrive with different ones rather
than inherit these.
"""

from __future__ import annotations

import os
from pathlib import Path

import bpy

from .i18n import msg
from .kitsuke import KitsukeError
from . import solver_backend


# Solver settings for sewing flat panels in free space.  Young's modulus and
# bend match the ZOZO garment examples; the strain limit is what keeps a
# seam from closing by stretching the panel instead of moving it.
YOUNG_MODULUS = 100.0
BEND = 1.0
STRAIN_LIMIT = 0.05
TIME_STEP = 0.01

# The job sews first and settles second; the driver owns why.
SEWING_FRAMES = 6
SETTLE_FRAMES = 5
# Drag high enough to settle the garment also overpowers the seam force, so
# it belongs only to the second phase. Anything from 2 upward settles; the
# value is not delicate.
AIR_DRAG = 5.0
STITCH_STIFFNESS = 1.0
# Raises the cap the seam force saturates at; the driver owns why it matters.
# The reference garment's panels start 292 mm apart, and the stock factor of
# 10 caps the pull at about 5 mm of separation, so they barely move: 8 frames
# closed 292 mm to 211 mm.  At 100 the same seam reaches 2.1 mm in 6.  Going
# further buys nothing measurable (3000 gives 2.06 mm), so this is set past
# the widest seam rather than as high as it will go.
STITCH_LENGTH_FACTOR = 100.0

_ENVIRONMENT_ROOT = "HOUSEI_PPF_ROOT"
_TREE_NAME = "ppf-contact-solver"


def is_zozo_tree(path: Path) -> bool:
    """Whether this directory is a usable ZOZO Contact Solver checkout."""
    return (path / "frontend" / "__init__.py").is_file()


def _configured_root() -> str:
    """The path set in Add-on Preferences, if the add-on is registered."""
    try:
        preferences = bpy.context.preferences.addons[__package__].preferences
    except (AttributeError, KeyError):
        return ""
    return bpy.path.abspath(getattr(preferences, "ppf_root", "") or "").strip()


def _candidate_roots() -> list[Path]:
    """Where to look, best evidence first.

    Installed as an extension, this module lives under Blender's own
    `bl_ext` directory, so a path relative to it finds nothing; that
    fallback is only useful when Housei runs from its checkout. Hence the
    explicit setting first and a search of the usual checkout homes after.
    """
    candidates: list[Path] = []
    override = os.environ.get(_ENVIRONMENT_ROOT, "").strip()
    if override:
        candidates.append(Path(override))
    configured = _configured_root()
    if configured:
        candidates.append(Path(configured))
    candidates.append(Path(__file__).resolve().parent.parent / _TREE_NAME)
    home = Path.home()
    candidates.extend(
        home / parent / _TREE_NAME
        for parent in ("git", "source/repos", "Documents", "projects", "")
    )
    return candidates


def _zozo_root() -> Path:
    candidates = _candidate_roots()
    for candidate in candidates:
        if is_zozo_tree(candidate):
            return candidate
    searched = "\n".join(f"  {candidate}" for candidate in candidates)
    raise KitsukeError(
        msg(
            "zg_zozo_tree_missing",
            env=_ENVIRONMENT_ROOT,
            searched=searched,
        )
    )


def describe_zozo_root() -> str:
    """One line for Preferences saying what the path setting resolved to.

    Reported where the path is entered, so a wrong directory is visible
    when it is typed rather than when Zero GRAVITY is first pressed.
    """
    try:
        root = _zozo_root()
    except KitsukeError:
        return msg("zg_prefs_not_found")
    try:
        _zozo_python(root)
    except KitsukeError as exc:
        return str(exc)
    return msg("zg_prefs_using", root=root)


def _zozo_python(root: Path) -> Path:
    """The interpreter that owns the ZOZO frontend's dependencies.

    Blender's own Python cannot be used: the frontend loads a Rust cdylib
    built against its tree and pulls in numpy and scipy of its own.
    """
    bundled = root / "build-win-native" / "python" / "python.exe"
    if bundled.is_file():
        return bundled
    raise KitsukeError(msg("zg_zozo_python_missing", root=root))


class PpfBackend:
    """The contract's PPF side: how to launch the driver and what to tell it."""

    id = "ppf"

    def __init__(self) -> None:
        # Resolving here means a missing or wrong checkout is reported when
        # the press starts, with the searched paths, rather than as a child
        # process that fails for reasons the operator cannot act on.
        self.root = _zozo_root()
        self.interpreter = _zozo_python(self.root)

    def command(self, input_path: str, output_path: str, error_path: str) -> list[str]:
        driver = Path(__file__).resolve().parent / "backends" / "ppf" / "driver.py"
        return [
            str(self.interpreter),
            str(driver),
            "--input",
            input_path,
            "--output",
            output_path,
            "--error",
            error_path,
            # Launch information travels on the command line, not inside the
            # scene file: where the solver lives is not a fact about cloth.
            "--ppf-root",
            str(self.root),
        ]

    def cwd(self) -> str:
        """The solver's own tree; its frontend resolves its cdylib from here."""
        return str(self.root)

    def env(self) -> dict[str, str]:
        environment = dict(os.environ)
        # `clear.py` loads the tri-tri checker through this, so the payload
        # finds the add-on's copy wherever the add-on was installed.
        environment["SHELL_ISECT_DLL"] = str(
            Path(__file__).resolve().parent / "bin" / "shell_isect.dll"
        )
        return environment

    def settings(self, session_name: str) -> dict:
        return {
            "contract": solver_backend.CONTRACT_VERSION,
            "session_name": session_name,
            "backend": {
                "young_modulus": YOUNG_MODULUS,
                "bend": BEND,
                "strain_limit": STRAIN_LIMIT,
                "time_step": TIME_STEP,
                "sew_frames": SEWING_FRAMES,
                "settle_frames": SETTLE_FRAMES,
                "air_drag": AIR_DRAG,
                "stitch_stiffness": STITCH_STIFFNESS,
                "stitch_length_factor": STITCH_LENGTH_FACTOR,
            },
        }


solver_backend.register(PpfBackend.id, PpfBackend)
