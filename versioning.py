"""Assign stable release-style labels from the change relative to a version's parent."""

import re
from collections import Counter

from formats import WORKBOOK_EXTENSIONS

INITIAL_VERSION = "v1.0.0"
CELL_ADDRESS = re.compile(r"^(.+)!([A-Z]{1,3}[1-9]\d*)  ")


def cell_values(text):
    return {match.groups(): line[match.end():]
            for line in text.splitlines() if (match := CELL_ADDRESS.match(line))}


def classify_change(extension, before_text, after_text, *, identical=False):
    if identical:
        return "patch", "identical"
    if not before_text or not after_text:
        return "patch", "unavailable"
    if extension in WORKBOOK_EXTENSIONS:
        before, after = cell_values(before_text), cell_values(after_text)
        changed = sum(before.get(key) != after.get(key) for key in before.keys() | after.keys())
        basis = "cells"
    else:
        before, after = re.findall(r"\w+|[^\w\s]", before_text), re.findall(r"\w+|[^\w\s]", after_text)
        before_counts, after_counts = Counter(before), Counter(after)
        changed = max(sum((before_counts - after_counts).values()), sum((after_counts - before_counts).values()))
        if not changed:
            changed = sum(left != right for left, right in zip(before, after))
        basis = "text"
    units = max(len(before), len(after))
    if changed >= 30 and changed * 2 >= units:
        return "major", basis
    if changed >= 5:
        return "minor", basis
    return "patch", basis if changed else "no_text_change"


def next_label(previous, scale):
    if previous is None:
        return INITIAL_VERSION
    major, minor, patch = map(int, previous[1:].split("."))
    if scale == "major":
        major, minor, patch = major + 1, 0, 0
    elif scale == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    return f"v{major}.{minor}.{patch}"
