# Sealed evidence

This orphan branch holds only `data/`: what the research routines used or sent, byte for byte, each folder with a
`MANIFEST.json` of sha256 hashes. Never edit or re-format a file here. A correction is a new folder
`<session>.r2` whose manifest names the folder it replaces. Layout and rules: `src/options_funnel/seal.py` and
`docs/runbook.md` on `main`.
