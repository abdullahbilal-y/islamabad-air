# Glossary

| Term | Meaning |
|---|---|
| **PMD** | Pakistan Meteorological Department. Publishes the daily Islamabad pollen count. |
| **Sector** | An Islamabad administrative block. PMD runs pollen traps in H-8, E-8, G-6 and F-10. |
| **Paper mulberry** | *Broussonetia papyrifera*. Planted widely in Islamabad decades ago; now the dominant spring allergen and the reason the counts get so extreme. |
| **grains/m³** | Pollen grains per cubic metre of air over 24 hours. The unit PMD reports. |
| **Band** | Our severity label for a sector's daily total: absent / low / moderate / high / very_high. Thresholds live in settings. |
| **`source_category`** | The severity word PMD itself printed. Stored verbatim and kept separate from our band, so a change on their side never rewrites our history. |
| **Snapshot** | A `raw_snapshot` row: the exact response body we fetched, stored before parsing. |
| **Strategy** | Which of the parser's approaches succeeded (`initial_rows_js`, `ajax_json`, `html_table`). Recorded per snapshot; a sudden change signals an upstream redesign. |
| **Natural key** | The real-world identity of an observation (source + date + sector + type). What makes re-ingest idempotent. |
| **Dedup key** | Subscription + kind + sector + period. Stops a 30-minute poll re-alerting on a once-daily figure. |
| **Outcome health** | Health measured on "did the fetch produce rows", not on "did the connection open". |
| **PKT** | Pakistan Standard Time, UTC+5. All observation dates are PKT. |
| **PurpleAir / OpenAQ** | Community and reference air-quality networks respectively. |
