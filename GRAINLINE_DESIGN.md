# Housei Grainline Mesh Design

Status: current lattice mesh contract

The PDF page defines warp vertically and weft horizontally. Panels are cut on
a uniform equilateral triangular lattice (`MESH_SPACING_M` = 5 mm, cut scheme
3 in `mesh_loader.py`), triangulated for Blender rendering and collision, with
pattern coordinates and grainline attributes retained on the mesh. Earlier cut
schemes used a square axis-aligned lattice; `housei_cut_scheme` records which
builder cut a part, and the ZOZO hand-off refuses a stale scheme.

Every panel is cut at that one pitch. It used to be 10 mm, with 5 mm kept for
panels whose shorter pattern-page side fell under 5 cm, and the reason for
holding the large panels coarse was a solver Housei no longer has. The reason
for going fine is under the arm: two panels have to slide past each other there,
and a 10 mm facet stands too far off the surface it approximates for them to
pass. The standoff is the chord's sagitta and falls with the pitch squared.

On the reference pattern the change costs 8,485 vertices -> 30,826, and the
triangles come out no worse: worst aspect 3.3e-4 -> 1.1e-3, smallest rest area
88x above the ZOZO floor (it was 161x), nothing degenerate. The shortest edge
does drop, 121 um -> 22 um, from `delaunay_2d_cdt` intersection vertices rather
than from the lattice; `_lattice_minimum` records why that is left alone.

Mixed-resolution seams are still handled — a garment cut before this change
keeps its 10 mm panels until it is re-cut, and `part_spacing_m` answers with the
pitch a part actually has. Such seams equalize to the coarser side's vertex
count before 1:1 pairing (the fine boundary sparsifies; interior stays fine).
Prepare for ZOZO refuses a garment still on the old pitch rather than converting
half of it on the way out; a fresh Cut out then Zero GRAVITY is the repair.

Stored attributes include `housei_pattern_position`,
`housei_grainline_family`, `housei_grainline_quad`, sewing membership, and fold
membership. Edge-family values remain proxy, warp, weft, and transition.

The seam paving band is gone: sewing-boundary vertices are kind **E** and the
interior kind **N** (`housei_vertex_kind`); kind **P** belonged to the removed
band and is retired, not reused. Sewing-row edges are still marked
`housei_shortenable` for future gather absorption. See
`SEAM_BOUNDARY_LAYER_DESIGN.md` for why the band was removed.

Nothing consumes the grainline metric as material any more: the square-lattice
solver that read warp/weft rest lengths and proxy-square shear is gone, and
Zero GRAVITY reads the panels through Blender's own triangulation, because the
quad map describes a material metric rather than a surface. The attributes
remain recorded pattern data.
