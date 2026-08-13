from __future__ import annotations

import json
from io import BytesIO

from openpyxl import Workbook

from zpi_app.parsers import parse_txt_bytes, parse_xlsx_bytes


def test_txt_parser_accepts_json_fragments_without_outer_braces():
    payload = (
        '"message":"hello","className":"Example","serverEventDatetime":"2026-01-01T00:00:00Z"\n'
        '{"message":"world","serviceName":"/health"}\n'
        'not json\n'
    ).encode("utf-8")

    result = parse_txt_bytes("logs.txt", payload)

    assert [record.get("message") for record in result.records] == ["hello", "world"]
    assert result.records[0].get("CLASS_NAME") == "Example"
    assert len(result.issues) == 1
    assert result.issues[0].row_number == 3


def test_xlsx_parser_reads_named_columns():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "logs"
    sheet.append(["serverEventDatetime", "className", "message"])
    sheet.append(["2026-08-03T09:42:30.565Z", "Example", "payload"])
    buffer = BytesIO()
    workbook.save(buffer)

    result = parse_xlsx_bytes("logs.xlsx", buffer.getvalue())

    assert len(result.records) == 1
    assert result.records[0].get("message") == "payload"
    assert result.records[0].sheet_name == "logs"


def test_xlsx_parser_reads_single_cell_json_fragments():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(['"message":"payload","logLevel":"INFO"'])
    buffer = BytesIO()
    workbook.save(buffer)

    result = parse_xlsx_bytes("one-column.xlsx", buffer.getvalue())

    assert len(result.records) == 1
    assert result.records[0].get("logLevel") == "INFO"

