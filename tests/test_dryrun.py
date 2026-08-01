# -*- coding: utf-8 -*-
import json
import os
import sys
import tempfile
import unittest

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_DIR)

import dryrun

SAMPLE = os.path.join(PLUGIN_DIR, "samples", "scene100_material.txt")


class TestDryRun(unittest.TestCase):
    def test_end_to_end_json(self):
        out = os.path.join(tempfile.mkdtemp(), "plan.json")
        code = dryrun.main([SAMPLE, "--json", out])
        self.assertEqual(code, 0)
        with open(out) as fh:
            data = json.load(fh)
        self.assertEqual(sorted(data.keys()),
                         ["materials_used", "openings", "walls", "warnings"])
        self.assertEqual(len(data["walls"]), 18)
        self.assertEqual(len(data["openings"]), 10)
        # JSON stays meters/scene-frame: spot-check against the source file
        first = data["walls"][0]
        self.assertEqual(first["id"], 4)
        self.assertAlmostEqual(first["a"][0], -2.5230430886149406)
        self.assertEqual(first["material"], "wallpaper")
        # thickness defaulting applied
        self.assertAlmostEqual(first["thickness"], 0.1)

    def test_empty_program_fails(self):
        empty = os.path.join(tempfile.mkdtemp(), "empty.txt")
        with open(empty, "w") as fh:
            fh.write("\n")
        self.assertEqual(dryrun.main([empty]), 1)

    def test_writes_ifc_mapping_artifacts(self):
        folder = tempfile.mkdtemp()
        manifest_path = os.path.join(folder, "mapping.json")
        pset_path = os.path.join(folder, "BIMScript_Properties.txt")
        code = dryrun.main([
            SAMPLE, "--ifc-manifest", manifest_path,
            "--ifc-pset", pset_path])
        self.assertEqual(code, 0)
        with open(manifest_path) as fh:
            manifest = json.load(fh)
        self.assertEqual(manifest["mapping"]["state"], "plan_only")
        self.assertEqual(manifest["counts"]["planned"]["walls"], 18)
        with open(pset_path) as fh:
            self.assertIn("BIMScript_Properties", fh.read())


if __name__ == "__main__":
    unittest.main()
