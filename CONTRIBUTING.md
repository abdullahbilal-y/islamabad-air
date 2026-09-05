# Contributing

This project exists because public data about the air in Islamabad is hard to
use. Anything that makes it easier to use is welcome.

Before changing the scraper or the ingest path, read
[brain/landmines.md](brain/landmines.md). It is short and it will save you from
re-introducing a bug we already found.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

The test suite runs entirely offline and takes a few seconds.

## Adding a data source

This is the most useful kind of contribution, and the interface is deliberately
small. Implement `Source` in `src/hawa/sources/base.py`:

```python
class MySource(Source):
    id = "my_source"            # stable forever; it is a database value
    name = "Human readable name"
    attribution = "Who produced the data"
    provides = frozenset({"air"})   # and/or "pollen"

    def available(self) -> tuple[bool, str | None]:
        # Return (False, "why") if this source cannot run — e.g. no API key.
        return True, None

    def fetch(self, client: httpx.Client) -> Fetched:
        ...   # network only

    def parse(self, fetched: Fetched) -> ParseResult:
        ...   # pure; no I/O
```

Then register it in `src/hawa/sources/__init__.py`. Nothing else changes.

Four rules, each of which exists because of a real failure:

1. **`fetch` and `parse` stay separate.** The raw body is persisted and
   committed before `parse` runs, so a parser bug never loses data that cannot
   be re-fetched.
2. **`parse` must be pure.** No network, no clock reads that change the output.
   That is what lets it be tested from a saved response.
3. **`parse` raises rather than returning empty.** An empty result and a broken
   parser are indistinguishable to the caller, and one of them is safe to serve
   while the other is not.
4. **Never invent a value.** If a field cannot be read, keep the raw text and
   leave the number null. A plausible wrong number is worse than a visible gap
   because nobody ever reviews it.

Every source needs:

- a real captured response in `tests/fixtures/` (byte-for-byte — do not tidy it);
- a parser test against that fixture;
- a test that a shape change raises rather than returning nothing;
- a natural key, so re-ingesting cannot duplicate rows.

## Other things worth doing

- **Run PurpleAir or OpenAQ against a real API key.** Neither has ever made a
  live authenticated call (landmine #8). Expect the response *shape* to be what
  is wrong. Reporting what breaks is genuinely valuable.
- **A notification channel.** `src/hawa/alerts/channels.py` — one function plus a
  registry entry. WhatsApp via a provider, web push, Slack, SMS.
- **Something built on the API.** A Telegram bot with better formatting, a home
  screen widget, a seasonal chart. Tell us and we will link it.
- **Historical backfill.** We only hold data from the day polling started. If you
  can find archived PMD press releases or PDFs, that history is worth a lot —
  ingest it through the same idempotent path and it will merge cleanly.

## Style

`ruff check .` and `mypy` must pass. Comments should explain *why*, not restate
what the line does; if a piece of code looks strange, the comment should say what
would go wrong if it were written the obvious way.

## Scraping politely

Please do not lower the default 30-minute poll interval. PMD publishes once a
day. The client sends an identifying User-Agent and caches identical responses.
The aim is for this data to be more available, not for a government server to
have a bad time.

## Reporting a broken scraper

If `/healthz` reports a `parse` failure, the raw body that failed is already in
the database — `raw_snapshot`. Attach it to the issue. That is usually enough to
fix the parser without waiting for the page to break again.
