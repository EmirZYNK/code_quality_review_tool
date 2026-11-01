# Changelog

## Unreleased — dynamic before/after

### Added
- Side-by-side Before/After view (`quality_studio/diffview.py`): line diff, `resolved` / `new` finding chips,
  "only changed lines" toggle.
- Per-patch static effect (`impact.patch_effects`): score change shown next to every suggested patch, plus a live
  preview (score, resolved, new issues) for the currently selected patches before they are applied.
- Live analysis toggle in the Code Review tab: re-analyzes automatically when the editor content changes.
- Security rules SEC112–SEC115: `random` used for secrets (context-aware: assignment target, dict key, keyword
  argument or enclosing function name), credentials embedded in URL literals (password masked in the message),
  `requests` calls without a timeout or with `timeout=None`, and `input()` reaching `open()` within one scope
  (flow-sensitive, with basename/allow-list/prefix-check sanitizers).
- ruff `S311`/`S113` and bandit `B311`/`B113` now corroborate SEC112/SEC114.
- 40 ground-truth cases for the new rules (4 documented known limitations); evaluation set is now 179 snippets.

### Changed
- SEC109 ground-truth cases pass `timeout=10` so they test TLS verification only.

## 2.0.0 — rebuild

Replaces the earlier v3–v10 line (two apps, three READMEs) with one package and one UI.

### Added
- Rule registry (`quality_studio/rules/catalog.py`): id, category, severity, confidence, CWE mapping,
  threshold configuration (`Config`), `# noqa` / `# cqs: ignore[RULE]` suppression.
- New rules: SEC107–SEC111, CQ105–CQ108, PERF103, RED001–RED006, AIP001–AIP005, categories
  Code Smell and Redundancy.
- AI-generation indicators (probabilistic, severity `info`, excluded from the score).
- Optional ruff/bandit adapters that corroborate AST findings (`--external`).
- CLI with `text`/`json`/`sarif` output and exit codes (0 clean, 1 findings, 2 error); scans files,
  directories and ZIP archives.
- Statistical dataset comparison: Mann-Whitney U, Cliff's delta, bootstrap CI, Fisher exact test on
  syntax-error rates, Bonferroni adjustment. Rule evaluation against 139 hand-labeled snippets.
- ZIP project scan wired into the UI; technical report in `docs/TEKNIK_RAPOR.md`.

### Fixed
- Patch engine corrupted code containing non-ASCII text (ast byte offsets used as character indexes);
  line splitting now follows ast (`\n`, `\r\n`, `\r`).
- Benchmark timings were inflated ~10-16x by tracemalloc; time and memory are now separate rounds, and
  argument copying is outside the timer.
- Docker benchmark: image pull has its own timeout; containers are named and killed on timeout.
- SEC104 flags only dynamically built SQL; PERF102 no longer assumes `list`; SEC105 is word-based and covers
  attributes, annotated assignments, dict keys, defaults and keyword arguments.
- Nested-loop detection covers `for`/`while`/comprehensions and ignores linear traversals
  (`for cell in row`); long-function rule uses line span; one complexity definition; size-aware score
  that no longer saturates at 0.
- Dataset comparison reports (instead of silently dropping) syntax-error samples, validates CSV columns and
  lifts the 128 KB csv field limit.

### Removed
- Gemini/LLM code, `.env`, `ai_engine/`, legacy transformer, stub benchmark/sandbox modules, stale formatter,
  `advanced_app.py`, README_V7/V10, committed `__pycache__`.
