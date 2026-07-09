"""Database layer: engine/session management, declarative base, and portable types.

The models are written to run on both PostgreSQL (production, via Supabase) and SQLite
(fast, network-free tests) by using the portable column types in :mod:`app.db.types`.
"""
