# -*- coding: utf-8 -*-
"""Deterministic BIMScript -> Revit -> IFC mapping and audit helpers.

This module deliberately does not author IFC geometry.  The pyRevit adapter
creates native Revit Walls, Doors and Windows, then Revit's maintained IFC
exporter serializes those elements.  The code here owns the explicit semantic
contract, the user-defined property-set definition and a zero-dependency
post-export structural scan.

IronPython 2.7 AND CPython 3 compatible.
"""

import hashlib
import json
import os
import re


MANIFEST_VERSION = "1.0"
MAPPING_VERSION = "1.0"
IFC_SCHEMA = "IFC4"
IFC_EXCHANGE_REQUIREMENT = "Reference View"
IFC_REFERENCE_VIEW_SCHEMA_LABEL = "IFC4 Reference View"
PSET_NAME = "BIMScript_Properties"

IFC_CLASS_BY_KIND = {
    "wall": "IfcWall",
    "door": "IfcDoor",
    "window": "IfcWindow",
}

REVIT_MAPPING_BY_KIND = {
    "wall": {"class": "Autodesk.Revit.DB.Wall", "category": "Walls"},
    "door": {"class": "Autodesk.Revit.DB.FamilyInstance", "category": "Doors"},
    "window": {"class": "Autodesk.Revit.DB.FamilyInstance", "category": "Windows"},
}

PSET_PROPERTIES = (
    ("BIMScript_Id", "Text", "BIMScript_Id"),
    ("BIMScript_Material", "Text", "BIMScript_Material"),
    ("BIMScript_Condition", "Text", "BIMScript_Condition"),
)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def pset_definition_text():
    """Return Autodesk's tab-delimited user-defined-Pset file format."""
    lines = [
        "# BIMScript instance properties for Revit IFC export",
        "PropertySet:\t{0}\tI\tIfcWall, IfcDoor, IfcWindow".format(PSET_NAME),
    ]
    for ifc_name, data_type, revit_name in PSET_PROPERTIES:
        lines.append("\t{0}\t{1}\t{2}".format(
            ifc_name, data_type, revit_name))
    return "\n".join(lines) + "\n"


def write_pset_definition(path):
    # Binary write: text mode would translate the newlines to CRLF on
    # Windows, so the packaged file would no longer match this module's
    # canonical text byte for byte.
    with open(path, "wb") as handle:
        handle.write(pset_definition_text().encode("utf-8"))


def _counts_for(build):
    return {
        "walls": len(build.walls),
        "doors": sum(1 for item in build.openings if item.kind == "door"),
        "windows": sum(1 for item in build.openings if item.kind == "window"),
    }


def _record_lookup(created_elements):
    lookup = {}
    for record in created_elements or []:
        key = (record.get("kind"), record.get("bimscript_id"))
        lookup.setdefault(key, []).append(record)
    return lookup


def _take_record(lookup, kind, entity_id):
    records = lookup.get((kind, entity_id), [])
    return records.pop(0) if records else None


def _element_manifest(kind, spec, actual):
    revit = dict(REVIT_MAPPING_BY_KIND[kind])
    revit["created"] = actual is not None
    if actual:
        for key in ("element_id", "unique_id", "ifc_guid"):
            value = actual.get(key)
            if value is not None:
                revit[key] = value

    ifc = {
        "class": IFC_CLASS_BY_KIND[kind],
        "property_set": PSET_NAME,
    }
    source = {
        "command": "make_" + kind,
        "id": spec.entity_id,
    }
    if kind != "wall":
        source["host_wall_id"] = spec.host_id
        ifc["host_translation"] = {
            "generated_by": "Revit IFC exporter",
            "opening_class": "IfcOpeningElement",
            "relations": ["IfcRelVoidsElement", "IfcRelFillsElement"],
        }

    return {
        "source": source,
        "revit": revit,
        "ifc": ifc,
        "properties": {
            "BIMScript_Id": str(spec.entity_id),
            "BIMScript_Material": spec.material,
            "BIMScript_Condition": spec.condition,
        },
    }


def build_manifest(build, source_path=None, created_elements=None,
                   export_info=None, validation=None, runtime_warnings=None):
    """Build an auditable mapping manifest from a BuildPlan.

    ``created_elements`` is adapter-supplied evidence, not a second plan.  Each
    record may contain kind, bimscript_id, Revit element_id/unique_id and the
    IFC GUID stored by the exporter.
    """
    lookup = _record_lookup(created_elements)
    elements = []
    for wall in build.walls:
        elements.append(_element_manifest(
            "wall", wall, _take_record(lookup, "wall", wall.entity_id)))
    for opening in build.openings:
        elements.append(_element_manifest(
            opening.kind, opening,
            _take_record(lookup, opening.kind, opening.entity_id)))

    created_counts = {"walls": 0, "doors": 0, "windows": 0}
    for item in elements:
        if item["revit"]["created"]:
            plural = item["source"]["command"].replace("make_", "") + "s"
            created_counts[plural] += 1

    source = {}
    if source_path:
        source["file"] = os.path.basename(source_path)
        if os.path.exists(source_path):
            source["sha256"] = sha256_file(source_path)

    state = "plan_only"
    if export_info and export_info.get("completed"):
        state = "exported"
    if validation:
        state = "validated" if validation.get("passed") else "validation_failed"

    return {
        "manifest_version": MANIFEST_VERSION,
        "mapping": {
            "version": MAPPING_VERSION,
            "strategy": "BIMScript -> native Revit -> Revit IFC exporter",
            "state": state,
            "ifc_schema": IFC_SCHEMA,
            "preferred_exchange_requirement": IFC_EXCHANGE_REQUIREMENT,
            "property_set": PSET_NAME,
            "claim_boundary": (
                "This manifest records intended/observed class and property "
                "mappings; it is not evidence of lossless IFC semantics."),
        },
        "source": source,
        "counts": {
            "planned": _counts_for(build),
            "created_in_revit": created_counts,
        },
        "export": export_info or {"completed": False},
        "elements": elements,
        "validation": validation or {
            "performed": False,
            "level": "none",
            "passed": False,
        },
        "build_warnings": list(build.warnings),
        "runtime_warnings": list(runtime_warnings or []),
    }


def write_manifest(path, manifest):
    with open(path, "w") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _entity_count(text, entity_names):
    total = 0
    for name in entity_names:
        total += len(re.findall(
            r"#\d+\s*=\s*" + name + r"\s*\(", text, re.IGNORECASE))
    return total


def expected_counts_from_manifest(manifest):
    created = manifest.get("counts", {}).get("created_in_revit", {})
    if sum(created.values()):
        return created
    return manifest.get("counts", {}).get("planned", {})


def validate_ifc_file(path, manifest=None):
    """Lexically scan a STEP IFC for the contract's minimum evidence.

    This catches missing/truncated exports, wrong schema, missing element
    classes, missing Pset fields and missing hosting relationships.  It does
    not replace schema validation or geometric inspection in an IFC tool.
    """
    result = {
        "performed": True,
        "level": "zero_dependency_structural_scan",
        "passed": False,
        "file": os.path.basename(path),
        "checks": [],
        "limitations": (
            "Lexical STEP scan only; run an IFC schema validator and inspect "
            "geometry/hosting in an independent viewer before publication."),
    }
    if not os.path.isfile(path):
        result["checks"].append({
            "name": "file_exists", "ok": False, "actual": False})
        return result

    with open(path, "rb") as handle:
        raw = handle.read()
    try:
        text = raw.decode("utf-8", "ignore")
    except AttributeError:  # pragma: no cover - IronPython may return text
        text = raw
    upper = text.upper()

    schema_match = re.search(
        r"FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'", text, re.IGNORECASE)
    schema = schema_match.group(1).upper() if schema_match else None
    checks = result["checks"]
    checks.append({"name": "file_exists", "ok": True, "actual": True})
    checks.append({
        "name": "ifc4_schema", "ok": bool(schema and schema.startswith("IFC4")),
        "expected": IFC_SCHEMA, "actual": schema,
    })

    counts = {
        "walls": _entity_count(text, ("IFCWALL", "IFCWALLSTANDARDCASE")),
        "doors": _entity_count(text, ("IFCDOOR",)),
        "windows": _entity_count(text, ("IFCWINDOW",)),
        "void_relations": _entity_count(text, ("IFCRELVOIDSELEMENT",)),
        "fill_relations": _entity_count(text, ("IFCRELFILLSELEMENT",)),
    }
    result["observed_counts"] = counts
    if manifest:
        planned = manifest.get("counts", {}).get("planned", {})
        created = manifest.get("counts", {}).get("created_in_revit", {})
        if planned:
            shortfalls = dict(
                (plural, {"planned": planned.get(plural, 0),
                          "created": created.get(plural, 0)})
                for plural in ("walls", "doors", "windows")
                if created.get(plural, 0) < planned.get(plural, 0))
            check = {
                "name": "all_planned_elements_created",
                "ok": not shortfalls,
            }
            if shortfalls:
                check["shortfalls"] = shortfalls
            result["checks"].append(check)

    expected = expected_counts_from_manifest(manifest) if manifest else None
    if expected:
        for plural in ("walls", "doors", "windows"):
            checks.append({
                "name": plural + "_at_least_created",
                "ok": counts[plural] >= expected.get(plural, 0),
                "expected_minimum": expected.get(plural, 0),
                "actual": counts[plural],
            })
        opening_count = expected.get("doors", 0) + expected.get("windows", 0)
        requested_schema = (manifest.get("export") or {}).get("requested_schema")
        is_reference_view = requested_schema == IFC_REFERENCE_VIEW_SCHEMA_LABEL
        for relation in ("void_relations", "fill_relations"):
            if is_reference_view:
                checks.append({
                    "name": relation + "_at_least_openings",
                    "ok": True,
                    "expected_minimum": opening_count,
                    "actual": counts[relation],
                    "skipped_reference_view": True,
                    "note": (
                        "IFC4 Reference View is not enforced for this check; "
                        "export plain IFC4 to validate "
                        "IfcRelVoidsElement/IfcRelFillsElement."),
                })
            else:
                checks.append({
                    "name": relation + "_at_least_openings",
                    "ok": counts[relation] >= opening_count,
                    "expected_minimum": opening_count,
                    "actual": counts[relation],
                })

    for token in (PSET_NAME,) + tuple(item[0] for item in PSET_PROPERTIES):
        checks.append({
            "name": token + "_present", "ok": token.upper() in upper,
        })

    if manifest:
        guids = []
        for item in manifest.get("elements", []):
            guid = item.get("revit", {}).get("ifc_guid")
            if guid:
                guids.append(guid)
        if guids:
            found = sum(1 for guid in guids if str(guid).upper() in upper)
            checks.append({
                "name": "stored_ifc_guids_present",
                "ok": found == len(guids),
                "expected": len(guids), "actual": found,
            })

    result["passed"] = all(check.get("ok", False) for check in checks)
    return result
