# Housei utility scripts

These scripts are occasional preparation tools, not part of Housei's everyday
N-panel workflow.

## Silhouette export

`silhouette_export.py` exports the active character mesh as two
Adobe Illustrator-readable SVG files:

- `<Object>_shadow_xz.svg`: front/back projection;
- `<Object>_shadow_yz.svg`: side projection.

The script evaluates modifiers and Object Mode transforms. SVG dimensions are
millimeters and respect Blender's Scene Unit Scale. Normally this preparation is
needed only once for each character.

### Run in Blender

1. Select the character mesh and ensure it is the active object.
2. Open Blender's **Scripting** workspace.
3. In the Text Editor, choose **Open** and open `UTIL/silhouette_export.py`.
4. Optional: set `OUTPUT_DIRECTORY` near the top of the script. Blender-relative
   paths such as `//silhouettes` are supported.
5. Press **Run Script**.

When `OUTPUT_DIRECTORY` is empty, files are written beside the saved `.blend`
file. For an unsaved file, they are written to the user's home directory. A
completion popup shows the output folder; complete paths are also printed in
the system console.

The active object must be a Mesh. If the evaluated mesh has no detectable
silhouette edges, the script stops with an error and writes no replacement for
that projection.
