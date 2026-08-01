# -*- coding: utf-8 -*-
"""Import BIMScript as native Revit elements and optionally export IFC4.

Thin executor of the BuildPlan produced by bimscript_core (which is the
Linux-tested part): every geometric/semantic decision was already made there;
this file only speaks Revit API. Paper Section 8 mapping:

    make_wall            -> DB.Wall.Create (native wall, per-material wall type)
    make_door/window     -> hosted DB.FamilyInstance on the host wall
    material=...         -> Revit Material on the wall type's core layer
                            (+ BIMScript_Material shared parameter everywhere)
    condition=...        -> BIMScript_Condition shared parameter (custom Pset)

IFC is a second, explicit adapter stage.  Revit's installed IFC exporter maps
the native categories to IfcWall/IfcDoor/IfcWindow and the BIMScript shared
parameters to BIMScript_Properties.  A JSON sidecar records the intended mapping,
Revit element identities, stored IFC GUIDs and a lightweight export scan.

IronPython 2.7 compatible (pyRevit default engine): no f-strings.
Revit 2021+ units API with legacy fallback.
"""
__title__ = "Import\nBIMScript"
__author__ = "BIMScript"

import os
import sys
import tempfile

_PLUGIN_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from pyrevit import DB, forms, revit, script  # noqa: E402

from bimscript_core import parser as bs_parser  # noqa: E402
from bimscript_core import plan as bs_plan  # noqa: E402
from bimscript_core import taxonomy  # noqa: E402
from bimscript_core import ifc as bs_ifc  # noqa: E402

doc = revit.doc
app = doc.Application
output = script.get_output()

SHARED_PARAMS = ("BIMScript_Id", "BIMScript_Material", "BIMScript_Condition")
PSET_PATH = os.path.join(_PLUGIN_DIR, "ifc", "BIMScript_Properties.txt")
IFC_PSET_ENTITIES = ("IfcWall", "IfcDoor", "IfcWindow")


def to_internal(meters):
    """meters -> Revit internal units (feet), across API generations."""
    try:
        return DB.UnitUtils.ConvertToInternalUnits(meters, DB.UnitTypeId.Meters)
    except AttributeError:  # pre-2021
        return DB.UnitUtils.ConvertToInternalUnits(
            meters, DB.DisplayUnitType.DUT_METERS)


def lowest_level():
    levels = list(DB.FilteredElementCollector(doc).OfClass(DB.Level))
    if not levels:
        return None
    return min(levels, key=lambda lv: lv.Elevation)


def ensure_shared_parameters(warnings):
    """Bind BIMScript_* instance text parameters to Walls/Doors/Windows."""
    try:
        sp_path = os.path.join(tempfile.gettempdir(), "bimscript_shared_params.txt")
        if not os.path.exists(sp_path):
            open(sp_path, "a").close()
        app.SharedParametersFilename = sp_path
        def_file = app.OpenSharedParameterFile()
        group = def_file.Groups.get_Item("BIMScript")
        if group is None:
            group = def_file.Groups.Create("BIMScript")

        categories = app.Create.NewCategorySet()
        for bic in (DB.BuiltInCategory.OST_Walls, DB.BuiltInCategory.OST_Doors,
                    DB.BuiltInCategory.OST_Windows):
            categories.Insert(doc.Settings.Categories.get_Item(bic))
        binding = app.Create.NewInstanceBinding(categories)

        for name in SHARED_PARAMS:
            definition = group.Definitions.get_Item(name)
            if definition is None:
                try:  # 2022+: ForgeTypeId spec
                    options = DB.ExternalDefinitionCreationOptions(
                        name, DB.SpecTypeId.String.Text)
                except AttributeError:  # 2021 and older
                    options = DB.ExternalDefinitionCreationOptions(
                        name, DB.ParameterType.Text)
                definition = group.Definitions.Create(options)
            try:  # <=2023 group enum
                doc.ParameterBindings.Insert(
                    definition, binding, DB.BuiltInParameterGroup.PG_DATA)
            except AttributeError:  # 2024+: GroupTypeId
                doc.ParameterBindings.Insert(
                    definition, binding, DB.GroupTypeId.Data)
    except Exception as exc:  # never fatal: Comments fallback still applies
        warnings.append("shared parameters unavailable ({0}); values go to "
                        "Comments only".format(exc))


def tag_element(element, entity_id, material, condition):
    values = {"BIMScript_Id": str(entity_id), "BIMScript_Material": material,
              "BIMScript_Condition": condition}
    for name, value in values.items():
        param = element.LookupParameter(name)
        if param is not None and not param.IsReadOnly:
            param.Set(value)
    comments = element.get_Parameter(
        DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
    if comments is not None and not comments.IsReadOnly:
        comments.Set("BIMScript id={0}; material={1}; condition={2}".format(
            entity_id, material, condition))


def integer_element_id(element):
    """Return a JSON-safe ElementId across Revit API generations."""
    try:  # Revit 2024+
        return int(element.Id.Value)
    except AttributeError:
        return int(element.Id.IntegerValue)


def created_record(kind, spec, element):
    """Keep the element temporarily so IFC GUID evidence can be read later."""
    return {
        "kind": kind,
        "bimscript_id": spec.entity_id,
        "element_id": integer_element_id(element),
        "unique_id": str(element.UniqueId),
        "_element": element,
    }


def stored_ifc_guid(element):
    """Best-effort read of the GUID parameter populated by StoreIFCGUID."""
    parameter = None
    try:
        parameter = element.get_Parameter(DB.BuiltInParameter.IFC_GUID)
    except (AttributeError, TypeError):
        pass
    if parameter is None:
        for name in ("IfcGUID", "IFC GUID", "IfcGuid"):
            parameter = element.LookupParameter(name)
            if parameter is not None:
                break
    if parameter is None:
        return None
    try:
        value = parameter.AsString()
        return str(value) if value else None
    except Exception:
        return None


def public_created_records(records):
    public = []
    for record in records:
        item = dict(record)
        element = item.pop("_element")
        guid = stored_ifc_guid(element)
        if guid:
            item["ifc_guid"] = guid
        public.append(item)
    return public


def ensure_pset_definition(warnings):
    """Return a usable Pset definition path, regenerating it if missing.

    bimscript_core owns the canonical text, so a packaged file that was
    deleted (for example by reinstalling the extension) is recoverable
    without failing the export.
    """
    if os.path.isfile(PSET_PATH):
        return PSET_PATH
    fallback = os.path.join(tempfile.gettempdir(), "BIMScript_Properties.txt")
    for path in (PSET_PATH, fallback):
        try:
            folder = os.path.dirname(path)
            if folder and not os.path.isdir(folder):
                os.makedirs(folder)
            bs_ifc.write_pset_definition(path)
            warnings.append(
                "packaged Pset definition was missing; regenerated it at "
                "{0}".format(path))
            return path
        except Exception:
            continue
    return None


def shared_parameter_ids():
    ids = {}
    for element in (DB.FilteredElementCollector(doc)
                    .OfClass(DB.SharedParameterElement)):
        ids[DB.Element.Name.__get__(element)] = element.Id
    return ids


def ensure_user_defined_pset(warnings):
    """Define BIMScript_Properties in the store the modern IFC exporter reads.

    Revit's exporter only reads ExportUserDefinedPsetsFileName on its
    legacy parameter-mapping code path. On current builds (verified on
    2027) that path is off, and user-defined Psets come from a
    document-level IFCUserDefinedPropertySet store instead -- an
    IFCParameterTemplate does not feed it. Rebuild the definition every
    run so it stays in step with the taxonomy, and report whether it
    took, since the legacy file alone is not enough here.
    """
    if not hasattr(DB, "IFCUserDefinedPropertySet"):
        return False
    try:
        from System.Collections.Generic import List

        store = DB.IFCUserDefinedPropertySet
        pset = store.FindPropertySetByName(doc, bs_ifc.PSET_NAME)
        if pset is None:
            pset = store.Create(doc, bs_ifc.PSET_NAME)
        else:
            pset.ClearPropertySet()

        entities = List[str]()
        for name in IFC_PSET_ENTITIES:
            entities.Add(name)
        pset.SetApplicableEntities(entities)

        parameter_ids = shared_parameter_ids()
        for name in SHARED_PARAMS:
            pset.AddProperty(DB.IFCUserDefinedProperty(
                name,
                parameter_ids.get(name, DB.ElementId.InvalidElementId),
                name, "Text", DB.IFCUserDefinedPropertyType.Single, ""))
        return True
    except Exception as exc:
        warnings.append(
            "could not define {0} in the document ({1}); the export will "
            "likely omit the BIMScript property set".format(
                bs_ifc.PSET_NAME, exc))
        return False


def ifc_export_options(pset_path):
    """Create a version-tolerant IFC4 Reference View configuration."""
    options = DB.IFCExportOptions()
    schema_label = "Revit default IFC version"
    for enum_name, label in (
            ("IFC4RV", "IFC4 Reference View"),
            ("IFC4", "IFC4")):
        if hasattr(DB.IFCVersion, enum_name):
            options.FileVersion = getattr(DB.IFCVersion, enum_name)
            schema_label = label
            break
    options.ExportBaseQuantities = True
    options.WallAndColumnSplitting = False
    options.SpaceBoundaryLevel = 0
    named = {
        "ExportIFCCommonPropertySets": "true",
        "ExportInternalRevitPropertySets": "false",
        "ExportUserDefinedPsets": "true",
        "ExportUserDefinedPsetsFileName": pset_path,
        "StoreIFCGUID": "true",
        "UseActiveViewGeometry": "false",
        "VisibleElementsOfCurrentView": "false",
        "ExportLinkedFiles": "false",
    }
    for name, value in named.items():
        options.AddOption(name, value)
    return options, schema_label, named


def export_ifc_file(path, warnings, pset_path):
    """Export the whole active document; return auditable export metadata."""
    folder = os.path.dirname(path)
    file_stem = os.path.splitext(os.path.basename(path))[0]
    actual_path = os.path.join(folder, file_stem + ".ifc")
    options = None
    pset_defined = False
    # The transaction is required both for StoreIFCGUID write-back and for
    # defining the user-defined Pset in the document above.
    try:
        with revit.Transaction("Export BIMScript IFC"):
            pset_defined = ensure_user_defined_pset(warnings)
            options, schema_label, named = ifc_export_options(pset_path)
            result = doc.Export(folder, file_stem, options)
            if not result:
                raise RuntimeError("Revit Document.Export returned false")
    finally:
        if options is not None:
            try:
                options.Dispose()
            except Exception:
                pass
    return actual_path, {
        "completed": True,
        "output_file": os.path.basename(actual_path),
        "requested_schema": schema_label,
        "exporter": "installed Revit IFC exporter",
        "revit_version": str(app.VersionNumber),
        "revit_build": str(getattr(app, "VersionBuild", "unknown")),
        "base_quantities": True,
        "named_options": named,
        "user_defined_pset_defined": pset_defined,
    }


def ensure_materials(names, warnings):
    """Create/reuse one Revit Material per taxonomy name, paper-figure colors."""
    existing = {}
    for mat in DB.FilteredElementCollector(doc).OfClass(DB.Material):
        existing[mat.Name] = mat.Id
    ids = {}
    for name in names:
        revit_name = "BIMScript_" + name
        if revit_name in existing:
            ids[name] = existing[revit_name]
            continue
        try:
            mat_id = DB.Material.Create(doc, revit_name)
            mat = doc.GetElement(mat_id)
            r, g, b = taxonomy.material_rgb(name)
            mat.Color = DB.Color(r, g, b)
            ids[name] = mat_id
        except Exception as exc:
            warnings.append("material '{0}': {1}".format(name, exc))
    return ids


def base_wall_type():
    type_id = doc.GetDefaultElementTypeId(DB.ElementTypeGroup.WallType)
    base = doc.GetElement(type_id) if type_id != DB.ElementId.InvalidElementId else None
    if isinstance(base, DB.WallType) and base.Kind == DB.WallKind.Basic:
        return base
    for wall_type in DB.FilteredElementCollector(doc).OfClass(DB.WallType):
        if wall_type.Kind == DB.WallKind.Basic:
            return wall_type
    return None


def ensure_wall_types(materials_needed, material_ids, warnings):
    """One duplicated wall type per material, material set on the core layer."""
    base = base_wall_type()
    if base is None:
        warnings.append("no Basic wall type in the template; using defaults")
        return {}
    existing = {}
    for wall_type in DB.FilteredElementCollector(doc).OfClass(DB.WallType):
        existing[DB.Element.Name.__get__(wall_type)] = wall_type
    types = {}
    for name in materials_needed:
        type_name = "BIMScript - " + name
        if type_name in existing:
            types[name] = existing[type_name]
            continue
        try:
            new_type = base.Duplicate(type_name)
            structure = new_type.GetCompoundStructure()
            if structure is not None and name in material_ids:
                layer_index = structure.GetFirstCoreLayerIndex()
                if layer_index < 0:
                    layer_index = 0
                structure.SetMaterialId(layer_index, material_ids[name])
                new_type.SetCompoundStructure(structure)
            types[name] = new_type
        except Exception as exc:
            warnings.append("wall type for '{0}': {1} (default type used)".format(
                name, exc))
    return types


def first_symbol_of(category):
    for symbol in (DB.FilteredElementCollector(doc)
                   .OfClass(DB.FamilySymbol).OfCategory(category)):
        return symbol
    return None


def existing_symbol_named(base_symbol, name):
    family_id = base_symbol.Family.Id
    for symbol in DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol):
        if symbol.Family.Id == family_id and \
                DB.Element.Name.__get__(symbol) == name:
            return symbol
    return None


def sized_symbol(base_symbol, kind, width_m, height_m, cache, warnings):
    """Best-effort per-size type: reuse or duplicate the family symbol and set W/H."""
    key = (kind, round(width_m, 2), round(height_m, 2))
    if key in cache:
        return cache[key]
    symbol = base_symbol
    try:
        name = "BIMScript {0} {1:.2f}x{2:.2f}".format(kind, width_m, height_m)
        duplicate = existing_symbol_named(base_symbol, name)
        if duplicate is None:
            duplicate = base_symbol.Duplicate(name)
        applied = False
        for param_name, value in (("Width", width_m), ("Height", height_m)):
            param = duplicate.LookupParameter(param_name)
            if param is not None and not param.IsReadOnly:
                param.Set(to_internal(value))
                applied = True
        if applied:
            symbol = duplicate
        else:
            warnings.append("{0} family has no Width/Height type params; "
                            "default size used".format(kind))
    except Exception as exc:
        warnings.append("{0} size type {1:.2f}x{2:.2f}: {3}".format(
            kind, width_m, height_m, exc))
    cache[key] = symbol
    return symbol


def main():
    program_path = forms.pick_file(file_ext="txt",
                                   title="Pick a BIMScript program (.txt)")
    if not program_path:
        return

    entities, parse_warnings = bs_parser.parse_program_file(program_path)
    build = bs_plan.build_plan(entities, parse_warnings)
    if not build.walls:
        forms.alert("No buildable walls in this program.", exitscript=True)

    level = lowest_level()
    if level is None:
        forms.alert("Document has no Level to build on.", exitscript=True)

    warnings = list(build.warnings)
    created = {"walls": 0, "doors": 0, "windows": 0}
    created_records = []
    door_base = first_symbol_of(DB.BuiltInCategory.OST_Doors)
    window_base = first_symbol_of(DB.BuiltInCategory.OST_Windows)
    size_cache = {}

    with revit.Transaction("Import BIMScript"):
        ensure_shared_parameters(warnings)
        material_ids = ensure_materials(build.materials_used, warnings)
        wall_types = ensure_wall_types(
            sorted(set(w.material for w in build.walls)), material_ids, warnings)

        wall_elements = {}
        for spec in build.walls:
            start = DB.XYZ(to_internal(spec.a[0]), to_internal(spec.a[1]),
                           to_internal(spec.a[2]))
            end = DB.XYZ(to_internal(spec.b[0]), to_internal(spec.b[1]),
                         to_internal(spec.b[2]))
            line = DB.Line.CreateBound(start, end)
            wall_type = wall_types.get(spec.material)
            type_id = wall_type.Id if wall_type is not None else (
                doc.GetDefaultElementTypeId(DB.ElementTypeGroup.WallType))
            offset = to_internal(spec.a[2]) - level.Elevation
            try:
                wall = DB.Wall.Create(doc, line, type_id, level.Id,
                                      to_internal(spec.height), offset,
                                      False, False)
            except Exception as exc:
                warnings.append("wall id={0}: {1}".format(spec.entity_id, exc))
                continue
            tag_element(wall, spec.entity_id, spec.material, spec.condition)
            wall_elements[spec.entity_id] = wall
            created["walls"] += 1
            created_records.append(created_record("wall", spec, wall))

        doc.Regenerate()  # walls must exist before hosting

        for spec in build.openings:
            base_symbol = door_base if spec.kind == "door" else window_base
            if base_symbol is None:
                warnings.append("no {0} family loaded; {1} id={2} skipped "
                                "(load one, e.g. M_Single-Flush / M_Fixed)".format(
                                    spec.kind, spec.kind, spec.entity_id))
                continue
            host = wall_elements.get(spec.host_id)
            if host is None:
                warnings.append("{0} id={1}: host wall {2} was not created, "
                                "skipped".format(spec.kind, spec.entity_id,
                                                 spec.host_id))
                continue
            symbol = sized_symbol(base_symbol, spec.kind, spec.width,
                                  spec.height, size_cache, warnings)
            if not symbol.IsActive:
                symbol.Activate()
                doc.Regenerate()
            point = DB.XYZ(to_internal(spec.center_xy[0]),
                           to_internal(spec.center_xy[1]), level.Elevation)
            try:
                instance = doc.Create.NewFamilyInstance(
                    point, symbol, host, level,
                    DB.Structure.StructuralType.NonStructural)
            except Exception as exc:
                warnings.append("{0} id={1}: {2}".format(
                    spec.kind, spec.entity_id, exc))
                continue
            sill = instance.get_Parameter(
                DB.BuiltInParameter.INSTANCE_SILL_HEIGHT_PARAM)
            if sill is not None and not sill.IsReadOnly:
                sill.Set(to_internal(spec.sill))
            tag_element(instance, spec.entity_id, spec.material, spec.condition)
            created[spec.kind + "s"] += 1
            created_records.append(created_record(spec.kind, spec, instance))

    output.print_md("# BIMScript import: {0}".format(
        os.path.basename(program_path)))
    output.print_md("**Created** {0} walls, {1} doors, {2} windows "
                    "({3} materials).".format(created["walls"], created["doors"],
                                              created["windows"],
                                              len(build.materials_used)))
    export_path = None
    manifest_path = None
    validation = None
    should_export = forms.alert(
        "Native Revit import is complete. Export this document as IFC4 "
        "Reference View now?\n\nThe export includes BIMScript_Properties and writes "
        "an audit sidecar next to the IFC file.",
        title="BIMScript import complete", yes=True, no=True)
    if should_export:
        default_name = os.path.splitext(os.path.basename(program_path))[0]
        default_name += "_BIMScript.ifc"
        requested_path = forms.save_file(
            file_ext="ifc", default_name=default_name,
            title="Export BIMScript IFC4")
        if requested_path:
            export_info = {
                "completed": False,
                "output_file": os.path.basename(requested_path),
                "requested_schema": "IFC4 Reference View",
                "exporter": "installed Revit IFC exporter",
            }
            try:
                pset_path = ensure_pset_definition(warnings)
                if pset_path is None:
                    raise RuntimeError(
                        "could not read or regenerate the Pset definition at "
                        "{0}".format(PSET_PATH))
                export_path, export_info = export_ifc_file(
                    requested_path, warnings, pset_path)
                records = public_created_records(created_records)
                provisional = bs_ifc.build_manifest(
                    build, source_path=program_path,
                    created_elements=records, export_info=export_info,
                    runtime_warnings=warnings[len(build.warnings):])
                validation = bs_ifc.validate_ifc_file(export_path, provisional)
                if not validation["passed"]:
                    warnings.append(
                        "IFC was written, but its structural scan failed; see "
                        "the pyRevit output and sidecar before using it")
                manifest = bs_ifc.build_manifest(
                    build, source_path=program_path,
                    created_elements=records, export_info=export_info,
                    validation=validation,
                    runtime_warnings=warnings[len(build.warnings):])
                manifest_path = export_path + ".bimscript.json"
                bs_ifc.write_manifest(manifest_path, manifest)
                output.print_md("## IFC export")
                output.print_md("- IFC: `{0}`".format(export_path))
                output.print_md("- Mapping manifest: `{0}`".format(manifest_path))
                output.print_md("- Structural scan: **{0}**".format(
                    "PASS" if validation["passed"] else "FAIL"))
                for check in validation["checks"]:
                    output.print_md("  - {0}: {1}".format(
                        "PASS" if check["ok"] else "FAIL", check["name"]))
            except Exception as exc:
                warnings.append("IFC export or audit failed: {0}".format(exc))
                export_info["error"] = str(exc)
                records = public_created_records(created_records)
                manifest = bs_ifc.build_manifest(
                    build, source_path=program_path,
                    created_elements=records, export_info=export_info,
                    runtime_warnings=warnings[len(build.warnings):])
                manifest_path = requested_path + ".bimscript.json"
                try:
                    bs_ifc.write_manifest(manifest_path, manifest)
                except Exception as manifest_exc:
                    warnings.append("IFC manifest write failed: {0}".format(
                        manifest_exc))

    if warnings:
        output.print_md("## Warnings ({0})".format(len(warnings)))
        for warning in warnings:
            output.print_md("- {0}".format(warning))

    if export_path:
        export_summary = "\nIFC: {0} ({1}).".format(
            export_path, "scan passed" if validation and validation["passed"]
            else "scan needs review")
    else:
        export_summary = "\nIFC was not exported."
    forms.alert(
        "BIMScript import done: {0} walls, {1} doors, {2} windows.\n"
        "{3} warning(s) - see the pyRevit output window.{4}".format(
            created["walls"], created["doors"], created["windows"],
            len(warnings), export_summary))


main()
