# SPDX-License-Identifier: GPL-3.0-or-later
"""Japanese-first interface strings for the Housei (縫製) N-panel.

Housei is the Japanese product line. Status and error messages are written in
Japanese by the author; that Japanese text is authoritative. English strings
for the Message box are optional aids only and may lag or be imprecise.

UI labels keep English source strings as Blender identifiers and are translated
through ``translations_dict`` when Blender's interface language is Japanese.
"""

from __future__ import annotations


# Each key is a (context, source string) pair. Blender resolves an operator
# button label in the "Operator" context and a panel heading, property name, or
# plain label in the default "*" context, so a string used as both is registered
# under both.
translations_dict = {
    "ja_JP": {
        ("*", "Housei"): "縫製",
        ("*", "Inputs"): "入力",
        ("*", "Message"): "メッセージ",
        ("*", "Pattern Path"): "型紙",
        ("*", "Clothes"): "衣服",
        ("*", "Body"): "ボディ",
        ("*", "Status"): "状態",
        ("Operator", "Load"): "読み込み",
        ("*", "Load"): "読み込み",
        ("Operator", "Cut out"): "裁断",
        ("*", "Cut out"): "裁断",
        ("Operator", "Zero GRAVITY"): "無重力着付",
        ("*", "Zero GRAVITY"): "無重力着付",
        ("Operator", "Prepare for ZOZO"): "ZOZO用準備作業",
        ("*", "Prepare for ZOZO"): "ZOZO用準備作業",
        ("*", "Body export height"): "ボディ書き出し高さ",
        ("*", "Bottom"): "下",
        ("*", "Top"): "上",
        ("*", "Bottom (cm)"): "下 (cm)",
        ("*", "Top (cm)"): "上 (cm)",
        ("*", "Shell-isect vs Body"): "ボディとの交差検査",
        ("*", "ZOZO Contact Solver"): "ZOZO Contact Solver",
        ("*", "Ready"): "準備完了",
        ("*", "Loading..."): "読み込み中...",
    }
}


def is_japanese() -> bool:
    """True when Blender's UI language is Japanese."""
    try:
        import bpy

        locale = str(getattr(bpy.app.translations, "locale", "") or "")
        return locale.lower().startswith("ja")
    except Exception:
        return False


# Dynamic status / soft-error templates. Keys are stable English ids.
_STATUS_EN: dict[str, str] = {
    "ready": "Ready",
    "loading": "Loading...",
    "load_already": "A pattern is already being loaded.",
    "load_need_pdf": "Select a PDF pattern file first.",
    "load_need_pdf_file": "Pattern Path must point to an existing .pdf file.",
    "parser_missing": "Parser program is missing: {name}",
    "parser_start_failed": "Could not start pattern parser: {exc}",
    "load_failed": "Load failed: {exc}",
    "loaded_parts": "Loaded {name}: {n} HOU part(s)",
    "cut_need_hou": "Select HOU part(s) before Cut out.",
    "cut_failed": "Cut out failed: {exc}",
    "cut_done": "Cut out: copied {n} part(s) to {name} (Z+{cm} cm)",
    "zero_g_noop": "Zero GRAVITY: nothing selected in Clothes; no change.",
    "zero_g_failed": "Zero GRAVITY failed: {exc}",
    "zero_g_need_parts": "Clothes needs at least two HOU parts to sew.",
    "zero_g_need_clothes": "Select a Clothes work collection first.",
    "zero_g_need_body": "Select a mesh Body before pressing Zero GRAVITY.",
    "zero_g_ok": (
        "Zero GRAVITY: sewed {pairs} pairs across {panels} panels "
        "in {frames} frames ({seconds:.1f} s); "
        "seam gap mean {gap_mean:.2f} mm, max {gap_max:.2f} mm; "
        "last frame moved {residual:.3f} mm"
    ),
    "kitsuke_need_two_parts": "{purpose} needs at least two HOU parts.",
    "kitsuke_apply_scale": (
        "Apply Scale on {name} before {purpose}; "
        "moving and rotating are supported, scaling is not."
    ),
    "kitsuke_sewing_required": "Automatic Sewing is required before a solver can run.",
    "kitsuke_sewing_failed": "Automatic Sewing failed: {exc}",
    "kitsuke_sewing_mismatch": (
        "Automatic Sewing failed: the verified panel set no longer matches the current objects."
    ),
    "kitsuke_need_body": "Select a mesh Body first.",
    "kitsuke_body_not_mesh": (
        "Body '{name}' is {type}, not MESH. Select the character's actual skin mesh."
    ),
    "kitsuke_body_no_tris": "Body has no triangles for collision detection.",
    "zg_zozo_tree_missing": (
        "The ZOZO Contact Solver tree was not found. Set its path in "
        "Preferences > Add-ons > Housei (or the {env} environment variable). "
        "Looked in:\n{searched}"
    ),
    "zg_zozo_python_missing": (
        "No ZOZO Python interpreter found under {root}. "
        "Build the native Windows distribution there first."
    ),
    "zg_prefs_not_found": "Not found. Zero GRAVITY cannot sew until this points at the checkout.",
    "zg_prefs_using": "Using {root}",
    "zg_no_triangles": "{name} has no triangles to simulate.",
    "zg_pattern_missing": (
        "{name} has no valid pattern coordinates. Load it again before Zero GRAVITY."
    ),
    "zg_pattern_nonfinite": "{name} has non-finite pattern coordinates.",
    "zg_no_seams": "There are no seams to sew.",
    "zg_seam_mismatch": "The sewing pairs do not match the current panel vertices.",
    "zg_nonfinite_panels": "The panels contain non-finite coordinates.",
    "zg_all_locked": "Every panel is Locked; unlock at least one before sewing.",
    "zg_need_clothes": "No Housei Clothes work collection is selected.",
    "zg_solver_vertex_count": "The solver returned a different vertex count than it was given.",
    "zg_solver_nonfinite": "The solver returned a non-finite state; the cloth was left unchanged.",
    "zg_solver_travelled": (
        "The solver moved a vertex {travelled:.2f} m, further than the "
        "whole Body ({body_size:.2f} m), so the result was discarded and the "
        "cloth left unchanged."
    ),
    "zg_solver_failed_line": "The ZOZO solver failed: {detail}",
    "zg_solver_failed_code": "The ZOZO solver failed with exit code {code}.",
    "sew_need_clothes": "No Housei Clothes work collection is selected.",
    "sew_need_two_parts": (
        "GRAVITY needs at least two HOU parts with a resolvable sewing connection."
    ),
    "sew_group_two_parts": (
        "Sewing group {label} must occur on exactly two different cloth parts, "
        "or twice on one part to close it onto itself."
    ),
    "sew_duplicate_spring": "Two sewing groups produce the same sewing spring.",
    "sew_no_group_yet": (
        "The selected and fixed parts contain no resolvable sewing group yet."
    ),
    "sew_branches": "Sewing group {label} branches on {name}.",
    "sew_not_continuous": "Sewing group {label} is not a continuous path on {name}.",
    "sew_not_simple_closed": "Sewing group {label} is not a simple closed path on {name}.",
    "sew_cannot_order_closed": "Cannot order closed sewing group {label} on {name}.",
    "sew_cannot_order": "Cannot order sewing group {label} on {name}.",
    "sew_closed_need_circular": "Closed sewing paths require circular matching.",
    "sew_path_count_diff": (
        "Sewing group {label} has different numbers of continuous paths on its two parts."
    ),
    "sew_too_many_paths": "Sewing group {label} has too many separate paths for automatic pairing.",
    "sew_ambiguous_pair": (
        "Sewing group {label} has an ambiguous path pairing; move the parts closer to their intended seams."
    ),
    "sew_zero_length": "A sewing path has zero length.",
    "sew_mixed_open_closed": "Sewing group {label} mixes unsupported open and closed paths.",
    "sew_direction_ambiguous": (
        "Sewing direction for group {label} is ambiguous; move the parts closer to their intended seams."
    ),
    "sew_open_only_composite": "Only open paths can be joined into a composite sewing loop.",
    "sew_cannot_align_closed": "Cannot align closed sewing paths.",
    "sew_already_sewn": "{name} already has a sewn mesh.",
    "sew_ring_need_body": (
        "Sewing group {label} needs closed RING paths and open paths on exactly two body parts."
    ),
    "sew_ring_cannot_pair": (
        "Sewing group {label} cannot pair its {count} closed path(s) with the body paths."
    ),
    "remesh_no_document": (
        "This clothes collection has no stored pattern; cut out parts from "
        "a loaded CUTTINGCLOTH collection so the pattern is copied over."
    ),
    "remesh_no_panels": "The stored pattern has no panels.",
    "remesh_missing_instances": (
        "Stored pattern has no panel instance for: {shown}"
    ),
    "cut_need_clothes_role": "Work collection must have housei_role=clothes.",
    "remesh_pattern_missing": (
        "{name} has no original pattern coordinates. Load and cut out again."
    ),
    "remesh_construction_missing": (
        "{name} has no construction coordinates. Load and cut out again."
    ),
    "prepare_mcp_running": "ZOZO MCP configuration is already running.",
    "prepare_stopped": "Prepare for ZOZO stopped: {message}{suffix}",
    "prepare_failed": "Prepare for ZOZO failed: {message}{suffix}",
    "prepare_summary": (
        "{recut}prepared {seams} ZOZO stitches "
        "(widest seam still open {gap_mm:.2f} mm){shell}{quality}"
    ),
    "prepare_recut": "re-cut {n} panel(s); ",
    "prepare_mcp_configuring": "{summary}; {mcp_note}; configuring ZOZO MCP on :{port}...",
    "prepare_mcp_start_fail": (
        "{summary}; copies are ready, but MCP could not start: {exc}"
    ),
    "mcp_started": "MCP started on :{port}",
    "mcp_start_fail": (
        "Could not start ZOZO MCP on :{port} ({detail}). "
        "Enable ZOZO Contact Solver and use MCP Start, then Prepare again."
    ),
    "mcp_setup_failed": "{summary}; ZOZO MCP setup failed: {detail}",
    "mcp_ready": (
        "{summary}; ZOZO MCP ready ({capture}){conn}. "
        "Use Transfer, then Run Simulation."
    ),
    "mcp_response_failed": "{summary}; ZOZO MCP response failed: {detail}",
    "prepared_default": "Prepared the ZOZO hand-off mesh",
    "shell_unavailable": "shell-isect unavailable",
    "shell_suffix": " [shell-isect {ver}]",
    "shell_suffix_missing": " [shell-isect unavailable]",
    # shell-isect / quality (user-facing status box)
    "shell_err_unavailable": (
        "ERROR: self-intersection check unavailable ({message}) [{suffix}]"
    ),
    "shell_err_failed": (
        "ERROR: self-intersection check failed ({message}) [{suffix}]"
    ),
    "shell_err_pairs": (
        "ERROR: self-intersection (tri-tri face pairs): {pipeline}"
        "{faces}{pairs} [{suffix}]"
    ),
    "shell_faces_range": " cloth_faces=0..{last}",
    "shell_face_pairs": " face_pairs: {pairs}",
    "shell_mode_both": "cloth+body",
    "shell_mode_cloth": "cloth-only",
    "shell_summary": "shell-isect {version} ({mode}{crop}): {pipeline}",
    "shell_crop": ", body {tested}/{total} tris",
    "shell_pipeline_clean": "check1=0 (clean; fix skipped)",
    "shell_pipeline": "check1={before} fix={fix} check2={after}",
    "quality_summary": (
        "triangle quality: {faces} faces, smallest rest area "
        "{area_min:.2e} m² (floor {floor:.2e}), "
        "shortest edge {edge_mm:.3f} mm, "
        "worst aspect {aspect:.2e}, "
        "{failing} under the floor"
    ),
    "quality_error": (
        "ERROR: {failing} triangle(s) have too little rest area for the solver: "
        "smallest {area_min:.2e} m² against a floor of {floor:.2e} m². "
        "A shell element's stiffness scales with 1/area, so these take the first "
        "solve to NaN and it stops after frame 0"
    ),
    "quality_worst": ". Worst: {shown}",
    "quality_worst_more": ", ... (+{n} more)",
    "quality_worst_item": (
        "(face {index}: area {area:.2e} m², shortest edge {edge_mm:.4f} mm)"
    ),
    # zozo handoff hard errors
    "zozo_need_clothes": "Select a loaded Housei Clothes collection first.",
    "zozo_need_body": "Select a mesh Body before Prepare for ZOZO.",
    "zozo_no_seams": "The garment has no sewing edges.",
    "zozo_nonfinite": "The cloth contains a non-finite vertex position.",
    "zozo_seam_mismatch": "The sewing pairs do not match the current panel vertices.",
    "zozo_topo_changed": "The ZOZO hand-off topology changed while creating the mesh.",
    "zozo_stitch_lost": "A loose ZOZO stitch edge was lost while creating the mesh.",
    "zozo_no_body_export": "ZOZO body was not exported; cannot configure MCP.",
    "zozo_stale_pitch": (
        "{n} panel(s) were cut on a lattice this build no longer cuts and would "
        "go over at the wrong pitch: {shown}, against {mm:.0f} mm. Re-load and "
        "cut out again, then run GRAVITY before handing over"
    ),
    "zozo_pattern_missing": (
        "{name} has no valid Housei pattern coordinates; load the pattern again."
    ),
}

_STATUS_JA: dict[str, str] = {
    "ready": "準備完了",
    "loading": "読み込み中...",
    "load_already": "別の型紙を読み込み中です。",
    "load_need_pdf": "先に PDF 型紙ファイルを指定してください。",
    "load_need_pdf_file": "型紙パスは存在する .pdf ファイルを指す必要があります。",
    "parser_missing": "パーサが見つかりません: {name}",
    "parser_start_failed": "型紙パーサを起動できませんでした: {exc}",
    "load_failed": "読み込み失敗: {exc}",
    "loaded_parts": "読み込み完了 {name}: HOU パーツ {n}",
    "cut_need_hou": "裁断の前に HOU 付きパーツを選択してください。",
    "cut_failed": "裁断失敗: {exc}",
    "cut_done": "裁断: {n} パーツを {name} にコピー（Z+{cm} cm）",
    "zero_g_noop": "無重力着付: 衣服コレクション内で未選択のため何もしません。",
    "zero_g_failed": "無重力着付 失敗: {exc}",
    "zero_g_need_parts": "衣服には縫う HOU パーツが少なくとも 2 つ必要です。",
    "zero_g_need_clothes": "先に衣服（作業）コレクションを指定してください。",
    "zero_g_need_body": "無重力着付の前にメッシュのボディを選択してください。",
    "zero_g_ok": (
        "無重力着付: 縫いペア {pairs}、パーツ {panels}、"
        "{frames} フレーム ({seconds:.1f} 秒); "
        "縫い目すきま 平均 {gap_mean:.2f} mm、最大 {gap_max:.2f} mm; "
        "最終フレーム移動 {residual:.3f} mm"
    ),
    "kitsuke_need_two_parts": "{purpose} には HOU パーツが少なくとも 2 つ必要です。",
    "kitsuke_apply_scale": (
        "{purpose} の前に {name} に「スケールを適用」してください。"
        "移動と回転は可、スケールは不可です。"
    ),
    "kitsuke_sewing_required": "ソルバ実行の前に自動縫い合わせが必要です。",
    "kitsuke_sewing_failed": "自動縫い合わせ失敗: {exc}",
    "kitsuke_sewing_mismatch": (
        "自動縫い合わせ失敗: 検証済みパネル集合が現在のオブジェクトと一致しません。"
    ),
    "kitsuke_need_body": "先にメッシュのボディを選択してください。",
    "kitsuke_body_not_mesh": (
        "ボディ '{name}' は {type} であり MESH ではありません。キャラクターの皮膚メッシュを選んでください。"
    ),
    "kitsuke_body_no_tris": "ボディに衝突用の三角形がありません。",
    "zg_zozo_tree_missing": (
        "ZOZO Contact Solver のツリーが見つかりません。"
        "設定 > アドオン > Housei（または環境変数 {env}）でパスを指定してください。"
        "検索場所:\n{searched}"
    ),
    "zg_zozo_python_missing": (
        "{root} に ZOZO 用 Python がありません。"
        "先にそのツリーで Windows ネイティブ配布をビルドしてください。"
    ),
    "zg_prefs_not_found": "未検出。チェックアウト先を指すまで無重力着付は縫えません。",
    "zg_prefs_using": "使用中: {root}",
    "zg_no_triangles": "{name} にシミュレーション用の三角形がありません。",
    "zg_pattern_missing": (
        "{name} に有効な型紙座標がありません。無重力着付の前に読み込み直してください。"
    ),
    "zg_pattern_nonfinite": "{name} の型紙座標に有限でない値があります。",
    "zg_no_seams": "縫う縫い目がありません。",
    "zg_seam_mismatch": "縫い目ペアが現在のパネル頂点と一致しません。",
    "zg_nonfinite_panels": "パネルに有限でない座標があります。",
    "zg_all_locked": "すべてのパネルが固定されています。縫う前に少なくとも 1 つを選択して自由にしてください。",
    "zg_need_clothes": "衣服（作業）コレクションが選択されていません。",
    "zg_solver_vertex_count": "ソルバが渡した頂点数と異なる結果を返しました。",
    "zg_solver_nonfinite": "ソルバが有限でない状態を返したため、布は変更していません。",
    "zg_solver_travelled": (
        "ソルバが頂点を {travelled:.2f} m 動かしました（ボディ全体 {body_size:.2f} m を超える）。"
        "結果を破棄し、布は変更していません。"
    ),
    "zg_solver_failed_line": "ZOZO ソルバ失敗: {detail}",
    "zg_solver_failed_code": "ZOZO ソルバが終了コード {code} で失敗しました。",
    "sew_need_clothes": "衣服（作業）コレクションが選択されていません。",
    "sew_need_two_parts": (
        "着付には、解決可能な縫い接続を持つ HOU パーツが少なくとも 2 つ必要です。"
    ),
    "sew_group_two_parts": (
        "縫いグループ {label} は、ちょうど 2 つの異なる布パーツに出るか、"
        "1 パーツ上で自分自身に閉じる必要があります。"
    ),
    "sew_duplicate_spring": "2 つの縫いグループが同じ縫いばねを作りました。",
    "sew_no_group_yet": (
        "選択パーツと固定パーツから解決できる縫いグループがまだありません。"
    ),
    "sew_branches": "縫いグループ {label} が {name} 上で分岐しています。",
    "sew_not_continuous": "縫いグループ {label} が {name} 上で連続したパスではありません。",
    "sew_not_simple_closed": "縫いグループ {label} が {name} 上で単純な閉パスではありません。",
    "sew_cannot_order_closed": "閉路の縫いグループ {label} を {name} 上で順序付けできません。",
    "sew_cannot_order": "縫いグループ {label} を {name} 上で順序付けできません。",
    "sew_closed_need_circular": "閉じた縫いパスには円環状の対応が必要です。",
    "sew_path_count_diff": (
        "縫いグループ {label} の両側で連続パスの本数が違います。"
    ),
    "sew_too_many_paths": "縫いグループ {label} の分離パスが多すぎて自動対応できません。",
    "sew_ambiguous_pair": (
        "縫いグループ {label} のパス対応が曖昧です。意図した縫い目へパーツを近づけてください。"
    ),
    "sew_zero_length": "縫いパスの長さがゼロです。",
    "sew_mixed_open_closed": "縫いグループ {label} は開パスと閉パスの混在が非対応です。",
    "sew_direction_ambiguous": (
        "縫いグループ {label} の向きが曖昧です。意図した縫い目へパーツを近づけてください。"
    ),
    "sew_open_only_composite": "複合縫いループに結合できるのは開パスだけです。",
    "sew_cannot_align_closed": "閉じた縫いパスを整列できません。",
    "sew_already_sewn": "{name} にはすでに縫い合わせメッシュがあります。",
    "sew_ring_need_body": (
        "縫いグループ {label} には、閉じた RING パスと、ちょうど 2 つのボディ側開パスが必要です。"
    ),
    "sew_ring_cannot_pair": (
        "縫いグループ {label} の閉パス {count} 本をボディ側パスと対応付けできません。"
    ),
    "remesh_no_document": (
        "この衣服コレクションに型紙データがありません。"
        "CUTTINGCLOTH から裁断して型紙を持ち込んでください。"
    ),
    "remesh_no_panels": "保存された型紙にパネルがありません。",
    "remesh_missing_instances": (
        "保存型紙に次のパネルインスタンスがありません: {shown}"
    ),
    "cut_need_clothes_role": "作業コレクションの housei_role が clothes ではありません。",
    "remesh_pattern_missing": (
        "{name} に型紙座標がありません。読み込みと裁断からやり直してください。"
    ),
    "remesh_construction_missing": (
        "{name} に施工座標がありません。読み込みと裁断からやり直してください。"
    ),
    "prepare_mcp_running": "ZOZO MCP の設定がすでに実行中です。",
    "prepare_stopped": "ZOZO用準備作業 中断: {message}{suffix}",
    "prepare_failed": "ZOZO用準備作業 失敗: {message}{suffix}",
    "prepare_summary": (
        "{recut}ZOZO ステッチ {seams} 本を準備 "
        "(最も開いている縫い目 {gap_mm:.2f} mm){shell}{quality}"
    ),
    "prepare_recut": "パネル {n} 枚を再カット; ",
    "prepare_mcp_configuring": "{summary}; {mcp_note}; ZOZO MCP を :{port} で設定中...",
    "prepare_mcp_start_fail": (
        "{summary}; コピーはできましたが MCP を開始できませんでした: {exc}"
    ),
    "mcp_started": "MCP を :{port} で開始しました",
    "mcp_start_fail": (
        "ZOZO MCP を :{port} で開始できませんでした ({detail})。"
        "ZOZO Contact Solver を有効にし MCP Start してから、もう一度準備してください。"
    ),
    "mcp_setup_failed": "{summary}; ZOZO MCP 設定失敗: {detail}",
    "mcp_ready": (
        "{summary}; ZOZO MCP 準備完了 ({capture}){conn}。"
        "Transfer のあと Run Simulation を実行してください。"
    ),
    "mcp_response_failed": "{summary}; ZOZO MCP 応答失敗: {detail}",
    "prepared_default": "ZOZO 引き渡しメッシュを準備しました",
    "shell_unavailable": "shell-isect 利用不可",
    "shell_suffix": " [shell-isect {ver}]",
    "shell_suffix_missing": " [shell-isect 利用不可]",
    "shell_err_unavailable": (
        "エラー: 自己交差チェックを利用できません ({message}) [{suffix}]"
    ),
    "shell_err_failed": (
        "エラー: 自己交差チェックに失敗しました ({message}) [{suffix}]"
    ),
    "shell_err_pairs": (
        "エラー: 自己交差 (三角×三角の面ペア): {pipeline}"
        "{faces}{pairs} [{suffix}]"
    ),
    "shell_faces_range": " 布面=0..{last}",
    "shell_face_pairs": " 面ペア: {pairs}",
    "shell_mode_both": "布+ボディ",
    "shell_mode_cloth": "布のみ",
    "shell_summary": "shell-isect {version} ({mode}{crop}): {pipeline}",
    "shell_crop": ", ボディ {tested}/{total} 三角",
    "shell_pipeline_clean": "check1=0 (クリーン; 修正スキップ)",
    "shell_pipeline": "check1={before} fix={fix} check2={after}",
    "quality_summary": (
        "三角品質: {faces} 面, 最小面積 "
        "{area_min:.2e} m² (下限 {floor:.2e}), "
        "最短辺 {edge_mm:.3f} mm, "
        "最悪アスペクト {aspect:.2e}, "
        "下限未満 {failing}"
    ),
    "quality_error": (
        "エラー: ソルバに渡せないほど面積が小さい三角が {failing} 枚あります。"
        "最小 {area_min:.2e} m² (下限 {floor:.2e} m²)。"
        "シェル要素の剛性は 1/面積 に比例するため、これらは最初の求解で NaN になり"
        "フレーム 0 で止まります"
    ),
    "quality_worst": "。最悪: {shown}",
    "quality_worst_more": ", ... (他 {n} 件)",
    "quality_worst_item": (
        "(面 {index}: 面積 {area:.2e} m², 最短辺 {edge_mm:.4f} mm)"
    ),
    "zozo_need_clothes": "先に読み込み済みの衣服コレクションを選択してください。",
    "zozo_need_body": "ZOZO用準備作業の前にメッシュのボディを選択してください。",
    "zozo_no_seams": "衣服に縫い目がありません。",
    "zozo_nonfinite": "布に有限でない頂点座標があります。",
    "zozo_seam_mismatch": "縫い目ペアが現在のパネル頂点と一致しません。",
    "zozo_topo_changed": "ZOZO 引き渡しメッシュ作成中にトポロジが変わりました。",
    "zozo_stitch_lost": "緩い ZOZO ステッチ辺が作成中に失われました。",
    "zozo_no_body_export": "ZOZO ボディが書き出されていないため MCP を設定できません。",
    "zozo_stale_pitch": (
        "このビルドが使わない格子で切られたパネルが {n} 枚あり、誤ったピッチのまま"
        "渡ってしまいます: {shown} (現在は {mm:.0f} mm)。読み込みと裁断からやり直して、"
        "着付してから渡してください"
    ),
    "zozo_pattern_missing": (
        "{name} に有効な型紙座標がありません。型紙を読み込み直してください。"
    ),
}


def msg(key: str, **kwargs) -> str:
    """Format a user-facing status / soft-error string.

    Japanese is always preferred: Housei is the Japanese product, and the
    author writes Message-box text in Japanese. English is used only when a
    Japanese template is missing (should not happen for shipped keys).
    """
    template = _STATUS_JA.get(key) or _STATUS_EN.get(key) or key
    if kwargs:
        try:
            return template.format(**kwargs)
        except Exception:
            return template
    return template
