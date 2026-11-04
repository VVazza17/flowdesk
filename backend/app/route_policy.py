"""The public-profile route invariant (IDEA-019, B-minimal, docs/SPEC-DEFECTS.md).

Every route uses only GET, HEAD or OPTIONS, or is admin-guarded. Anything this check cannot
classify fails it: a check that ignores what it does not understand gives false assurance. When
the §9 `WS /ws` route arrives in Phase 2a this is meant to break and force a decision.

DECISION: no admin-guarded branch yet. No admin route exists, so there is nothing to recognise a
guard on, and inventing a marker now would let a route pass on a promise. The first admin route
has to define what "guarded" means, here.

What this does not see: a GET handler that writes (IDEA-019 assigns that to the slice introducing
the request-scoped DB session) and anything answering from ASGI middleware rather than a route.
"""

from collections.abc import Sequence

from fastapi.routing import iter_route_contexts
from starlette.routing import BaseRoute, Route

READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def public_route_violations(routes: Sequence[BaseRoute]) -> list[str]:
    """Return one message per route that breaks the invariant; empty means it holds.

    iter_route_contexts, not a loop over `routes`: under FastAPI 0.162.0 an included router is one
    opaque `_IncludedRouter` in app.routes, so a plain loop would see nothing and pass vacuously.
    It flattens nested includes and reports effective (prefixed) paths.
    """
    violations: list[str] = []
    seen = 0
    for ctx in iter_route_contexts(routes):
        seen += 1
        # Allow-list on type: WebSockets, Mounts (their sub-app's routes are invisible from here),
        # Hosts and unknown BaseRoute subclasses all land here.
        if not isinstance(ctx.route, Route):
            label = ctx.path or repr(ctx.route)
            violations.append(f"{label}: cannot classify {type(ctx.route).__name__}")
            continue
        methods = ctx.methods
        if not methods:
            violations.append(f"{ctx.path}: no declared methods, cannot classify")
        elif extra := set(methods) - READ_ONLY_METHODS:
            violations.append(f"{ctx.path}: not read-only ({', '.join(sorted(extra))})")
    if seen == 0:
        # An empty walk is far likelier to mean the walker broke than that the app has no routes.
        violations.append("walk found no routes, so nothing was checked")
    return violations
