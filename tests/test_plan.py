# -*- coding: utf-8 -*-
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bimscript_core import parser, plan

SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "samples", "scene100_material.txt")


def wall(entity_id, ax=0.0, ay=0.0, bx=4.0, by=0.0, height=3.0, thickness=0.0,
         material="tile", condition="good", az=0.0):
    return {"command": "make_wall", "params": {
        "id": entity_id, "a_x": ax, "a_y": ay, "a_z": az, "b_x": bx, "b_y": by,
        "b_z": az, "height": height, "thickness": thickness,
        "material": material, "condition": condition}}


def door(entity_id, wall0, wall1=-1, px=1.0, py=0.0, pz=1.0, width=0.9, height=2.0):
    return {"command": "make_door", "params": {
        "id": entity_id, "wall0_id": wall0, "wall1_id": wall1, "position_x": px,
        "position_y": py, "position_z": pz, "width": width, "height": height,
        "material": "composite", "condition": "good"}}


class TestBuildPlan(unittest.TestCase):
    def test_real_sample_full_plan(self):
        entities, warnings = parser.parse_program_file(SAMPLE)
        build = plan.build_plan(entities, warnings)
        self.assertEqual(len(build.walls), 18)
        self.assertEqual(len(build.openings), 10)
        # every opening found its host among the walls
        wall_ids = set(w.entity_id for w in build.walls)
        for opening in build.openings:
            self.assertIn(opening.host_id, wall_ids)
        # doors sit on the floor: center z ~= height/2 in this corpus
        for opening in build.openings:
            if opening.kind == "door":
                self.assertLess(abs(opening.sill), 0.02)

    def test_sill_is_center_minus_half_height(self):
        entities = [wall(1), door(1000, wall0=1, pz=2.0, height=1.5)]
        build = plan.build_plan(entities)
        self.assertAlmostEqual(build.openings[0].sill, 2.0 - 0.75)

    def test_host_falls_back_to_wall1(self):
        entities = [wall(7), door(1000, wall0=99, wall1=7)]
        build = plan.build_plan(entities)
        self.assertEqual(len(build.openings), 1)
        self.assertEqual(build.openings[0].host_id, 7)

    def test_no_host_skips_with_warning(self):
        entities = [wall(1), door(1000, wall0=99, wall1=-1)]
        build = plan.build_plan(entities)
        self.assertEqual(build.openings, [])
        self.assertTrue(any("no host wall" in w for w in build.warnings))

    def test_zero_thickness_gets_default(self):
        build = plan.build_plan([wall(1, thickness=0.0)])
        self.assertAlmostEqual(build.walls[0].thickness, plan.DEFAULT_THICKNESS_M)

    def test_real_thickness_kept(self):
        build = plan.build_plan([wall(1, thickness=0.24)])
        self.assertAlmostEqual(build.walls[0].thickness, 0.24)

    def test_degenerate_wall_skipped(self):
        build = plan.build_plan([wall(1, bx=0.0, by=0.0)])
        self.assertEqual(build.walls, [])
        self.assertTrue(any("degenerate" in w for w in build.warnings))

    def test_out_of_vocab_material_becomes_unknown(self):
        build = plan.build_plan([wall(1, material="vibranium")])
        self.assertEqual(build.walls[0].material, "unknown")
        self.assertTrue(any("not in taxonomy" in w for w in build.warnings))

    def test_layout_only_defaults_to_unknown_without_warning(self):
        entity = wall(1)
        del entity["params"]["material"]
        del entity["params"]["condition"]
        build = plan.build_plan([entity])
        self.assertEqual(build.walls[0].material, "unknown")
        self.assertEqual(build.warnings, [])

    def test_negative_sill_clamped(self):
        entities = [wall(1), door(1000, wall0=1, pz=0.5, height=2.0)]
        build = plan.build_plan(entities)
        self.assertEqual(build.openings[0].sill, 0.0)

    def test_materials_used_covers_openings(self):
        entities = [wall(1, material="brick"), door(1000, wall0=1)]
        build = plan.build_plan(entities)
        self.assertEqual(build.materials_used, ["brick", "composite"])


if __name__ == "__main__":
    unittest.main()
