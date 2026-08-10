# Housei Development Notes

Status: independent Japanese product (縫製), started from a yohsai 0.14.7
source snapshot. Package version is `0.4.0` under `id = "housei"`. Not binary-
or data-compatible with yohsai. PDF load lives in **Katagami**, not here.

## Architecture

- A part is any mesh with `HOU` (JSON string custom property). Stateless: HOU
  holds metadata + base64 NPY payloads; no PLACED/PENDING/DONE machine.
- Pattern supply is external (Katagami PDF, MCP, etc.). Housei starts at
  Cut out (`裁断`): copies selected HOU parts into the Clothes work collection
  (role `clothes`) and lifts Z by 0.30 m.
- Zero GRAVITY: selection in Clothes deforms; non-selected HOU parts pin via
  `housei_kitsuke_locked` (same pin path as the old Existing Lock / DONE).
  Empty selection → no-op. Sewing rebuilds when `housei_sewing_verified` is false.
- Each press first equalizes every seam's two sides to matching vertex
  counts, recutting only the shorter side from the pattern stored on the
  collection. Matched sides pair 1:1 so the longer edge gathers. A recut changes
  topology and therefore forces a Sewing rebuild.
- Zero GRAVITY closes every seam in one
  ZOZO Contact Solver job (`zero_gravity.py` -> `backend_ppf.py` ->
  `backends/ppf/driver.py`), run as a child process in the solver's own tree so
  its CUDA backend stays out of Blender. The Body goes over as a static collider
  and the panels start flat, which is what makes the scene intersection-free at
  the start and the step cost small. A press sews from flat, so pressing again
  re-sews rather than advancing.
- Zero GRAVITY talks to the solver through the backend contract in
  `solver_backend.py`; nothing under `backends/` is ever imported by Blender.
- Prepare for ZOZO (`ZOZO用準備作業`) re-cuts, copies, and checks, in that
  order: `remesh_with_seam_counts` first so what goes over is a panel the
  current triangulation built rather than whichever one was in the scene, then
  copies of those panels, their seams as stitch edges, their pattern
  coordinates as UVs, and a copy of the Body, then shell-isect, then a
  triangle-quality gate, then MCP configuration. It still does not sew, open,
  weld or clear anything -- Zero GRAVITY has already closed the seams, and
  moving good cloth on the way out could only make it worse. See
  `ZOZO_HANDOFF_DESIGN.md` for the failure the two checks exist for.
- One lattice pitch, 5 mm, for every panel (`MESH_SPACING_M`). The old rule was
  10 mm with 5 mm for panels under 5 cm on the short side, justified by a full
  5 mm mesh breaking the square-lattice solve — that solver went in 0.13.0 and
  took the justification with it. What replaced it points the other way: under
  the arm two panels have to slide past each other, and a 10 mm facet stands too
  high off the surface it approximates for them to pass. `part_spacing_m` still
  answers with whatever pitch a part was actually cut at, so a garment carried
  across the change keeps its 10 mm panels until Update re-cuts it, and Prepare
  for ZOZO stops rather than hand over a half-converted garment.
- Housei had a cloth solver of its own until 0.13.0: a native square-lattice
  runtime behind a Normal GRAVITY button, with a CUDA path, an OpenMP colouring
  and an undoable session. It is gone, along with `native/`, `native_solver.py`
  and `SOLVER_DESIGN.md`. Closing a seam by positional projection ties stiffness
  to the iteration count, so buying speed there costs correctness; an implicit
  seam force solved inside a Newton step gives the converged answer at any step
  count. Once Zero GRAVITY sewed a whole garment in seconds, the second solver
  had no job left that the first did not do better, and Zero GRAVITY was
  carrying code whose only purpose was to defend against it.
- Only a non-finite or implausibly large returned state causes a press to be
  discarded; the cloth is then left unchanged.
- Update recuts meshes from stable panel labels.
- `i18n.py` holds the N-panel's Japanese translation dictionary, registered
  under the add-on package name. Operator button labels resolve in the
  `Operator` context and panel headings, property names, and plain labels in
  the default `*` context, so a shared string is registered under both. English
  source strings stay the identifiers; Blender's interface language selects the
  translation.

Only explicit requirements authorize behavior. Do not infer shape, fit, volume,
or Body-relative placement from names, topology, screenshots, or prior work.

## Build

The extension version is defined in `blender_manifest.toml`.

```powershell
blender.exe --command extension build --source-dir . --output-dir .\dist
```

`bin/` ships `shell_isect.dll` and the licensed `vcomp140.dll` OpenMP runtime it
needs. Neither is built from this repository.

There is no broad product test suite. Write new tests against the code as it is
when they are needed, and delete them again rather than let them drift.

`blender_manifest.toml` `[build] paths` is the authoritative file list: current
source, documentation, and the shipped DLLs under `bin/`. Wheels come from the
separate `wheels` key. Build directories, caches, temporary files, local PDFs,
and earlier ZIPs are excluded. Deleting a documentation file requires removing
its `paths` entry too, or the build fails on the missing path.
