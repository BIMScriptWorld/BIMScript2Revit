# -*- coding: utf-8 -*-
"""Build-plan intermediate representation: parsed entities -> what to build.

Every piece of validation and geometry interpretation lives HERE, on the
Linux-testable side of the boundary; the pyRevit adapter is a dumb executor of
the returned BuildPlan. Units stay meters throughout — the adapter converts to
Revit internal units at the last moment.

Geometry conventions (verified against corpus files, see the design spec):
- wall endpoints a/b are floor-line points, a_z is the wall base elevation;
- opening position_* is the opening CENTER, so sill = position_z - height/2;
- wall1_id == -1 means the opening has a single host wall;
- thickness == 0.0 is common in GT -> DEFAULT_THICKNESS_M applies.

IronPython 2.7 compatible (no dataclasses / f-strings).
"""

from bimscript_core import taxonomy

DEFAULT_THICKNESS_M = 0.1
MIN_WALL_LENGTH_M = 1e-3
MIN_OPENING_SIZE_M = 1e-3


class WallSpec(object):
    def __init__(self, entity_id, a, b, height, thickness, material, condition):
        self.entity_id = entity_id
        self.a = a                    # (x, y, z) meters
        self.b = b                    # (x, y, z) meters
        self.height = height          # meters
        self.thickness = thickness    # meters, defaulted if source was ~0
        self.material = material      # canonical taxonomy name
        self.condition = condition    # canonical taxonomy name

    def as_dict(self):
        return {
            "id": self.entity_id, "a": list(self.a), "b": list(self.b),
            "height": self.height, "thickness": self.thickness,
            "material": self.material, "condition": self.condition,
        }


class OpeningSpec(object):
    def __init__(self, kind, entity_id, host_id, center_xy, sill, width, height,
                 material, condition):
        self.kind = kind              # "door" | "window"
        self.entity_id = entity_id
        self.host_id = host_id        # WallSpec.entity_id of the host
        self.center_xy = center_xy    # (x, y) meters
        self.sill = sill              # meters above wall base (>= 0)
        self.width = width
        self.height = height
        self.material = material
        self.condition = condition

    def as_dict(self):
        return {
            "kind": self.kind, "id": self.entity_id, "host_id": self.host_id,
            "center_xy": list(self.center_xy), "sill": self.sill,
            "width": self.width, "height": self.height,
            "material": self.material, "condition": self.condition,
        }


class BuildPlan(object):
    def __init__(self, walls, openings, materials_used, warnings):
        self.walls = walls
        self.openings = openings
        self.materials_used = materials_used  # sorted list of canonical names
        self.warnings = warnings

    def as_dict(self):
        return {
            "walls": [w.as_dict() for w in self.walls],
            "openings": [o.as_dict() for o in self.openings],
            "materials_used": list(self.materials_used),
            "warnings": list(self.warnings),
        }


def _norm_material_condition(params, label, warnings):
    material, mat_ok = taxonomy.normalize_material(params.get("material"))
    if not mat_ok:
        warnings.append("{0}: material '{1}' not in taxonomy -> unknown".format(
            label, params.get("material")))
    condition, cond_ok = taxonomy.normalize_condition(params.get("condition"))
    if not cond_ok:
        warnings.append("{0}: condition '{1}' not in taxonomy -> unknown".format(
            label, params.get("condition")))
    return material, condition


def build_plan(entities, parse_warnings=None):
    """Turn parsed entities into a validated BuildPlan.

    Order matters: walls first (openings need hosts), then openings in file
    order. Invalid entities are skipped with a warning, never fatal.
    """
    warnings = list(parse_warnings) if parse_warnings else []
    walls = []
    wall_ids = {}

    for entity in entities:
        if entity["command"] != "make_wall":
            continue
        p = entity["params"]
        entity_id = p.get("id")
        label = "wall id={0}".format(entity_id)
        try:
            a = (p["a_x"], p["a_y"], p.get("a_z", 0.0))
            b = (p["b_x"], p["b_y"], p.get("b_z", 0.0))
            height = p["height"]
        except KeyError as exc:
            warnings.append("{0}: missing param {1}, skipped".format(label, exc))
            continue
        length = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
        if length < MIN_WALL_LENGTH_M:
            warnings.append("{0}: degenerate (length {1:.4f} m), skipped".format(
                label, length))
            continue
        if height <= 0:
            warnings.append("{0}: non-positive height, skipped".format(label))
            continue
        thickness = p.get("thickness", 0.0)
        if thickness < DEFAULT_THICKNESS_M / 20.0:  # treat ~0 as "unspecified"
            thickness = DEFAULT_THICKNESS_M
        material, condition = _norm_material_condition(p, label, warnings)
        spec = WallSpec(entity_id, a, b, height, thickness, material, condition)
        walls.append(spec)
        if entity_id is not None:
            wall_ids[entity_id] = spec

    openings = []
    for entity in entities:
        if entity["command"] not in ("make_door", "make_window"):
            continue
        kind = "door" if entity["command"] == "make_door" else "window"
        p = entity["params"]
        entity_id = p.get("id")
        label = "{0} id={1}".format(kind, entity_id)
        try:
            center = (p["position_x"], p["position_y"], p["position_z"])
            width = p["width"]
            height = p["height"]
        except KeyError as exc:
            warnings.append("{0}: missing param {1}, skipped".format(label, exc))
            continue
        if width < MIN_OPENING_SIZE_M or height < MIN_OPENING_SIZE_M:
            warnings.append("{0}: degenerate size, skipped".format(label))
            continue

        host_id = None
        for candidate in (p.get("wall0_id", -1), p.get("wall1_id", -1)):
            if candidate is not None and candidate >= 0 and candidate in wall_ids:
                host_id = candidate
                break
        if host_id is None:
            warnings.append(
                "{0}: no host wall found (wall0_id={1}, wall1_id={2}), skipped".format(
                    label, p.get("wall0_id"), p.get("wall1_id")))
            continue

        host = wall_ids[host_id]
        sill = center[2] - height / 2.0 - host.a[2]
        if sill < 0.0:
            if sill < -0.05:
                warnings.append("{0}: sill {1:.3f} m below wall base, clamped to 0".format(
                    label, sill))
            sill = 0.0

        material, condition = _norm_material_condition(p, label, warnings)
        openings.append(OpeningSpec(kind, entity_id, host_id, (center[0], center[1]),
                                    sill, width, height, material, condition))

    used = {}
    for spec in walls:
        used[spec.material] = True
    for spec in openings:
        used[spec.material] = True
    materials_used = sorted(used.keys())

    return BuildPlan(walls, openings, materials_used, warnings)
