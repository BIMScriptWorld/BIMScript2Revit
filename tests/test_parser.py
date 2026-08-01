# -*- coding: utf-8 -*-
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bimscript_core import parser

SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "samples", "scene100_material.txt")


class TestParser(unittest.TestCase):
    def test_real_sample_parses_fully(self):
        entities, warnings = parser.parse_program_file(SAMPLE)
        self.assertEqual(warnings, [])
        commands = [e["command"] for e in entities]
        self.assertEqual(len(entities), 28)
        self.assertEqual(commands.count("make_wall"), 18)
        self.assertEqual(commands.count("make_door"), 3)
        self.assertEqual(commands.count("make_window"), 7)

    def test_first_wall_values_exact(self):
        entities, _ = parser.parse_program_file(SAMPLE)
        wall = entities[0]
        self.assertEqual(wall["command"], "make_wall")
        p = wall["params"]
        self.assertEqual(p["id"], 4)
        self.assertAlmostEqual(p["a_x"], -2.5230430886149406)
        self.assertAlmostEqual(p["height"], 3.4380104541778564)
        self.assertEqual(p["material"], "wallpaper")
        self.assertEqual(p["condition"], "good")

    def test_layout_only_line_without_material(self):
        line = ("make_wall, id=1, a_x=0.0, a_y=0.0, a_z=0.0, b_x=2.0, b_y=0.0, "
                "b_z=0.0, height=3.0, thickness=0.0")
        entity, warning = parser.parse_line(line)
        self.assertIsNone(warning)
        self.assertNotIn("material", entity["params"])

    def test_negative_wall1_id(self):
        line = ("make_window, id=2000, wall0_id=15, wall1_id=-1, position_x=1.0, "
                "position_y=2.0, position_z=2.0, width=1.0, height=1.0")
        entity, warning = parser.parse_line(line)
        self.assertIsNone(warning)
        self.assertEqual(entity["params"]["wall1_id"], -1)

    def test_garbage_lines_warn_not_raise(self):
        text = "\n".join([
            "make_wall, id=1, a_x=0, a_y=0, a_z=0, b_x=2, b_y=0, b_z=0, height=3, thickness=0",
            "make_spaceship, id=9, warp=9",
            "make_wall, id=2, a_x=oops",
            "",
        ])
        entities, warnings = parser.parse_program(text)
        self.assertEqual(len(entities), 1)
        self.assertEqual(len(warnings), 2)
        self.assertIn("unknown command", warnings[0])
        self.assertIn("non-numeric", warnings[1])


if __name__ == "__main__":
    unittest.main()
