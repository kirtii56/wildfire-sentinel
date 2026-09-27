# Test fixtures

Every file in this directory is **synthetic test data**. None of it is a real
NASA FIRMS observation. It exists only to exercise parsing, validation and
loading code paths in the test suite.

Synthetic data never leaves this directory: the only code that writes to a real
database is `scripts/run_ingest.py`, which writes only what FIRMS returned.
