# -*- coding: utf-8 -*-
import json
import os
import sys
import tempfile
import unittest

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_DIR)

from bimscript_core import ifc, parser, plan

SAMPLE = os.path.join(PLUGIN_DIR, "samples", "scene100_material.txt")


def sample_build():
    entities, warnings = parser.parse_program_file(SAMPLE)
    return plan.build_plan(entities, warnings)


class TestIfcMapping(unittest.TestCase):
    def test_pset_definition_uses_official_tab_format(self):
        text = ifc.pset_definition_text()
        self.assertIn(
            "PropertySet:\tBIMScript_Properties\tI\tIfcWall, IfcDoor, IfcWindow",
            text)
        self.assertIn("\tBIMScript_Id\tText\tBIMScript_Id", text)
        property_lines = [
            line for line in text.splitlines() if line.startswith("\t")]
        self.assertEqual(len(property_lines), 3)
        # Binary compare: a text-mode read would silently normalise CRLF and
        # hide a packaged file that no longer matches the canonical text.
        packaged = os.path.join(PLUGIN_DIR, "ifc", "BIMScript_Properties.txt")
        with open(packaged, "rb") as handle:
            self.assertEqual(handle.read(), text.encode("utf-8"))

    def test_written_pset_definition_is_byte_exact(self):
        path = os.path.join(tempfile.mkdtemp(), "BIMScript_Properties.txt")
        ifc.write_pset_definition(path)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(),
                             ifc.pset_definition_text().encode("utf-8"))

    def test_plan_only_manifest_is_explicit_and_complete(self):
        manifest = ifc.build_manifest(sample_build(), source_path=SAMPLE)
        self.assertEqual(manifest["mapping"]["state"], "plan_only")
        self.assertEqual(manifest["mapping"]["ifc_schema"], "IFC4")
        self.assertEqual(manifest["counts"]["planned"], {
            "walls": 18, "doors": 3, "windows": 7})
        self.assertEqual(manifest["counts"]["created_in_revit"], {
            "walls": 0, "doors": 0, "windows": 0})
        self.assertEqual(len(manifest["elements"]), 28)
        wall = manifest["elements"][0]
        self.assertEqual(wall["ifc"]["class"], "IfcWall")
        self.assertFalse(wall["revit"]["created"])
        door = next(item for item in manifest["elements"]
                    if item["source"]["command"] == "make_door")
        self.assertEqual(door["ifc"]["class"], "IfcDoor")
        self.assertEqual(door["ifc"]["host_translation"]["relations"],
                         ["IfcRelVoidsElement", "IfcRelFillsElement"])
        self.assertEqual(len(manifest["source"]["sha256"]), 64)

    def test_created_evidence_is_merged_by_kind_and_source_id(self):
        build = sample_build()
        actual = [{
            "kind": "wall", "bimscript_id": build.walls[0].entity_id,
            "element_id": 42, "unique_id": "revit-unique",
            "ifc_guid": "0123456789012345678901",
        }]
        manifest = ifc.build_manifest(build, created_elements=actual)
        first = manifest["elements"][0]
        self.assertTrue(first["revit"]["created"])
        self.assertEqual(first["revit"]["element_id"], 42)
        self.assertEqual(manifest["counts"]["created_in_revit"]["walls"], 1)

    def test_manifest_json_round_trip(self):
        path = os.path.join(tempfile.mkdtemp(), "manifest.json")
        ifc.write_manifest(path, ifc.build_manifest(sample_build()))
        with open(path) as handle:
            loaded = json.load(handle)
        self.assertEqual(loaded["manifest_version"], "1.0")
        self.assertEqual(loaded["mapping"]["property_set"], "BIMScript_Properties")


class TestIfcStructuralScan(unittest.TestCase):
    def _write_ifc(self, text):
        path = os.path.join(tempfile.mkdtemp(), "sample.ifc")
        with open(path, "w") as handle:
            handle.write(text)
        return path

    def test_scan_passes_minimal_valid_contract(self):
        text = """ISO-10303-21;
HEADER;
FILE_SCHEMA(('IFC4'));
ENDSEC;
DATA;
#1=IFCWALL('w');
#2=IFCDOOR('d');
#3=IFCWINDOW('x');
#4=IFCRELVOIDSELEMENT('v');
#5=IFCRELFILLSELEMENT('f');
#6=IFCRELVOIDSELEMENT('v2');
#7=IFCRELFILLSELEMENT('f2');
#8=IFCPROPERTYSET('p',$,'BIMScript_Properties',$,());
#9=IFCPROPERTYSINGLEVALUE('BIMScript_Id',$,IFCTEXT('1'),$);
#10=IFCPROPERTYSINGLEVALUE('BIMScript_Material',$,IFCTEXT('wood'),$);
#11=IFCPROPERTYSINGLEVALUE('BIMScript_Condition',$,IFCTEXT('good'),$);
ENDSEC;
END-ISO-10303-21;
"""
        manifest = {"counts": {"created_in_revit": {
            "walls": 1, "doors": 1, "windows": 1}}}
        result = ifc.validate_ifc_file(self._write_ifc(text), manifest)
        self.assertTrue(result["passed"])
        self.assertEqual(result["observed_counts"]["walls"], 1)

    def _no_void_fill_ifc_text(self):
        return """ISO-10303-21;
HEADER;
FILE_SCHEMA(('IFC4'));
ENDSEC;
DATA;
#1=IFCWALL('w');
#2=IFCDOOR('d');
#3=IFCWINDOW('x');
#4=IFCPROPERTYSET('p',$,'BIMScript_Properties',$,());
#5=IFCPROPERTYSINGLEVALUE('BIMScript_Id',$,IFCTEXT('1'),$);
#6=IFCPROPERTYSINGLEVALUE('BIMScript_Material',$,IFCTEXT('wood'),$);
#7=IFCPROPERTYSINGLEVALUE('BIMScript_Condition',$,IFCTEXT('good'),$);
ENDSEC;
END-ISO-10303-21;
"""

    def test_reference_view_schema_skips_void_fill_checks(self):
        manifest = {
            "counts": {"created_in_revit": {
                "walls": 1, "doors": 1, "windows": 1}},
            "export": {"requested_schema": "IFC4 Reference View"},
        }
        result = ifc.validate_ifc_file(
            self._write_ifc(self._no_void_fill_ifc_text()), manifest)
        self.assertTrue(result["passed"])
        by_name = {item["name"]: item for item in result["checks"]}
        for name in ("void_relations_at_least_openings",
                     "fill_relations_at_least_openings"):
            self.assertTrue(by_name[name]["ok"])
            self.assertTrue(by_name[name].get("skipped_reference_view"))

    def test_plain_ifc4_schema_still_enforces_void_fill_checks(self):
        manifest = {
            "counts": {"created_in_revit": {
                "walls": 1, "doors": 1, "windows": 1}},
            "export": {"requested_schema": "IFC4"},
        }
        result = ifc.validate_ifc_file(
            self._write_ifc(self._no_void_fill_ifc_text()), manifest)
        self.assertFalse(result["passed"])
        failed = [item["name"] for item in result["checks"] if not item["ok"]]
        self.assertIn("void_relations_at_least_openings", failed)
        self.assertIn("fill_relations_at_least_openings", failed)

    def test_shortfall_against_plan_fails_even_when_ifc_matches_revit(self):
        manifest = {
            "counts": {
                "planned": {"walls": 1, "doors": 1, "windows": 1},
                "created_in_revit": {"walls": 1, "doors": 0, "windows": 0},
            },
            "export": {"requested_schema": "IFC4 Reference View"},
        }
        result = ifc.validate_ifc_file(
            self._write_ifc(self._no_void_fill_ifc_text()), manifest)
        self.assertFalse(result["passed"])
        check = next(item for item in result["checks"]
                     if item["name"] == "all_planned_elements_created")
        self.assertFalse(check["ok"])
        self.assertEqual(sorted(check["shortfalls"]), ["doors", "windows"])
        self.assertEqual(check["shortfalls"]["doors"],
                         {"planned": 1, "created": 0})

    def test_full_plan_delivery_passes_the_shortfall_check(self):
        manifest = {
            "counts": {
                "planned": {"walls": 1, "doors": 1, "windows": 1},
                "created_in_revit": {"walls": 1, "doors": 1, "windows": 1},
            },
            "export": {"requested_schema": "IFC4 Reference View"},
        }
        result = ifc.validate_ifc_file(
            self._write_ifc(self._no_void_fill_ifc_text()), manifest)
        check = next(item for item in result["checks"]
                     if item["name"] == "all_planned_elements_created")
        self.assertTrue(check["ok"])
        self.assertNotIn("shortfalls", check)
        self.assertTrue(result["passed"])

    def test_scan_rejects_wrong_schema_and_missing_pset(self):
        path = self._write_ifc(
            "ISO-10303-21;HEADER;FILE_SCHEMA(('IFC2X3'));ENDSEC;DATA;"
            "#1=IFCWALLSTANDARDCASE('w');ENDSEC;END-ISO-10303-21;")
        result = ifc.validate_ifc_file(path)
        self.assertFalse(result["passed"])
        failed = [item["name"] for item in result["checks"] if not item["ok"]]
        self.assertIn("ifc4_schema", failed)
        self.assertIn("BIMScript_Properties_present", failed)

    def test_missing_file_is_clean_failure(self):
        result = ifc.validate_ifc_file("/definitely/not/here.ifc")
        self.assertFalse(result["passed"])
        self.assertEqual(result["checks"][0]["name"], "file_exists")


if __name__ == "__main__":
    unittest.main()
