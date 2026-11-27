"""Read CSV and TSV as text and align versions by an optional unique header."""

import codecs
import csv
import io
import re
from collections import Counter

SEPARATORS = {"csv": ",", "tsv": "\t"}
TABLE_NAME = "데이터"
MAX_TABLE_FIELDS = 25_000
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def records(extension, data):
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        decoded = data.decode("utf-16")
    else:
        try:
            decoded = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            decoded = data.decode("cp949")
    if CONTROL.search(decoded):
        raise ValueError("표 파일에 지원하지 않는 제어 문자가 있습니다.")
    source = io.StringIO(decoded, newline="")
    try:
        for index, record in enumerate(csv.reader(source, delimiter=SEPARATORS[extension], strict=True), 1):
            if len(record) > 16_384 or index > 1_048_576:
                raise ValueError("표 범위를 초과했습니다.")
            yield record
    except csv.Error as error:
        raise ValueError("표 파일의 따옴표 또는 필드 경계를 확인하세요.") from error


def table_rows(extension, data):
    result = []
    fields = 0
    for row in records(extension, data):
        fields += max(1, len(row))
        if fields > MAX_TABLE_FIELDS:
            raise ValueError("표 미리보기의 셀 한도를 초과했습니다.")
        result.append(row)
    return result


def address(row, column):
    letters = []
    column += 1
    while column:
        column, digit = divmod(column - 1, 26)
        letters.append(chr(ord("A") + digit))
    return "".join(reversed(letters)) + str(row + 1)


def lines(table):
    for row, record in enumerate(table):
        for column, value in enumerate(record):
            if value:
                yield f"{TABLE_NAME}!{address(row, column)}  {value!r}"


def normalized_table(extension, data):
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=SEPARATORS[extension], quoting=csv.QUOTE_ALL)
    writer.writerows(records(extension, data))
    return output.getvalue().encode("utf-8")


def keyed_rows(table, column):
    keyed = {}
    for record in table[1:]:
        if not any(record):
            continue
        key = record[column] if column < len(record) else ""
        if not key.strip() or key in keyed:
            return None
        keyed[key] = record
    return keyed


def key_columns(before, after):
    if not before or not after:
        return {}
    left_names, right_names = Counter(before[0]), Counter(after[0])
    left = [row for row in before[1:] if any(row)]
    right = [row for row in after[1:] if any(row)]
    width = min((len(row) for row in (*left, *right)), default=len(before[0]))
    return {
        name: column
        for column, (name, opposite) in enumerate(zip(before[0][:width], after[0]))
        if name.strip() and len(name) <= 255 and name == opposite and left_names[name] == right_names[name] == 1
        and unique_values(left, column) and unique_values(right, column)
    }


def unique_values(table, column):
    values = {row[column] for row in table}
    return len(values) == len(table) and all(value.strip() for value in values)


def aligned_rows(before, after, column):
    left, right = keyed_rows(before, column), keyed_rows(after, column)
    keys = list(left) + [key for key in right if key not in left]
    return [[table[0], *(index.get(key, []) for key in keys)]
            for table, index in ((before, left), (after, right))]


def sheet(table):
    cells = {address(row, col): [value, None, {}, "text"]
             for row, record in enumerate(table) for col, value in enumerate(record) if value}
    return {"name": TABLE_NAME, "cells": cells, "merges": [], "row_count": len(table),
            "column_count": max(map(len, table), default=0)}
