# Archive — Pre-Variant-C Migration

Archived on 3 May 2026 as part of Variant C calendar strategy
migration. These files are from the original Proppa Kraken
Supertrend+RSI strategy (retired 2 May 2026).

## Contents

- **`bak_files/`** — backup snapshots from various development
 sessions during the original Proppa Kraken bot build. File
 suffixes indicate the development phase or session that
 produced them (e.g. `_session3`, `_closepath`, `_stoploss`).

- **`patch_scripts/`** — one-off migration/patch scripts that
 were applied to the working files during development. All
 patches in this directory were applied to the working tree
 before archival. Scripts are kept for historical reference
 only — they will not run cleanly against the current working
 tree.

- **`old_docs/`** — historical milestone documentation
 (PHASE_4_MILESTONE_FOLIO.html) and a diagnostic script
 (diag_b1.py) from the original development phase.

## Status

The retired Supertrend+RSI strategy code lives in git history
under tag `v1-supertrend-retired-2026-05-02`. All work moving
forward happens on the `variant-c-calendar` branch.

Do not delete — kept for historical reference and rollback
safety.
