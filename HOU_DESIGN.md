# HOU Design

Status: shipped with Housei 0.2.x

## Role

`HOU` is the **single string custom property** that marks a Blender mesh object
as a Housei part and carries every metadata payload the pipeline needs.

Housei is **stateless** with respect to PLACED / PENDING / DONE. What matters
for a press is:

- which objects have a non-empty `HOU`;
- which of those sit in the Clothes **work collection**;
- which of those are **selected** (free) vs not (fixed anchors).

Collection membership alone does not define a part. `CUTTINGCLOTH_NNN` is only
a convenient place for Load output. A part may live anywhere as long as `HOU`
is present.

## Storage

| Item | Value |
|------|--------|
| Blender property name | `HOU` (object custom property) |
| Type | string |
| Content | one JSON object (UTF-8) |
| Schema stamp | `"schema": "housei-hou/1.0.0"` |
| Implementation | `hou.py` |

Binary blocks (per-vertex coordinates) are embedded as **base64-encoded NPY**
inside the JSON. There is no separate custom-property per field: one string
avoids a cloud of Blender IDs and keeps export/import simple.

Length is not limited by Housei; Blender must accept long custom property
strings (current builds do for megabyte-scale HOU).

## Recognition

```text
is_hou_part(obj)  ⇔  obj.type == "MESH" and HOU is a non-empty string
```

Objects without `HOU` are invisible to Load consumers, 裁断, Zero GRAVITY,
and ZOZO hand-off. Do not invent garment meaning from object names.

## JSON shape (1.0.0)

Keys written by `pack_part_hou` / `sync_hou_from_object`:

| Key | Type | Meaning |
|-----|------|---------|
| `schema` | string | always `housei-hou/1.0.0` |
| `role` | string | `part` for cloth panels |
| `panel_id` | string | pattern panel id (e.g. `OMOTE`, `SODE`) |
| `panel_label` | string | `#` label / update label |
| `panel_instance` | string | instance id after `@M` expand (e.g. `SODE:LEFT`) |
| `panel_index` | int | stable order index at load |
| `mirror_side` | string | mirror side when applicable |
| `ring_closed` | bool | RING construction |
| `mesh_spacing_m` | float | lattice pitch used to cut this mesh |
| `cut_scheme` | int | triangulation scheme version |
| `source_svg` | string | absolute path of the source PDF (historical name) |
| `collection` | string | last known collection name (hint only) |
| `sewing_labels` | string[] | edge attribute labels `sewing_*` present on the mesh |
| `pattern_position_npy_b64` | string | optional; Nx3 float64 pattern rest coords (NPY) |
| `construction_position_npy_b64` | string | optional; Nx3 construction coords (NPY) |

### Extra keys (optional, writer-defined)

Load and 裁断 may add further keys without a schema bump when they do not
change how existing readers interpret the part:

| Key | When |
|-----|------|
| `source_collection_role` | Load: `"cutting"` |
| `sewing_groups` | Load: full pattern `sewing_groups` map (for multi-panel readiness) |
| `source_object` | 裁断: name of the object copied from |
| `work_collection` | 裁断: Clothes collection name |

Unknown keys must be preserved by round-trips that only rewrite known fields.

## NPY embedding

```text
JSON string  ←  base64( ascii )  ←  bytes of numpy.save(.npy)
```

- Arrays are contiguous; pattern / construction blocks are `float64` with shape
  `(n_vertices, 3)`.
- Decode with `numpy.load(..., allow_pickle=False)` only.
- Vertex count must match the mesh when both are present.

Helpers: `hou.npy_to_b64`, `hou.b64_to_npy`.

## Relationship to mesh attributes and other custom props

While the solver and sewing still read Blender mesh attributes and some
`housei_*` custom properties for speed and compatibility, **HOU is the
authoritative, portable bag of part metadata**. After topology or pose-changing
operations that Housei owns, call `sync_hou_from_object` so HOU matches the
mesh again.

Parallel sources that still exist on the object (not a second public API):

- mesh attributes: `housei_pattern_position`, `housei_construction_position`,
  `sewing_<label>`, grainline, shortenable, etc.;
- object props: `housei_panel_id`, `housei_role`, `housei_kitsuke_locked`, …;
- collection props: `housei_document_json` (full pattern for remesh / multi-panel
  sewing group membership).

## Workflow contract

1. **External load** (Katagami, MCP, …) — builds meshes, writes `housei_*` +
   mesh attributes, then `HOU` / `sync_hou_from_object` (including
   `sewing_groups` from the pattern JSON when available). Housei does not
   load PDF.
2. **Cut out (裁断)** — deep-copies selected HOU parts into the Clothes work
   collection, lifts Z by 30 cm, rewrites HOU with `source_object` /
   `work_collection`. Source objects are unchanged.
3. **Zero GRAVITY** — selection in Clothes deforms; non-selected HOU parts pin
   (`housei_kitsuke_locked`). Sewing uses mesh edge labels and the stored
   pattern document to **skip incomplete multi-panel groups** (e.g. armhole
   letter that still needs SODE). After a successful solve, HOU is synced again.
4. **Prepare for ZOZO** — reads the Clothes work collection only.

## Multi-panel sewing readiness

Pattern `sewing_groups` lists which panel ids participate in each letter.
If Clothes holds only a subset of those panels, that letter is **not** sewn
yet (partial assembly is intentional). Side seams that only need the panels
already present still resolve.

## Non-goals

- Inferring panel role from object or collection names.
- Treating missing `HOU` as “maybe a part”.
- Requiring `CUTTINGCLOTH_*` membership for validity.
- Embedding world pose or solver velocity as mandatory HOU fields (Object Mode
  transform is the pose; rest state is pattern NPY / mesh attributes).

## Versioning

- Bump `housei-hou/x.y.z` when a reader **must** change behavior.
- Additive optional keys may ship without a schema bump if older readers ignore
  them safely.
- Implementation lives in `hou.py`; this document is the contract.

## Related

- `README.md` — product workflow
- `SVG_TO_JSON_SPEC.md` — pattern JSON before mesh/HOU
- `KITSUKE_DESIGN.md` — sewing and solver hand-off
- `PPF_ZERO_GRAVITY_DESIGN.md` — Zero GRAVITY job
- `SEWING_PLAN_DESIGN.md` — persisted seam pairs for external exporters
