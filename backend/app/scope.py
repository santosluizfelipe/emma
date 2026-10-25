"""Which records a user may see: the org units (teams) they work in.

The scope is set by the server for each request (in production, from the logged-in
user's role), never by the model or the request body. Every tool and the SQL guard
apply it in the query itself, so access never depends on the model behaving, and a
coordinator's question touches their team's data, not the whole store.

None means "no restriction" (single tenant, local development, tests).
"""

from __future__ import annotations

from typing import List, Optional, Tuple

OrgUnits = Optional[Tuple[int, ...]]


def normalise(org_unit_ids) -> OrgUnits:
    """Accept None or any iterable of ids; return a sorted tuple of ints."""
    if org_unit_ids is None:
        return None
    return tuple(sorted({int(u) for u in org_unit_ids}))


def _marks(org_units: Tuple[int, ...]) -> str:
    return ",".join("?" * len(org_units))


def people_filter(column: str, org_units: OrgUnits) -> Tuple[str, List[int]]:
    """SQL condition limiting `column` (a service_users.id) to people in scope."""
    if org_units is None:
        return "1 = 1", []
    return (f"{column} IN (SELECT id FROM service_users WHERE org_unit_id IN ({_marks(org_units)}))",
            list(org_units))


def forms_filter(column: str, org_units: OrgUnits) -> Tuple[str, List[int]]:
    """SQL condition limiting `column` (a forms.id) to forms owned by an org unit in scope.

    Forms are scoped by their own org unit, so a safeguarding form that names no
    linked person is still visible to the team that raised it.
    """
    if org_units is None:
        return "1 = 1", []
    return f"{column} IN (SELECT id FROM forms WHERE org_unit_id IN ({_marks(org_units)}))", list(org_units)


def chunks_filter(alias: str, org_units: OrgUnits) -> Tuple[str, List[int]]:
    """SQL condition for search chunks: visits by their person, forms by their org unit."""
    if org_units is None:
        return "1 = 1", []
    people, p1 = people_filter(f"{alias}.service_user_id", org_units)
    forms, p2 = forms_filter(f"{alias}.record_id", org_units)
    return (f"(({alias}.record_type = 'visit' AND {people}) OR ({alias}.record_type = 'form' AND {forms}))",
            p1 + p2)
