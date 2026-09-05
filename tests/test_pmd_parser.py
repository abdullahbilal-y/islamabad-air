"""Parser tests, all against the real captured page or realistic mutations of it."""

from __future__ import annotations

import json
from datetime import date

import pytest

from hawa.parsers.pmd import PollenParseError, parse_pmd_pollen


def test_parses_the_real_pmd_page(pmd_page):
    parsed = parse_pmd_pollen(pmd_page)

    assert parsed.strategy == "initial_rows_js"
    assert parsed.observed_date == date(2026, 9, 4)
    assert parsed.last_updated_text == "September 4, 2026"
    # All four monitored sectors appear as columns even when only one reports.
    assert parsed.sectors_seen == ["H-8", "E-8", "G-6", "F-10"]

    types = {row.pollen_type for row in parsed.rows}
    assert "Paper Mulberry" in types
    assert types == {
        "Acacia",
        "Alternaria",
        "Cannabis",
        "Dandelion",
        "Eucalyptus",
        "Grasses",
        "Paper Mulberry",
        "Pines",
    }


def test_null_cells_are_omitted_not_zeroed(pmd_page):
    """A sector that reported nothing must not become a zero.

    On the captured day only H-8 had readings. Recording 0 for the other three
    would make every city-wide average wrong, and the mistake is invisible
    afterwards because 0 is a perfectly plausible pollen count.
    """
    parsed = parse_pmd_pollen(pmd_page)
    assert {row.sector for row in parsed.rows} == {"H-8"}


def test_keeps_the_raw_string_alongside_the_parsed_number(pmd_page):
    parsed = parse_pmd_pollen(pmd_page)
    acacia = next(r for r in parsed.rows if r.pollen_type == "Acacia")
    # PMD prints "00", not "0". We keep both: the text and our reading of it.
    assert acacia.value_raw == "00"
    assert acacia.value == 0
    assert acacia.source_category == "Absent"
    assert acacia.needs_review is False


def test_unreadable_value_is_flagged_not_guessed():
    body = """
    rows.push({ type: "Paper Mulberry", 'H-8': "1O2", category: "High" });
    """
    parsed = parse_pmd_pollen(body, fallback_date=date(2026, 4, 1))
    row = parsed.rows[0]
    assert row.value is None, "must not invent a number for an unreadable cell"
    assert row.value_raw == "1O2"
    assert row.needs_review is True
    assert "1O2" in (row.review_reason or "")


def test_thousands_separators_are_read():
    body = 'rows.push({ type: "Paper Mulberry", \'H-8\': "46,132", category: "Very High" });'
    parsed = parse_pmd_pollen(body, fallback_date=date(2026, 3, 19))
    assert parsed.rows[0].value == 46132


def test_falls_back_to_json_strategy():
    body = json.dumps(
        {
            "rows": [
                {"type": "Pines", "H-8": "40", "E-8": None, "category": "Moderate"},
            ],
            "lastUpdated": "March 19, 2026",
        }
    )
    parsed = parse_pmd_pollen(body, fallback_date=date(2026, 3, 19))
    assert parsed.strategy == "ajax_json"
    assert parsed.rows[0].pollen_type == "Pines"
    assert parsed.rows[0].value == 40


def test_falls_back_to_html_table():
    body = """
    <html><body><table>
      <thead><tr><th>Type</th><th>H-8</th><th>G-6</th></tr></thead>
      <tbody>
        <tr><td>Paper Mulberry</td><td>46132</td><td>12662</td></tr>
        <tr><td>TOTAL</td><td>46132</td><td>12662</td></tr>
      </tbody>
    </table></body></html>
    """
    parsed = parse_pmd_pollen(body, fallback_date=date(2026, 3, 19))
    assert parsed.strategy == "html_table"
    assert len(parsed.rows) == 2, "the TOTAL row must not be ingested as a pollen type"
    assert {r.sector for r in parsed.rows} == {"H-8", "G-6"}


def test_a_200_with_no_rows_is_an_error_not_an_empty_day():
    """The single most important assertion in this file.

    If PMD redesigns the page, we get a cheerful 200 OK whose body we cannot
    read. Returning "no pollen today" there would be a confident lie during
    peak season. It has to raise so health goes red and somebody looks.
    """
    body = "<html><body><h1>Site under maintenance</h1></body></html>"
    with pytest.raises(PollenParseError) as exc:
        parse_pmd_pollen(body)
    assert "shape probably changed" in str(exc.value)


def test_falls_back_to_pkt_today_when_no_date_is_printed():
    from hawa.parsers.pmd import today_pkt

    body = 'rows.push({ type: "Pines", \'H-8\': "5", category: "Low" });'
    parsed = parse_pmd_pollen(body)
    assert parsed.observed_date == today_pkt()
