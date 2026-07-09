"""Business-logic services.

Services own all domain rules and database writes. Routers call services; services
never import routers. Each service function takes an explicit ``Session`` (and any
providers it needs) so it is unit-testable without HTTP or global state.
"""
