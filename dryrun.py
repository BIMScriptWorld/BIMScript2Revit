#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Linux-side dry run: show exactly what the Revit plugin would build.

The pyRevit adapter is a thin executor of the BuildPlan produced here, so this
is the honest preview of the Windows import — same parser, same validation,
same numbers, no Revit required.

Usage:
    python3 dryrun.py samples/scene100_material.txt
    python3 dryrun.py <program.txt> --json out.json
    python3 dryrun.py <program.txt> --ifc-manifest mapping.json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bimscript_core import ifc, parser, plan, taxonomy  # noqa: E402


def summarize(build, stream=sys.stdout):
    def emit(line=""):
        stream.write(line + "\n")

    emit("BIMScript dry run — what the Revit plugin will build")
    emit("=" * 56)
    emit("walls: {0} | doors: {1} | windows: {2}".format(
        len(build.walls),
        sum(1 for o in build.openings if o.kind == "door"),
        sum(1 for o in build.openings if o.kind == "window")))
    emit()
    emit("Revit materials (created/reused, with paper-figure colors):")
    for name in build.materials_used:
        emit("  BIMScript_{0:<16} rgb{1}".format(name, taxonomy.material_rgb(name)))
    emit()
    emit("Wall types (duplicated from the default Basic wall type):")
    for name in sorted(set(w.material for w in build.walls)):
        emit("  BIMScript - {0}".format(name))
    emit()
    emit("Walls (meters, scene frame):")
    for w in build.walls:
        emit("  id={0:<5} ({1:7.2f},{2:7.2f}) -> ({3:7.2f},{4:7.2f})  h={5:.2f} t={6:.2f}"
             "  {7}/{8}".format(w.entity_id, w.a[0], w.a[1], b0(w), b1(w),
                                w.height, w.thickness, w.material, w.condition))
    emit()
    emit("Openings (hosted family instances):")
    for o in build.openings:
        emit("  {0:<6} id={1:<5} host wall {2:<4} center=({3:6.2f},{4:6.2f})"
             "  sill={5:.2f} w={6:.2f} h={7:.2f}  {8}/{9}".format(
                 o.kind, o.entity_id, o.host_id, o.center_xy[0], o.center_xy[1],
                 o.sill, o.width, o.height, o.material, o.condition))
    if build.warnings:
        emit()
        emit("Warnings ({0}):".format(len(build.warnings)))
        for w in build.warnings:
            emit("  ! " + w)


def b0(w):
    return w.b[0]


def b1(w):
    return w.b[1]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("program", help="BIMScript program .txt (GT or decoded)")
    ap.add_argument("--json", help="also write the BuildPlan as JSON here")
    ap.add_argument("--ifc-manifest",
                    help="write the deterministic Revit/IFC mapping plan here")
    ap.add_argument("--ifc-pset",
                    help="write Revit's user-defined IFC Pset TXT file here")
    args = ap.parse_args(argv)

    entities, parse_warnings = parser.parse_program_file(args.program)
    build = plan.build_plan(entities, parse_warnings)

    summarize(build)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(build.as_dict(), fh, indent=2)
        print("\nBuildPlan JSON written to {0}".format(args.json))
    if args.ifc_manifest:
        manifest = ifc.build_manifest(build, source_path=args.program)
        ifc.write_manifest(args.ifc_manifest, manifest)
        print("\nIFC mapping manifest written to {0}".format(
            args.ifc_manifest))
    if args.ifc_pset:
        ifc.write_pset_definition(args.ifc_pset)
        print("\nIFC Pset definition written to {0}".format(args.ifc_pset))

    if not build.walls and not build.openings:
        print("ERROR: nothing buildable in {0}".format(args.program), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
