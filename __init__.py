# SPDX-License-Identifier: GPL-3.0-or-later
"""縫製 (Housei): cut out, Zero GRAVITY dressing, and ZOZO hand-off for HOU parts."""

from __future__ import annotations

from . import ui


def register():
    ui.register()


def unregister():
    ui.unregister()
