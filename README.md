# 縫製 (Housei)

Housei（縫製）は、型紙ベースの衣服を組み立て・段階着付するための実験的な
Blender 拡張です。Illustrator の型紙が正本であり、Blender メッシュは置き換え
可能な物理的実体です。

**Housei は yohsai（洋裁）とは独立した日本語版プロダクト**です。識別子・
データ属性・JSON schema はすべて `housei` 名前空間に属し、yohsai とデータ
互換はありません。

Only explicit commands and documented data have meaning. Housei must not infer
garment-part semantics, intended shape, or Body-relative placement from panel
names, seam layout, or visual similarity.

## 方針 (policy)

1. **日本語が正文のメッセージ** — N パネルのメッセージ欄（状態・エラー）は
   作者が日本語で直接書く。英語のメッセージ文字列は補助であり、正文ではない。
2. **ドキュメントは英語でもよい** — 設計メモや README の技術記述は英語で
   よい。用語の正は日本語メッセージと型紙注釈の契約にある。
3. **明示コマンドのみ意味を持つ** — パーツ名、縫い目レイアウト、見た目の
   類似から衣服部位や配置を推測しない（yohsai と同じ契約）。
4. **独立リポジトリ** — yohsai からのコピーを起点とするが、履歴・リモート・
   パッケージ ID は別物。併存インストール可能（`id = "housei"`）。

## Workflow

A **part** is any mesh with a non-empty `HOU` string custom property (JSON).
Collection membership is not required. `CUTTINGCLOTH_NNN` is only a convenient
place for Load; `Clothes` is the **work collection** (e.g. tops vs skirt later).

The N-panel contains these inputs:

- `Pattern Path`: the Illustrator PDF (Load still available while load is in-tree);
- `Clothes`: work collection for 裁断 / Zero GRAVITY / ZOZO export;
- `Body`: the fixed collision mesh the solver collides cloth against.

The normal operation order is:

1. `Load` creates HOU parts under a new `CUTTINGCLOTH_NNN` data collection.
2. Select HOU part(s) (anywhere) and press **Cut out** (`裁断`): copies go into
   the Clothes work collection and are lifted **Z + 30 cm** for easy grabbing.
   Clothes is created if missing.
3. Place the copies in Object Mode.
4. Select the Body. Select the parts that should **deform** this press, then
   press `Zero GRAVITY`. Non-selected HOU parts in Clothes stay **fixed
   anchors** (same pin as the old Existing Lock / DONE path). Nothing selected
   in Clothes → immediate no-op.
5. Delete unwanted copies from Clothes and cut out again if needed.
6. For ZOZO simulation, use `Prepare for ZOZO` on the Clothes collection.

**Removed:** Update, Select Lock, Existing Lock, and PLACED/PENDING/DONE state
driving. Incremental sewing is **非選択固定** (selection free, others locked).

Part metadata lives in the object custom property **`HOU`** (one JSON string,
including base64 NPY blocks). See `HOU_DESIGN.md`.

## Interface language

- **Message box**: always Japanese (author-written). See `i18n.msg`.
- **Panel labels**: English identifiers, with Japanese translations via
  `bpy.app.translations` when Blender's interface language is Japanese
  (`Edit > Preferences > Interface > Translation` → Japanese, enable
  Interface). Product name: Housei → 縫製.
- This README keeps English operator names (`Load`, `Zero GRAVITY`, …) as
  stable identifiers; Japanese UI labels are in `i18n.translations_dict`.

## Pattern input

The parser accepts a one-page Illustrator PDF. Each closed panel must contain a
unique `#` label. Page vertical is warp and page horizontal is weft.
Illustrator layer and sublayer names are ignored; standard PDF page content is
read as one flattened drawing. A PDF text object whose first non-whitespace
characters are `//` is a comment and is ignored in full.

Supported annotations are:

- a single letter for a sewing group;
- `@W` for a fold edge;
- `@M` for authored-left and mirrored-right instances;
- two `RING` edges plus `@TOP` for a welded ring construction.

No undocumented annotation has implied behavior.

Load samples a uniform triangular lattice at a 5 mm pitch in pattern-page
coordinates and uses its triangles as the Blender and collision proxy. Pattern
coordinates retain the material rest state.

## Automatic Sewing

Zero GRAVITY runs Sewing from the world-space positions of the separate
source parts before a new pending stage. Sewing orders
marked boundary paths, matches them by normalized authored distance, and stores
cross-panel pairs in a transient preview.

Load records every part's initial Object Mode transform. Automatic Sewing ignores
parts still in `PLACED`, includes `PENDING` parts as the new work, and retains
`DONE` parts as connectivity anchors. Unresolvable paths stay pending; when a
moved part completes one side of a multipart sewing group, that side is sewn
without waiting for later parts.

The preview is a visual connectivity record. It does not define a replacement
initial cloth shape. Body geometry is not used by Sewing.

## Zero GRAVITY

Zero GRAVITY starts from the positioned source-panel vertices and closes every
seam in one job. The solve runs in the ZOZO Contact Solver's own tree as a child
process, so its CUDA backend never enters Blender's address space and a solver
crash costs the press rather than the session. No solver or iteration setup is
required.

The panels start flat and outside the Body, which is what makes the scene
intersection-free at the start -- the state a barrier solver requires. Because
they are flat, their placed position is also their stress-free shape, so a press
always sews from flat: pressing again re-sews rather than advancing, and never
mistakes stretched cloth for the pattern. Gravity is zero and the Body is a
static collider with no degrees of freedom.

A press is a job of a few seconds rather than a button that answers within a
frame. `PPF_ZERO_GRAVITY_DESIGN.md` covers the solver hand-off, the mesh rebuild
it needs, and what is not finished.

After the solve, positions are scattered back to the separate part objects.
Object translation and rotation are supported between presses; scaling and
vertex-count changes are rejected. A result is discarded only when it is
non-finite, or when it moves a vertex further than the whole Body -- that is a
failure to locate a vertex, not cloth.

On a successful press, pending parts become `DONE` without being relocked, so
Zero GRAVITY may repeat immediately. Moving another placed part starts a new
automatic Sewing stage. A later Load or switching Existing Lock on locks done
parts while retaining seam connectivity.

Nothing is kept between presses beyond Blender's own mesh state, so Undo and
Redo behave as they do for any mesh edit.

## Prepare for ZOZO

`Prepare for ZOZO` hands the garment over exactly as it stands. It does not
sew, open, weld or clear anything, and it does not modify, join, rename, hide,
or move the source Housei parts. Pipeline:

1. Build internal ZOZO **cloth** copy: the current panel positions, the seams as
   stitch edges, the pattern coordinates as UVs.
2. Build internal ZOZO **body** copy (always; ZOZO MCP / Transfer need it).
3. **shell-isect check** — default **cloth-only** (fast, practical). Optional
   panel toggle **Shell-isect vs Body** enables the full cloth+body twin
   (cloth–cloth and cloth–body; body–body skipped). High-poly bodies can take
   many minutes in twin mode; the body path remains in code when you need it.
4. **local fix** when pairs remain (cloth-only DLL fix, or body-aware push when
   twin mode is on). Topology and body verts are never edited.
5. **shell-isect check** again (same mode as step 3).
6. **NG** — write the error (pair counts + face-pair indices + shell-isect
   version / mode) to the status line, keep cloth/body copies for inspection,
   **do not** configure ZOZO. Settle with Zero GRAVITY and press Prepare
   again.
7. **PASS** (zero pairs) — configure ZOZO Contact Solver over MCP and leave the
   hand-off ready for Transfer / Run. Status messages end with
   `[shell-isect x.y.z cloth-only]` or `[... cloth+body]`.

Earlier versions pushed every seam apart into layers here, because ZOZO's own
add-on closes a seam with a loose stitch edge and a loose stitch edge needs a
positive contact gap. That was scaffolding for handing over a garment that was
not sewn yet. Zero GRAVITY closes the seams before this button is pressed, so
the scaffolding is gone and the cloth goes over untouched. The status line
reports the widest seam still open on what was handed over. The Housei parts
remain the authoritative state.

On PASS the button configures ZOZO through `http://localhost:9633/mcp`. It
replaces only the two groups named for the selected Housei collection, creates
a SHELL and STATIC group, uses absolute 1 mm contact gaps, preserves the
initial fitted shape as the bending rest shape, and sets conservative damping
and five inactive-momentum frames. **Frame range:** Prepare takes the intended
simulation length from the Blender scene range and, when active, the preview
range / ZOZO simulation frames (whichever is longer), writes that
`frame_count` to ZOZO, and **expands `scene.frame_end`** so Run/Fetch can
actually write every PC2 sample (a common failure was UI showing 250 frames
while Blender `frame_end` stayed at ~22 and only ~22 frames landed in the
cache). If the Body copy deforms through an Armature, Lattice, Mesh Deform,
shape keys, or drivers, the MCP client also records its deformation cache.

MCP setup runs outside Blender's main thread so ZOZO can safely execute its
queued Blender operations. Housei never starts Transfer or the simulation
automatically; inspect the groups, then use ZOZO's `Transfer` and
`Run Simulation` controls. `shell_isect.dll` lives under `bin/` (or
`SHELL_ISECT_DLL`).

## Update

Update rereads the same PDF and recuts the selected Clothes collection. Stable
`#` labels and mirror instances identify corresponding parts. Existing object
identity, transforms, materials, and collection ownership remain.

If sewing membership changes, the next eligible Zero GRAVITY press rebuilds
Sewing automatically. Pattern topology and material rest dimensions always come from
the revised PDF.

## Silhouette utility

Character silhouettes are exported separately with
`UTIL/silhouette_export.py`. See `UTIL/README.md`.

## Documentation

- `SVG_TO_JSON_SPEC.md`: input, JSON, Load, automatic Sewing, and Update contract;
- `KITSUKE_DESIGN.md`: Sewing, panel state, and what the ZOZO hand-off may do;
- `PPF_ZERO_GRAVITY_DESIGN.md`: the ZOZO Contact Solver hand-off;
- `ZOZO_HANDOFF_DESIGN.md`: what Prepare for ZOZO re-cuts and checks, and why;
- `GRAINLINE_DESIGN.md`: grain-aligned mesh and material mapping;
- `SEAM_BOUNDARY_LAYER_DESIGN.md`: why the seam paving band was removed for
  the uniform lattice;
- `DEVELOPMENT_NOTES.md`: architecture summary and build notes.

## Platforms

Housei ships for **Windows x64** only (`housei-<version>-windows_x64.zip`).
The package bundles `shell_isect.dll` and the licensed `vcomp140.dll` OpenMP
runtime it needs. Zero GRAVITY additionally requires a built ZOZO Contact Solver
checkout; set its path in Preferences > Add-ons > Housei.

## License

Housei is licensed under GNU GPL v3.0 or later. Third-party boundaries and
attribution are listed in `THIRD_PARTY_NOTICES.md`.
