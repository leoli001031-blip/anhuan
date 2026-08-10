"""Deterministic, local-only routing for the registered Fixture set."""

from .router import RouteFailure, build_route_plan, write_route_outputs

__all__ = ["RouteFailure", "build_route_plan", "write_route_outputs"]
