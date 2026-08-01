#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the zero-dependency BIMScript IFC structural scan.

Usage:
    python3 validate_ifc.py export.ifc --manifest export.ifc.bimscript.json
    python3 validate_ifc.py export.ifc --json validation.json
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bimscript_core import ifc  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ifc_file")
    parser.add_argument("--manifest", help="BIMScript IFC sidecar JSON")
    parser.add_argument("--json", help="write the validation result here")
    args = parser.parse_args(argv)

    manifest = None
    if args.manifest:
        with open(args.manifest) as handle:
            manifest = json.load(handle)
    result = ifc.validate_ifc_file(args.ifc_file, manifest)

    for check in result["checks"]:
        print("{0} {1}".format("PASS" if check["ok"] else "FAIL",
                              check["name"]))
    print("\nResult: {0}".format("PASS" if result["passed"] else "FAIL"))

    if args.json:
        with open(args.json, "w") as handle:
            json.dump(result, handle, indent=2, sort_keys=True)
            handle.write("\n")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
