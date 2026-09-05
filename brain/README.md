# Project brain

The project-specific mental model for this repo — what a new contributor (human
or agent) reads first. Keep it grounded in real file paths; update it in the same
change that changes behaviour; when docs and code disagree, trust the code.

Hawa scrapes the Pakistan Meteorological Department's daily Islamabad pollen
counts (sectors H-8, E-8, G-6, F-10) off a JavaScript-rendered page, keeps the
history, joins it with community air-quality sensors, and serves it as a public
REST API with a dashboard and a threshold alert engine. Most of the design is a
response to one fact: **the upstream is a page we have no agreement with, and its
failures are silent.**

Start with [landmines.md](landmines.md) (the most valuable page), then:
- [system-overview.md](system-overview.md) — what this system does, at a glance.
- [architecture.md](architecture.md) — components and how they fit together.
- [file-map.md](file-map.md) — where things live.
- [operations.md](operations.md) — how to build, run, test, deploy, observe.
- [glossary.md](glossary.md) — project/domain terms.
