# Seam Boundary Layer (Paving Band) — removed in 0.14.6

Status: removed. Load no longer builds a band. This file records what the band
was and why it went, because the reason it went is a lesson about where mesh
structure is allowed to differ from the rest of the cloth: nowhere the eye can
see it.

Related: `GRAINLINE_DESIGN.md`, `KITSUKE_DESIGN.md`.

## What it was

For each sewing-relevant boundary chain, Load offset the outline 10 mm inward
(`SEAM_BAND_WIDTH_M`) and paved a one-layer strip between the two rows:

```
   E0——E1——E2——E3——E4——E5     Edge vertices (outer stitch row)
   |   |   |   |   |   |
   P0——P1——P2——P3——P4——P5     Proximity vertices (band)
```

with a three-way vertex classification (`housei_vertex_kind`: N/E/P) intended
for a future clearance model (band at near-contact distance A, interior at B),
and `housei_shortenable` on the stitch row for a gather term. None of that was
ever consumed — this document's own status line said so from the day it was
written.

## Why it went

The band was the least uniform mesh in the garment, and it sat exactly on the
seams — the one place uniformity matters most for the finish:

- Band cells were 5 × 10 mm, twice the interior triangle size, in a coherent
  strip along every sewing edge.
- The interior lattice was excluded to 10 mm + a 0.12-pitch margin, so the
  strip between the P row and the ragged lattice frontier was filled by
  unconstrained Delaunay: triangle heights from 0.6 mm slivers up to 7 mm,
  side by side. On a straight side seam the frontier runs parallel to the
  seam, so the bad case was coherent — a column of slivers the full length of
  the seam, which is what a "dirty seam" looked like in the ZOZO solve.
- The band's constrained spokes crossed other constraints at corners and the
  CDT minted micro-vertices there (22 µm shortest edge on the reference cut).

The simulation reads triangles, world positions, pattern coordinates, seams
and rest lengths; nothing reads the band. The finish paid for structure
nothing used.

## What replaced it

One uniform equilateral triangular lattice per panel (`_interior_grid`), the
same construction `backends/ppf/remesh.py` uses, because its Delaunay triangulation is
already near-equilateral:

- rows √3/2 · pitch apart, alternate rows offset half a pitch, anchored to the
  pattern page;
- every lattice point holds **half a pitch clear** of the outline and of the
  fold row, so the CDT bridges boundary to lattice with well-shaped triangles
  and there is no transition strip;
- the fold row is the only interior constraint left, so there are no crossing
  constraints for the CDT to mint micro-vertices from.

Measured on the reference garment against the band-and-square cut: worst
aspect 0.0014 → 0.28 (equilateral = 0.866), triangles under 0.2 aspect 1.1% →
**zero**, shortest edge 30 µm → 2.5 mm, rest-area spread (CV) 27% → 8%,
seam-adjacent spread 68% → 25%. Nothing near a seam is meshed differently
from anything else, which is the point: a sewn seam has no seam-shaped
topology for the drape to trace.

`housei_vertex_kind` is still written (N/E; E marks the stitch row), and
`housei_shortenable` still marks the outer sewing row. Value 2 (P) is retired
and must not be reused.

## The seam-count doubling bug, fixed in the same pass

`compute_seam_count_overrides` speaks in mesh-chain edges, but the builder
used to apply a forced count to **every authored segment** carrying the label.
A label spanning two segments (the reference garment's side seams, label B)
got twice the asked-for count; a run ending on the fold got doubled again by
its mirror image. The next override pass measured the overshoot as the new
longer side and forced the partner up to match: 110 → 220 → 440 edges,
divergent, and the two sides never agreed — so the stitches paired by
arc-walk ladder instead of 1:1, which is what a puckered seam line is.

Now `_forced_segment_counts` distributes the chain count over the run's
segments by arc length (fold-adjacent runs take half; the mirror carries the
rest), and the override pass rounds fold-merged chains to the even counts they
can actually produce. One adapt pass converges; a second pass is a no-op —
which is what `KITSUKE_DESIGN.md` promised all along.

Panels cut by an old scheme are recognised by `housei_cut_scheme` (absent or
≠ 3) and re-cut once by the ZOZO hand-off's stage 1, pose transferred — clean
natural counts first, count matching after, in that order, because a count
target measured on a stale mesh inherits the stale mesh's history
(`ZOZO_HANDOFF_DESIGN.md` tells that story).
