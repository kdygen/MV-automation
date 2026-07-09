"""External service providers behind narrow interfaces.

Every third-party dependency (distance/geocoding, email, LLM) is wrapped in a small
protocol with at least two implementations: a real one and an in-memory fake. The fake
is the default in development and tests, so the platform runs end-to-end with no
network access or API keys. Providers are selected by name from settings.
"""
