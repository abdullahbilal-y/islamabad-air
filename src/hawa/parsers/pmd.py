"""Parse PMD's Islamabad pollen page.

Why this file is defensive
--------------------------
PMD's page is not an API we are entitled to. It is a public government page
whose markup can change without notice, and when it does the failure is
*silent*: the fetch still returns 200 OK, the parser just finds nothing. So:

* three independent strategies are tried in order, and the one that worked is
  recorded on the snapshot -- that record is how you notice a redesign;
* "200 OK but zero rows" is treated as a **parse failure**, not an empty day,
  because those two look identical from the outside and only one is normal;
* values are kept exactly as printed. ``"00"`` stays ``"00"`` in ``value_raw``
  even though ``value`` becomes ``0``, and anything we cannot read is flagged
  for review rather than coerced into a plausible-looking number.

Observed structure as of 2026-09-05 (see tests/fixtures/):

    const initialRows = (function() {
        const rows = [];
        rows.push({ type: "Acacia", 'H-8': "00", 'E-8': null, ..., category: "Absent" });

with ``<div class="last-updated" id="lastUpdated">Last Updated: September 4, 2026</div>``
and a month-scoped JSON endpoint at ``/rnd/pollen-data/ajax-data?month=...``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

PKT = ZoneInfo("Asia/Karachi")

#: Keys in a pushed row object that are metadata, not a sector column.
_NON_SECTOR_KEYS = {"type", "category"}

_ROW_PUSH_RE = re.compile(r"rows\.push\(\s*\{(?P<body>.*?)\}\s*\)\s*;", re.DOTALL)

_KV_RE = re.compile(
    r"""(?:'(?P<k1>[^']+)'|"(?P<k2>[^"]+)"|(?P<k3>[A-Za-z_][\w\-]*))\s*:\s*"""
    r"""(?:"(?P<v_str>[^"]*)"|'(?P<v_str2>[^']*)'|(?P<v_null>null)|(?P<v_num>-?\d+(?:\.\d+)?))"""
)

_LAST_UPDATED_RE = re.compile(
    r"""id=["']lastUpdated["'][^>]*>\s*(?:Last\s+Updated:)?\s*(?P<when>[^<]+?)\s*<""",
    re.IGNORECASE,
)

_DATE_FORMATS = ("%B %d, %Y", "%d %B %Y", "%Y-%m-%d", "%d-%m-%Y", "%b %d, %Y")


class PollenParseError(RuntimeError):
    """Raised when no strategy could extract rows from a fetched body."""


@dataclass(slots=True)
class PollenRow:
    """One pollen type in one sector, as printed by PMD."""

    sector: str
    pollen_type: str
    value_raw: str | None
    value: int | None
    source_category: str | None = None
    needs_review: bool = False
    review_reason: str | None = None


@dataclass(slots=True)
class ParsedPollen:
    observed_date: date
    rows: list[PollenRow] = field(default_factory=list)
    strategy: str = "unknown"
    #: Sector columns the page actually rendered, in page order.
    sectors_seen: list[str] = field(default_factory=list)
    last_updated_text: str | None = None


def today_pkt() -> date:
    """PMD publishes on Pakistan Standard Time; a UTC 'today' is wrong after 19:00 PKT."""
    return datetime.now(PKT).date()


def _parse_date_text(text: str | None) -> date | None:
    if not text:
        return None
    cleaned = text.strip().strip(".")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _coerce_value(raw: str | None) -> tuple[int | None, bool, str | None]:
    """Return ``(value, needs_review, reason)`` for a printed cell.

    A cell we cannot read keeps its raw text and is flagged. We never guess a
    number for it -- a plausible wrong value is worse than a visible gap,
    because nobody reviews a number that looks fine.
    """
    if raw is None:
        return None, False, None
    text = raw.strip()
    if text == "" or text in {"-", "--", "N/A", "NA", "n/a"}:
        return None, False, None
    if re.fullmatch(r"\d+", text):
        return int(text), False, None
    # Things like "1,234" are still unambiguous; anything else is not.
    if re.fullmatch(r"[\d,]+", text):
        return int(text.replace(",", "")), False, None
    return None, True, f"unparseable value {text!r}"


def _kv_pairs(body: str) -> list[tuple[str, str | None]]:
    pairs: list[tuple[str, str | None]] = []
    for m in _KV_RE.finditer(body):
        key = m.group("k1") or m.group("k2") or m.group("k3")
        if m.group("v_null") is not None:
            value: str | None = None
        elif m.group("v_str") is not None:
            value = m.group("v_str")
        elif m.group("v_str2") is not None:
            value = m.group("v_str2")
        else:
            value = m.group("v_num")
        pairs.append((key, value))
    return pairs


def _rows_from_objects(objects: list[dict[str, str | None]]) -> tuple[list[PollenRow], list[str]]:
    sectors_seen: list[str] = []
    rows: list[PollenRow] = []
    for obj in objects:
        pollen_type = (obj.get("type") or "").strip()
        if not pollen_type:
            continue
        category = obj.get("category")
        for key, raw in obj.items():
            if key in _NON_SECTOR_KEYS:
                continue
            sector = key.strip()
            if sector not in sectors_seen:
                sectors_seen.append(sector)
            # A null cell means "this sector's trap reported nothing today".
            # That is real information, but it is not a zero -- skip it rather
            # than inventing a 0 that would drag every average down.
            if raw is None:
                continue
            value, needs_review, reason = _coerce_value(raw)
            rows.append(
                PollenRow(
                    sector=sector,
                    pollen_type=pollen_type,
                    value_raw=raw,
                    value=value,
                    source_category=category,
                    needs_review=needs_review,
                    review_reason=reason,
                )
            )
    return rows, sectors_seen


def _strategy_initial_rows(body: str) -> tuple[list[PollenRow], list[str]]:
    """Primary: the server-rendered ``rows.push({...})`` block."""
    objects: list[dict[str, str | None]] = []
    for match in _ROW_PUSH_RE.finditer(body):
        obj = dict(_kv_pairs(match.group("body")))
        if obj:
            objects.append(obj)
    return _rows_from_objects(objects)


def _strategy_json(body: str) -> tuple[list[PollenRow], list[str]]:
    """Secondary: the ajax-data JSON payload (``{"rows": [...]}``)."""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise PollenParseError("body is not JSON") from exc
    raw_rows = payload.get("rows") if isinstance(payload, dict) else payload
    if not isinstance(raw_rows, list):
        raise PollenParseError("JSON payload has no 'rows' list")
    objects = [
        {k: (None if v is None else str(v)) for k, v in row.items()}
        for row in raw_rows
        if isinstance(row, dict)
    ]
    return _rows_from_objects(objects)


def _strategy_html_table(body: str) -> tuple[list[PollenRow], list[str]]:
    """Fallback: a genuinely server-rendered ``<table>``.

    PMD renders client-side today, so this path is dormant -- it exists because
    "they went back to plain HTML" is a cheaper thing to already support than
    to discover during a pollen spike.
    """
    from selectolax.parser import HTMLParser

    tree = HTMLParser(body)
    for table in tree.css("table"):
        # Only the first header row, and only once: a "thead th, tr:first-child th"
        # selector matches the same cells twice and doubles every column.
        header_row = table.css_first("thead tr") or table.css_first("tr")
        if header_row is None:
            continue
        header_cells = [c.text(strip=True) for c in header_row.css("th")]
        if len(header_cells) < 2:
            continue
        sector_cols = [
            (idx, name)
            for idx, name in enumerate(header_cells)
            if re.fullmatch(r"[A-Z]-\d{1,2}", name.strip(), re.IGNORECASE)
        ]
        if not sector_cols:
            continue
        objects: list[dict[str, str | None]] = []
        body_rows = table.css("tbody tr") or [r for r in table.css("tr") if r.css("td")]
        for tr in body_rows:
            cells = [c.text(strip=True) for c in tr.css("td")]
            if not cells:
                continue
            label = cells[0]
            if not label or label.upper() == "TOTAL":
                continue
            obj: dict[str, str | None] = {"type": label}
            for idx, name in sector_cols:
                obj[name.strip().upper()] = cells[idx] if idx < len(cells) else None
            objects.append(obj)
        if objects:
            return _rows_from_objects(objects)
    raise PollenParseError("no sector table found in HTML")


#: Ordered strategies. The name that succeeds is persisted on the snapshot;
#: a sudden change of strategy is the earliest signal that PMD redesigned.
STRATEGIES = (
    ("initial_rows_js", _strategy_initial_rows),
    ("ajax_json", _strategy_json),
    ("html_table", _strategy_html_table),
)


def parse_pmd_pollen(body: str, *, fallback_date: date | None = None) -> ParsedPollen:
    """Extract pollen rows from a PMD response body.

    Raises :class:`PollenParseError` if every strategy came up empty. That is
    deliberately an error and not an empty result: a 200 OK with no rows is
    what an upstream redesign looks like, and swallowing it would let the API
    serve a confident "no pollen today" during peak season.
    """
    errors: list[str] = []
    for name, strategy in STRATEGIES:
        try:
            rows, sectors = strategy(body)
        except PollenParseError as exc:
            errors.append(f"{name}: {exc}")
            continue
        except Exception as exc:  # a strategy blowing up must not stop the next one
            errors.append(f"{name}: unexpected {type(exc).__name__}: {exc}")
            continue
        if rows:
            last_updated_text = None
            match = _LAST_UPDATED_RE.search(body)
            if match:
                last_updated_text = match.group("when").strip()
            observed = _parse_date_text(last_updated_text) or fallback_date or today_pkt()
            return ParsedPollen(
                observed_date=observed,
                rows=rows,
                strategy=name,
                sectors_seen=sectors,
                last_updated_text=last_updated_text,
            )
        errors.append(f"{name}: produced 0 rows")

    raise PollenParseError(
        "no strategy extracted pollen rows -- PMD's page shape probably changed. "
        "Tried: " + "; ".join(errors)
    )
