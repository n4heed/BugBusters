"""Durable SQLite persistence for the dataset-import lifecycle.

See db.py (connection/schema) and repository.py (serialization + CRUD).
Nothing in the agent pipeline (Triage/Investigation/Verification) reads or
writes through this package — it is scoped to dataset metadata, uploaded
file bytes, field mappings, validation reports and canonical records only.
"""
