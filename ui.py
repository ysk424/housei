# SPDX-License-Identifier: GPL-3.0-or-later
"""Housei Blender N-panel."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import bpy
from bpy.props import BoolProperty, FloatProperty, PointerProperty, StringProperty
from bpy.types import (
    AddonPreferences,
    Collection,
    Object,
    Operator,
    Panel,
    PropertyGroup,
)

from .hou import is_hou_part, sync_hou_from_object
from .i18n import msg, translations_dict
from .kitsuke import KitsukeError, adapt_seam_counts
from . import backend_ppf
from .backend_ppf import SETTLE_FRAMES, SEWING_FRAMES
from .zero_gravity import sew_zero_gravity
from .mesh_loader import (
    CUT_OUT_Z_OFFSET_M,
    apply_nonselected_fixed,
    create_sewn_mesh,
    cut_out_parts_to_work,
    ensure_work_collection,
    hou_parts_in_collection,
    participating_parts,
    remove_sewn_preview,
)
from .shell_isect_bridge import library_version
from .zozo_handoff import ZOZO_MCP_PORT, ZozoHandoffError, prepare_for_zozo


_zozo_process: subprocess.Popen[str] | None = None
_zozo_scene_name: str | None = None
_zozo_prepared_summary: str | None = None
_ZOZO_CLIENT_FILENAME = "zozo_mcp_client.py"
_ZOZO_CONFIG_FILENAME = "zozo_mcp_config.json"


def _version() -> str:
    try:
        path = os.path.join(os.path.dirname(__file__), "blender_manifest.toml")
        with open(path, "rb") as f:
            return str(tomllib.load(f).get("version", "?"))
    except Exception:
        return "?"


def _wrap_status_lines(text: str, width: int = 52) -> list[str]:
    """Split status text into panel lines (no icons; message box only)."""
    ready = msg("ready")
    raw = (text or "").strip() or ready
    lines: list[str] = []
    for paragraph in raw.replace("\r\n", "\n").split("\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        while len(paragraph) > width:
            # Prefer breaking on spaces; for Japanese text without spaces, cut hard.
            cut = paragraph.rfind(" ", 0, width)
            if cut < width // 2:
                cut = width
            lines.append(paragraph[:cut].rstrip())
            paragraph = paragraph[cut:].lstrip()
        if paragraph:
            lines.append(paragraph)
    return lines or [ready]


def _draw_status_box(layout, props) -> None:
    """Large multi-line status area; text only (no alert icons)."""
    box = layout.box()
    header = box.row()
    header.label(text="Message")
    col = box.column(align=True)
    col.scale_y = 1.05
    lines = _wrap_status_lines(props.parse_status, width=46)
    # Reserve vertical space so long Prepare/shell-isect notes stay readable.
    while len(lines) < 6:
        lines.append("")
    for line in lines[:14]:
        col.label(text=line if line else " ")


def _mesh_object_poll(_properties, obj: Object) -> bool:
    """Only allow actual mesh objects in the shared Body field."""
    return obj.type == "MESH"


def _selected_mesh_objects() -> list[Object]:
    return [obj for obj in bpy.context.selected_objects if obj.type == "MESH"]


def _selected_hou_parts() -> list[Object]:
    return [obj for obj in _selected_mesh_objects() if is_hou_part(obj)]


def _hou_parts_selected_in_collection(collection: Collection | None) -> list[Object]:
    if collection is None:
        return []
    names = {obj.name for obj in hou_parts_in_collection(collection)}
    return [obj for obj in _selected_hou_parts() if obj.name in names]


def _housei_data_dir() -> str:
    return bpy.utils.user_resource("DATAFILES", path="housei", create=True)


def _bundled_python() -> str:
    """Return Blender's bundled Python executable without external dependencies."""
    names = ["python.exe"] if os.name == "nt" else [f"python{sys.version_info.major}.{sys.version_info.minor}", "python3", "python"]
    candidates = [Path(sys.prefix) / "bin" / name for name in names]
    executable = Path(sys.executable)
    if executable.name.lower().startswith("python"):
        candidates.append(executable)
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise FileNotFoundError("Blender's bundled Python executable was not found.")


def _subprocess_environment() -> dict[str, str]:
    environment = os.environ.copy()
    inherited_paths = [path for path in sys.path if isinstance(path, str) and path]
    existing = environment.get("PYTHONPATH")
    if existing:
        inherited_paths.append(existing)
    environment["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(inherited_paths))
    return environment


def _set_zozo_status(message: str) -> None:
    if _zozo_scene_name:
        scene = bpy.data.scenes.get(_zozo_scene_name)
        if scene is not None and hasattr(scene, "housei"):
            scene.housei.parse_status = message


def _fix_windows_mojibake(text: str) -> str:
    """Repair common Windows Japanese mojibake in status / exception strings."""
    if not text:
        return text

    def _kana_kanji(s: str) -> int:
        return sum(
            1 for c in s if "\u3040" <= c <= "\u30ff" or "\u4e00" <= c <= "\u9fff"
        )

    def _hiragana(s: str) -> int:
        return sum(1 for c in s if "\u3040" <= c <= "\u309f")

    # Prefer UTF-8 recovery first (UTF-8 bytes misread as latin-1/cp1252).
    # Avoid ranking raw CP932 remaps higher — they invent garbage CJK.
    candidates: list[tuple[int, str]] = []
    for enc_from, enc_to, weight in (
        ("latin-1", "utf-8", 100),
        ("cp1252", "utf-8", 100),
        ("latin-1", "cp932", 10),
        ("cp1252", "cp932", 10),
    ):
        try:
            fixed = text.encode(enc_from, errors="strict").decode(enc_to, errors="strict")
        except (UnicodeError, LookupError):
            continue
        if "\ufffd" in fixed or fixed == text:
            continue
        score = _kana_kanji(fixed) * weight + _hiragana(fixed) * 50
        if score > 0:
            candidates.append((score, fixed))
    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]
    # Already sensible CJK, or unrecoverable: leave as-is (except known codes).
    if _kana_kanji(text) > 0:
        return text
    if "10061" in text:
        return (
            "WinError 10061: connection refused "
            f"(nothing listening on ZOZO MCP port {ZOZO_MCP_PORT})"
        )
    return text


def _zozo_mcp_port_from_scene(scene, default: int = ZOZO_MCP_PORT) -> int:
    try:
        if hasattr(scene, "zozo_contact_solver"):
            return int(scene.zozo_contact_solver.state.mcp_port) or default
    except Exception:
        pass
    return default


def _ensure_zozo_mcp_server(
    port: int = ZOZO_MCP_PORT, wait_s: float = 3.0
) -> tuple[int, str]:
    """Start ZOZO's MCP HTTP server if it is not already listening.

    Uses the official Extension API (``bpy.ops.mcp.start_server`` /
    ``start_mcp_server``), same as the N-panel Start button.

    Returns ``(actual_port, status_note)``.
    """
    import socket
    import time

    def _port_open(p: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", int(p)), timeout=0.2):
                return True
        except OSError:
            return False

    if _port_open(port):
        return int(port), f"MCP already on :{port}"

    errors: list[str] = []
    # 1) Public operator (preferred; updates ZOZO panel state / alt port).
    try:
        if hasattr(bpy.ops, "mcp") and hasattr(bpy.ops.mcp, "start_server"):
            result = bpy.ops.mcp.start_server()
            if not (result == {"FINISHED"} or "FINISHED" in str(result)):
                errors.append(f"mcp.start_server -> {result}")
        else:
            errors.append("bpy.ops.mcp.start_server unavailable")
    except Exception as exc:
        errors.append(f"ops: {_fix_windows_mojibake(str(exc))}")

    actual = _zozo_mcp_port_from_scene(bpy.context.scene, port)

    if not _port_open(actual) and not _port_open(port):
        # 2) Direct Python API (Blender 4.2+ extension module names vary).
        started = False
        for mod_name in (
            "bl_ext.user_default.ppf_contact_solver.mcp.mcp_server",
            "ppf_contact_solver.mcp.mcp_server",
        ):
            try:
                mod = __import__(
                    mod_name, fromlist=["start_mcp_server", "is_mcp_running", "get_mcp_server"]
                )
                if not mod.is_mcp_running():
                    mod.start_mcp_server(int(port))
                server = mod.get_mcp_server()
                if server is not None and getattr(server, "port", None):
                    actual = int(server.port)
                started = True
                break
            except Exception as exc:
                errors.append(f"{mod_name}: {_fix_windows_mojibake(str(exc))}")
        if not started:
            errors.append("ZOZO MCP start API not found (is the extension enabled?)")

    actual = _zozo_mcp_port_from_scene(bpy.context.scene, actual)
    deadline = time.time() + max(0.5, float(wait_s))
    while time.time() < deadline:
        if _port_open(actual):
            return actual, msg("mcp_started", port=actual)
        if actual != port and _port_open(port):
            return int(port), msg("mcp_started", port=port)
        time.sleep(0.1)

    detail = "; ".join(errors) if errors else "port did not open"
    raise RuntimeError(msg("mcp_start_fail", port=port, detail=detail))


def _poll_zozo_mcp() -> float | None:
    global _zozo_process, _zozo_scene_name, _zozo_prepared_summary
    process = _zozo_process
    if process is None:
        return None
    if process.poll() is None:
        return 0.2

    stdout, stderr = process.communicate()
    summary = _zozo_prepared_summary or msg("prepared_default")
    try:
        lines = [line for line in stdout.splitlines() if line.strip()]
        result = json.loads(lines[-1]) if lines else {}
        if process.returncode != 0 or result.get("status") != "success":
            diagnostic = _fix_windows_mojibake(
                str(result.get("message") or stderr.strip() or "ZOZO MCP setup failed.")
            )
            _set_zozo_status(
                msg("mcp_setup_failed", summary=summary, detail=diagnostic[:200])
            )
        else:
            capture = str(result.get("capture", "not needed"))
            connection = str(result.get("connection", "")).strip()
            conn_note = f"; {connection}" if connection else ""
            _set_zozo_status(
                msg(
                    "mcp_ready",
                    summary=summary,
                    capture=capture,
                    conn=conn_note,
                )
            )
    except Exception as exc:
        diagnostic = _fix_windows_mojibake(
            stderr.strip() or stdout.strip() or str(exc)
        )
        _set_zozo_status(
            msg("mcp_response_failed", summary=summary, detail=diagnostic[:200])
        )
    finally:
        _zozo_process = None
        _zozo_scene_name = None
        _zozo_prepared_summary = None
    return None


class HouseiPreferences(AddonPreferences):
    """Machine-level settings, kept out of the .blend file.

    Where the ZOZO Contact Solver lives is a property of the machine, not
    of a garment, so it belongs here rather than on the Scene: it has to
    survive opening a different file, and it is not something to re-enter
    per project.
    """

    bl_idname = __package__

    ppf_root: StringProperty(
        name="ZOZO Contact Solver",
        description=(
            "Directory of the ppf-contact-solver checkout Zero GRAVITY sews "
            "with. Leave empty to search the usual locations"
        ),
        subtype="DIR_PATH",
        default="",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "ppf_root")
        resolved = backend_ppf.describe_zozo_root()
        ok = resolved.startswith("Using") or resolved.startswith("使用中")
        layout.label(text=resolved, icon="CHECKMARK" if ok else "ERROR")


class HouseiProperties(PropertyGroup):
    parse_status: StringProperty(
        name="Status",
        description="Status and warnings (panel message area only; not operator error icons)",
        default="Ready",
        options={"TEXTEDIT_UPDATE"},
    )
    clothes_collection: PointerProperty(
        name="Clothes",
        description="Work collection for 裁断 copies and Zero GRAVITY (e.g. CLOTHES_001)",
        type=Collection,
    )
    body_object: PointerProperty(
        name="Body",
        description="Fixed body mesh used for GRAVITY collision",
        type=Object,
        poll=_mesh_object_poll,
    )
    shell_isect_include_body: BoolProperty(
        name="Shell-isect vs Body",
        description=(
            "When on (default), Prepare runs the full cloth+body shell-isect "
            "twin — the same pairs ZOZO counts. Only the body under the garment "
            "is tested, so this is seconds rather than the minutes it used to "
            "take. Turn it off to check cloth against itself alone"
        ),
        default=True,
    )
    # World-Z band of the Body copy handed to ZOZO (cm in the panel, meters
    # inside prepare_for_zozo). Defaults match a torso-length garment band.
    body_export_z_min_cm: FloatProperty(
        name="Bottom (cm)",
        description=(
            "Lower world-Z of the exported ZOZO Body mesh, in centimetres "
            "(default 40 cm = 0.4 m). Triangles fully below this height are dropped"
        ),
        default=40.0,
        min=0.0,
        max=300.0,
        soft_min=0.0,
        soft_max=200.0,
        step=10,
        precision=1,
    )
    body_export_z_max_cm: FloatProperty(
        name="Top (cm)",
        description=(
            "Upper world-Z of the exported ZOZO Body mesh, in centimetres "
            "(default 145 cm = 1.45 m). Triangles fully above this height are dropped"
        ),
        default=145.0,
        min=0.0,
        max=300.0,
        soft_min=0.0,
        soft_max=200.0,
        step=10,
        precision=1,
    )


class HOUSEI_OT_cut_out(Operator):
    bl_idname = "housei.cut_out"
    bl_label = "Cut out"
    bl_description = (
        "Copy selected HOU parts into the Clothes work collection and lift "
        f"them by {int(CUT_OUT_Z_OFFSET_M * 100)} cm on Z for easy placement"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"

    def execute(self, context):
        props = context.scene.housei
        sources = _selected_hou_parts()
        if not sources:
            props.parse_status = msg("cut_need_hou")
            return {"FINISHED"}
        try:
            work = ensure_work_collection(context, props.clothes_collection)
            props.clothes_collection = work
            copies = cut_out_parts_to_work(context, work, sources)
        except Exception as exc:
            message = str(exc).strip() or type(exc).__name__
            props.parse_status = msg("cut_failed", exc=message[:240])
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        if not copies:
            props.parse_status = msg("cut_need_hou")
            return {"FINISHED"}
        props.parse_status = msg(
            "cut_done",
            n=len(copies),
            name=work.name,
            cm=int(CUT_OUT_Z_OFFSET_M * 100),
        )
        self.report({"INFO"}, props.parse_status)
        return {"FINISHED"}


def _prepare_sewing(context, collection) -> None:
    """Bring seams and the Sewing plan up to date before the solver runs."""
    if adapt_seam_counts(context, collection):
        collection["housei_sewing_verified"] = False

    parts = participating_parts(collection)
    if len(parts) < 2:
        raise KitsukeError(msg("zero_g_need_parts"))
    if not bool(collection.get("housei_sewing_verified", False)):
        remove_sewn_preview(collection, reveal_parts=True)
        collection["housei_sewing_verified"] = False
        create_sewn_mesh(context, collection)


def _run_zero_gravity(operator: Operator, context):
    """Sew with non-selected fixed (same pin as old Existing Lock / DONE)."""
    props = context.scene.housei
    collection = props.clothes_collection
    try:
        if collection is None or collection.get("housei_role") != "clothes":
            raise KitsukeError(msg("zero_g_need_clothes"))
        free = _hou_parts_selected_in_collection(collection)
        if not free:
            # Nothing selected in the work collection: no-op (easy to notice).
            props.parse_status = msg("zero_g_noop")
            return {"FINISHED"}
        if props.body_object is None:
            raise KitsukeError(msg("zero_g_need_body"))

        apply_nonselected_fixed(collection, free)
        _prepare_sewing(context, collection)
        remove_sewn_preview(collection, reveal_parts=True)
        message = sew_zero_gravity(context, collection, props.body_object)
        for obj in hou_parts_in_collection(collection):
            sync_hou_from_object(obj)
    except Exception as exc:
        message = str(exc).strip() or type(exc).__name__
        props.parse_status = msg("zero_g_failed", exc=message[:240])
        operator.report({"ERROR"}, message)
        return {"CANCELLED"}
    props.parse_status = message
    operator.report({"INFO"}, message)
    return {"FINISHED"}


class HOUSEI_OT_kitsuke_zero_gravity(Operator):
    bl_idname = "housei.kitsuke_zero_gravity"
    bl_label = "Zero GRAVITY"
    bl_description = (
        "Sew the Clothes work collection: selected HOU parts deform, "
        "non-selected HOU parts stay fixed anchors (same pin as the old "
        f"Existing Lock). One ZOZO job ({SEWING_FRAMES}+{SETTLE_FRAMES} frames). "
        "Does nothing when nothing is selected in the work collection"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"

    def execute(self, context):
        return _run_zero_gravity(self, context)


class HOUSEI_OT_prepare_zozo(Operator):
    bl_idname = "housei.prepare_zozo"
    bl_label = "Prepare for ZOZO"
    bl_description = (
        "Re-cut any panel whose seam counts or topology are out of date, copy "
        "the garment as it stands into ZOZO cloth/body objects, run shell-isect "
        "check→fix→check (cloth-only by default; enable Shell-isect vs Body for "
        "full twin), then check every triangle has rest area the solver can "
        "integrate; on PASS start ZOZO MCP if needed and configure on port "
        f"{ZOZO_MCP_PORT}. On NG, stop and report before the solver runs"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"

    def execute(self, context):
        global _zozo_process, _zozo_scene_name, _zozo_prepared_summary
        props = context.scene.housei
        if _zozo_process is not None and _zozo_process.poll() is None:
            self.report({"WARNING"}, msg("prepare_mcp_running"))
            return {"CANCELLED"}
        try:
            prepared = prepare_for_zozo(
                context,
                props.clothes_collection,
                props.body_object,
                shell_isect_include_body=bool(props.shell_isect_include_body),
                body_z_min_m=float(props.body_export_z_min_cm) * 0.01,
                body_z_max_m=float(props.body_export_z_max_cm) * 0.01,
            )
        except ZozoHandoffError as exc:
            message = _fix_windows_mojibake(str(exc).strip() or type(exc).__name__)
            # Status box only — never self.report ERROR (avoids レポート:エラー).
            ver = library_version()
            suffix = (
                msg("shell_suffix", ver=ver)
                if ver
                else msg("shell_suffix_missing")
            )
            props.parse_status = msg(
                "prepare_stopped", message=message, suffix=suffix
            )
            return {"CANCELLED"}
        except Exception as exc:
            message = _fix_windows_mojibake(str(exc).strip() or type(exc).__name__)
            ver = library_version()
            suffix = (
                msg("shell_suffix", ver=ver)
                if ver
                else msg("shell_suffix_missing")
            )
            props.parse_status = msg(
                "prepare_failed", message=message, suffix=suffix
            )
            return {"CANCELLED"}

        shell_suffix = f" [{prepared.shell_isect.version_suffix()}]"

        # shell-isect NG and other soft stops: status box only, no MCP / no report.
        if prepared.abort_message:
            # error_report already ends with [shell-isect x.y.z]
            props.parse_status = msg(
                "prepare_stopped",
                message=prepared.abort_message,
                suffix="",
            )
            return {"CANCELLED"}

        # Self-intersection and triangle quality are both gated above; MCP only.
        recut = (
            msg("prepare_recut", n=len(prepared.remeshed_parts))
            if prepared.remeshed_parts
            else ""
        )
        quality_note = (
            f"; {prepared.quality.summary()}" if prepared.quality is not None else ""
        )
        summary = msg(
            "prepare_summary",
            recut=recut,
            seams=prepared.seam_count,
            gap_mm=prepared.seam_distance_max_m * 1000.0,
            shell=shell_suffix,
            quality=quality_note,
        )
        try:
            mcp_port, mcp_note = _ensure_zozo_mcp_server(ZOZO_MCP_PORT)
            config = prepared.mcp_configuration(context.scene)
            config["port"] = int(mcp_port)
            config_path = Path(_housei_data_dir()) / _ZOZO_CONFIG_FILENAME
            config_path.write_text(
                json.dumps(config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            client_path = Path(__file__).with_name(_ZOZO_CLIENT_FILENAME)
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            _zozo_process = subprocess.Popen(
                [_bundled_python(), str(client_path), str(config_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
                env=_subprocess_environment(),
            )
            _zozo_scene_name = context.scene.name
            _zozo_prepared_summary = summary
            # Status box only — no operator report icon for prepare success/warnings.
            props.parse_status = msg(
                "prepare_mcp_configuring",
                summary=summary,
                mcp_note=mcp_note,
                port=mcp_port,
            )
            if not bpy.app.timers.is_registered(_poll_zozo_mcp):
                bpy.app.timers.register(_poll_zozo_mcp, first_interval=0.2)
        except Exception as exc:
            message = _fix_windows_mojibake(str(exc).strip() or type(exc).__name__)
            props.parse_status = msg(
                "prepare_mcp_start_fail",
                summary=summary,
                exc=message[:240],
            )
        return {"FINISHED"}


class HOUSEI_PT_main(Panel):
    bl_idname = "HOUSEI_PT_main"
    bl_label = "Housei"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Housei"

    def draw(self, context):
        layout = self.layout
        props = context.scene.housei
        layout.label(text=f"Housei v{_version()}")
        layout.separator(factor=0.4)
        inputs = layout.column(align=True)
        inputs.label(text="Inputs")
        inputs.prop(props, "clothes_collection")
        inputs.prop(props, "body_object")
        layout.separator(factor=0.4)
        actions = layout.column(align=True)
        actions.operator(HOUSEI_OT_cut_out.bl_idname, text="Cut out")
        actions.operator(HOUSEI_OT_kitsuke_zero_gravity.bl_idname, text="Zero GRAVITY")
        # Body export Z band sits directly above Prepare for ZOZO.
        body_band = actions.box()
        body_band.label(text="Body export height")
        band_row = body_band.row(align=True)
        band_row.prop(props, "body_export_z_min_cm", text="Bottom")
        band_row.prop(props, "body_export_z_max_cm", text="Top")
        actions.operator(HOUSEI_OT_prepare_zozo.bl_idname, text="Prepare for ZOZO")
        actions.prop(props, "shell_isect_include_body", text="Shell-isect vs Body")
        layout.separator(factor=0.5)
        _draw_status_box(layout, props)


_classes = (
    HouseiPreferences,
    HouseiProperties,
    HOUSEI_OT_cut_out,
    HOUSEI_OT_kitsuke_zero_gravity,
    HOUSEI_OT_prepare_zozo,
    HOUSEI_PT_main,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.housei = PointerProperty(type=HouseiProperties)
    bpy.app.translations.register(__package__, translations_dict)


def unregister():
    global _zozo_process, _zozo_scene_name, _zozo_prepared_summary
    bpy.app.translations.unregister(__package__)
    if bpy.app.timers.is_registered(_poll_zozo_mcp):
        bpy.app.timers.unregister(_poll_zozo_mcp)
    if _zozo_process is not None and _zozo_process.poll() is None:
        _zozo_process.terminate()
    _zozo_process = None
    _zozo_scene_name = None
    _zozo_prepared_summary = None
    del bpy.types.Scene.housei
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
