# Sewing Plan Design

Status: shipped with Housei 0.5.0

## Role

`housei_sewing_plan_json` is a **collection custom property** holding the
verified sewing plan of the garment in a Clothes work collection: which
vertex is sewn to which, per sewing label. It is written on every successful
**Zero GRAVITY** press and never read back by Housei itself.

It exists for one audience: an **external exporter** (another Blender
extension, targeting another simulator or file format) that wants to read the
sewn garment from the `.blend` alone. Every other piece of the garment is
already persisted data; the seam pairing was the one thing that existed only
as Housei code (`build_sewing_plan`: ring pairing, composite loops,
sewing-group completeness). Reimplementing that logic in a second reader is
how two tools end up sewing two different garments, so the resolved result is
written down instead.

## What a reader needs, and where it lives

| Garment piece | Where |
|---|---|
| Which objects are panels, their metadata | `HOU` object property (`HOU_DESIGN.md`) |
| Draped shape (as dressed) | mesh vertices + `obj.matrix_world` |
| Flat pattern coordinates | `housei_pattern_position` POINT attribute (also NPY inside `HOU`) |
| Seam pairs | **this property** |
| Body / collider | the reader's own selection UI |

A reader needs **no Housei code and no Python import of Housei**. Extensions
must not import each other (`bl_ext.<repo>.<pkg>` paths depend on the local
repository name); the `.blend` data is the whole interface.

## Storage

| Item | Value |
|------|-------|
| Property name | `housei_sewing_plan_json` (collection custom property) |
| Collection | the Clothes work collection (`housei_role == "clothes"`) |
| Type | string, one JSON object (UTF-8) |
| Schema stamp | `"schema": "housei-sewing-plan/1.0.0"` |
| Writer | Zero GRAVITY success path (`ui.py`), built in `sewing_plan.py` |

## JSON shape (1.0.0)

| Key | Type | Meaning |
|-----|------|---------|
| `schema` | string | always `housei-sewing-plan/1.0.0` |
| `collection` | string | Clothes collection name at write time |
| `labels` | string[] | sewing labels resolved by this plan, sorted |
| `parts` | object[] | participating panels, in Housei's panel order (see below) |
| `pairs` | object | `{label: [[slot_a, vertex_a, slot_b, vertex_b], ...]}` |
| `pair_count` | int | total pairs across all labels |

Each entry of `parts`:

| Key | Type | Meaning |
|-----|------|---------|
| `object` | string | Blender object name (primary locator; unique in a `.blend`) |
| `instance` | string | `housei_panel_instance` (stable semantic id, e.g. `SODE:LEFT`) |
| `panel_id` | string | pattern panel id (e.g. `OMOTE`) |
| `panel_index` | int | Housei's stable panel order index |
| `vertices` | int | fingerprint: vertex count of the mesh the plan was built on |
| `cut_scheme` | int | fingerprint: `housei_cut_scheme` at write time |
| `mesh_spacing_m` | float | fingerprint: lattice pitch at write time |

Pair encoding: `slot` is an index into the `parts` array; `vertex` is a
vertex index **local to that part object's mesh**. A reader therefore never
needs to know how Housei concatenates panels into one block.

## Reader rules (the contract)

1. Find the Clothes collection (`housei_role == "clothes"`); read and parse
   the property. Missing property → the garment was never sewn by a Housei
   build that writes plans; **refuse** and ask the operator to press
   Zero GRAVITY.
2. Locate each `parts[i]` by `object` name among the collection's objects.
3. Verify every fingerprint: `len(mesh.vertices) == vertices`, `cut_scheme`
   equals `housei_cut_scheme`, `mesh_spacing_m` matches within tolerance.
   **Any mismatch → refuse** with "run Zero GRAVITY again in Housei". Vertex
   indices do not survive a re-cut; a reader repairs nothing, ever (the same
   stance as Housei's own stale-pitch refusal).
4. Read positions as `matrix_world @ vertex.co`; read the flat pattern from
   `housei_pattern_position`.
5. Ignore unknown JSON keys (additive versioning, same rule as `HOU`).

## Writer rules

- The payload is built from the exact pre-solve state Zero GRAVITY sews
  (after seam-count adaptation, before the solver runs), and **written only
  after the press succeeds**. A failed press leaves the previous plan
  untouched.
- A plan-write failure never fails the press; it is reported in the status
  line (`plan_save_failed`).
- Re-cuts do **not** delete the property. The fingerprints are the
  invalidation mechanism; keeping the writer additive is the point.

## Non-goals

- Housei reading the plan back. The ZOZO hand-off keeps deriving its pairs
  live from `sewing_*` edge labels, exactly as before this property existed.
- Embedding positions, velocities or the Body in the plan. The mesh is the
  pose; the Body is the reader's input.
- Cross-`.blend` portability of the plan. Vertex indices are meaningful only
  next to the meshes they were written beside.

## Versioning

- Bump `housei-sewing-plan/x.y.z` when a reader **must** change behavior.
- Additive optional keys may ship without a schema bump if older readers
  ignore them safely.
- Implementation lives in `sewing_plan.py`; this document is the contract.

## Related

- `HOU_DESIGN.md` — per-part metadata contract
- `KITSUKE_DESIGN.md` — sewing and solver hand-off
- `PPF_ZERO_GRAVITY_DESIGN.md` — the Zero GRAVITY job that triggers the write
