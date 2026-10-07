# Provenance

The authenticated board service, database, client and replay frontend were
adapted from the local ExploitBench collaboration implementation. The original
repository is MIT licensed; its license is retained in THIRD_PARTY_LICENSES.
Exact working-tree source hashes are in docs/import-provenance.json.

Counting and spelling rules are adapted from Pablo's local Multi-Agent-Bench,
commit a3d55c0679eb08c0b47a9976b5a2bc059d6f9659. Graph colouring is adapted from
its `colouring_local` Hawk task and planted-graph generator on the
`james/leader` branch, commit e633b04cd109b6616a0273a3dd27724711814a5c.
Source repositories are unchanged.
The new board, disclosed identities, seeded draws, fixed-work counting
condition and DM-based colouring messages change comparability; this is not a reproduction of those runs.

HLE data is loaded from CAIS and the HLE-Verified gold-ID annotation, at the
revisions used by the current Inspect Evals HLE adapter. Data is not vendored. Authenticated revision/schema checks loaded 575 gold/text questions;
CAIS requires approved Hugging Face access. Real data stays in the local dataset
cache, and no model evaluation on it was launched.
The collaboration prompt and batch submission interface are new, so results
are not ordinary HLE leaderboard scores. Dataset license/access requirements
remain those of the original publishers.
