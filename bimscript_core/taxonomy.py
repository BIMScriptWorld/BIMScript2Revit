# -*- coding: utf-8 -*-
"""Frozen BIMScript material/condition taxonomies and display colors.

Mirrors src/training/config.py (data.normalization_values.materials/conditions,
2026-07-04 full-corpus survey — append only, never reorder, "unknown" last) and
misc/paper_figures/render_floorplan.py MATERIAL_COLORS, so Revit shaded views
match the paper figures. This module must stay dependency-free and IronPython
2.7 compatible: it is imported inside Revit's script engine.
"""

MATERIALS = [
    "aluminum", "brick", "composite", "concrete", "glass", "metal",
    "painted_plaster", "pvc", "stone", "tile", "wallpaper", "wood",
    "wood_paneling", "unknown",
]

CONDITIONS = ["new", "good", "worn", "damaged", "unknown"]

# Hex colors as in the paper's floor-plan renderer.
MATERIAL_COLORS_HEX = {
    "aluminum": "#8c9db5",
    "brick": "#b5651d",
    "composite": "#9467bd",
    "concrete": "#7f7f7f",
    "glass": "#17becf",
    "metal": "#4d5866",
    "painted_plaster": "#e8b64f",
    "pvc": "#e377c2",
    "stone": "#8c8c5a",
    "tile": "#2ca02c",
    "wallpaper": "#d62728",
    "wood": "#a0642d",
    "wood_paneling": "#6b3f1d",
    "unknown": "#c7c7c7",
}


def material_rgb(name):
    """Return (r, g, b) ints 0-255 for a material name (unknown-safe)."""
    hex_color = MATERIAL_COLORS_HEX.get(name, MATERIAL_COLORS_HEX["unknown"])
    hex_color = hex_color.lstrip("#")
    return (int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16))


def normalize_material(name):
    """Map a raw material string onto the frozen vocabulary.

    Returns (canonical_name, was_in_vocab). Absent/None maps to "unknown"
    without counting as out-of-vocab (layout-only programs carry no material).
    """
    if name is None or str(name).strip() == "":
        return "unknown", True
    cleaned = str(name).strip().lower()
    if cleaned in MATERIALS:
        return cleaned, True
    return "unknown", False


def normalize_condition(name):
    """Same contract as normalize_material, for conditions."""
    if name is None or str(name).strip() == "":
        return "unknown", True
    cleaned = str(name).strip().lower()
    if cleaned in CONDITIONS:
        return cleaned, True
    return "unknown", False
