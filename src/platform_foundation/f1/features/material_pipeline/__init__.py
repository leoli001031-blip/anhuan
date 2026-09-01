"""Local-only automatic material processing pipeline.

Keep this package initializer dependency-free: the generic ``f1_worker``
imports the local index entrypoint and must not import the API/report service
graph as a side effect of package initialization.
"""
