"""Pydantic request/response schemas for the HTTP API.

Schemas are the API contract; ORM models are storage. Keeping them separate lets the
database evolve without breaking clients (and vice versa).
"""
