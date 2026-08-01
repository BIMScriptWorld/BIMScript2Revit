# -*- coding: utf-8 -*-
"""Standalone parser for BIMScript program text.

Parses the flat command grammar emitted by the model / stored as GT:

    make_wall, id=4, a_x=-2.52, a_y=13.36, a_z=0.0, b_x=4.47, b_y=13.36,
        b_z=0.0, height=3.43, thickness=0.0[, material=wallpaper, condition=good]
    make_door|make_window, id=1000, wall0_id=1, wall1_id=11, position_x=…,
        position_y=…, position_z=…, width=…, height=…[, material=…, condition=…]

Deliberately dependency-free (no imports from the training repo): this module
runs inside Revit's IronPython 2.7 engine on Windows as well as under CPython 3
on Linux. Keep it free of f-strings, dataclasses, and pathlib.
"""

KNOWN_COMMANDS = ("make_wall", "make_door", "make_window")

_INT_KEYS = ("id", "wall0_id", "wall1_id")
_STR_KEYS = ("material", "condition")


def parse_line(line, line_number=0):
    """Parse one program line.

    Returns (entity_dict, warning). Exactly one of the two is None. Entity dict:
    {"command": str, "params": {key: int|float|str}}. Blank lines return
    (None, None).
    """
    text = line.strip()
    if not text:
        return None, None

    parts = [p.strip() for p in text.split(",")]
    command = parts[0]
    if command not in KNOWN_COMMANDS:
        return None, "line {0}: unknown command '{1}' skipped".format(line_number, command)

    params = {}
    for token in parts[1:]:
        if not token:
            continue
        if "=" not in token:
            return None, "line {0}: malformed token '{1}'".format(line_number, token)
        key, _, raw = token.partition("=")
        key = key.strip()
        raw = raw.strip()
        if key in _STR_KEYS:
            params[key] = raw
        elif key in _INT_KEYS:
            try:
                params[key] = int(float(raw))
            except ValueError:
                return None, "line {0}: non-integer value for {1}: '{2}'".format(
                    line_number, key, raw)
        else:
            try:
                params[key] = float(raw)
            except ValueError:
                return None, "line {0}: non-numeric value for {1}: '{2}'".format(
                    line_number, key, raw)

    return {"command": command, "params": params}, None


def parse_program(text):
    """Parse a whole program string.

    Returns (entities, warnings): entities is a list of entity dicts in file
    order; warnings a list of strings for skipped/malformed lines. Never raises
    on bad content — a decoded program from an undertrained model may contain
    garbage lines, and the plugin must import the good ones.
    """
    entities = []
    warnings = []
    for number, line in enumerate(text.splitlines(), 1):
        entity, warning = parse_line(line, number)
        if entity is not None:
            entities.append(entity)
        elif warning is not None:
            warnings.append(warning)
    return entities, warnings


def parse_program_file(path):
    """parse_program over a file path (utf-8)."""
    handle = open(path, "r")
    try:
        text = handle.read()
    finally:
        handle.close()
    return parse_program(text)
