"""A developer badge's studio FAMILY: which companies count, which games they developed, and which studio a
game is filed under.

IGDB credits the studio that did the work (Rockstar North, Rockstar San Diego), and the umbrella label
(Rockstar Games) is usually credited only as publisher, so a developer badge has to see the whole family
rather than the one Company row on `BadgeSeries.developer`.

Used by the nightly coverage audit (what is missing) and `generate_badge_stages` (what to build, ordered by
filing studio). The badge detail page's planned per-studio sections are meant to file stages with the same
`filing_studio`, so the generator's studio order and the page's sections agree.
"""

from collections import Counter

from django.db.models import Q

from trophies.models import Company, ConceptCompany


def developer_family_ids(company):
    """Ids of `company`'s studio family: `company`, every subsidiary below it (any depth), every former
    identity of any of them, and `company`'s own NEWER identities.

    Walks DOWN the IGDB tree from `company` only: `Company.subsidiaries` (the `parent` FK) and
    `Company.previous_identities` (the `changed_company` FK, i.e. studios renamed or merged INTO a family
    member). It never walks up to a parent or across to a sibling, so a Rockstar badge does not pull in the
    rest of Take-Two.

    The newer identities (`company.rename_chain()`) count for their OWN credits only, so a badge pinned to a
    studio's old name still covers what it ships under the new one. Their trees are deliberately NOT walked:
    IGDB models a merger as both predecessors pointing at the merged company (Square -> Square Enix <- Enix),
    so walking down from the successor would sweep in the merger partner and the successor's subsidiaries.
    Point the badge at the current company to cover its whole current family.

    Queries: one per rename hop, then one per level of the tree (plus the final, empty one). The `seen` set
    makes the walk cycle-safe: IGDB data is user-edited and a parent/rename loop must not spin forever.

    IGDB's `parent` is the CURRENT owner, so a studio acquired later brings its pre-acquisition games with
    it. That over-reach is accepted because a curator reviews it either way: the audit only flags, and
    `generate_badge_stages` is meant to be previewed with `--dry-run` before it writes.
    """
    seen = {c.id for c in company.rename_chain()}
    frontier = {company.id}
    while frontier:
        frontier = set(
            Company.objects.filter(
                Q(parent_id__in=frontier) | Q(changed_company_id__in=frontier)
            ).exclude(id__in=seen).values_list('id', flat=True)
        )
        seen |= frontier
    return seen


def family_developer_credits(family_ids):
    """{concept_id: {company_id: company name}} for every family studio credited as DEVELOPER.

    Querying ConceptCompany directly keeps "family company" and "is_developer" on the SAME row, so a game the
    umbrella only published (and an outside studio developed) has no entry. A row that is developer AND
    publisher (a studio that self-publishes) still counts; publish/port/support-only rows never do.
    """
    rows = ConceptCompany.objects.filter(company_id__in=family_ids, is_developer=True)
    credits = {}
    for concept_id, company_id, name in rows.values_list('concept_id', 'company_id', 'company__name'):
        credits.setdefault(concept_id, {})[company_id] = name
    return credits


def sorted_studio_names(studios):
    """The distinct names in a `{company_id: name}` map, case-insensitively sorted, for display.
    `Company.name` is not unique, so two family studios can share one."""
    return sorted(set(studios.values()), key=str.lower)


def studio_filing_map(developer, family_ids):
    """{company_id: (company_id, name) it is FILED under} for every company in the family. One query.

    Owner rule: renames join, mergers split. A company follows its `changed_company` forward while that is a
    PLAIN RENAME: the target is in the family and this company is the target's only predecessor there (DMA
    Design -> Rockstar North files under Rockstar North). When several companies point at one target it was
    a MERGER (Square + Enix -> Square Enix), each predecessor was a real studio of its own, and it keeps its
    own row. Anything that reaches one of the developer's own identities (`rename_chain()`: itself and its
    newer names) files under the developer's row.

    A merger partner with no local Company row is invisible here, so that merger reads as a rename: a data
    limit. The `seen` set stops a rename loop.
    """
    rows = list(Company.objects.filter(id__in=family_ids).values_list('id', 'name', 'changed_company_id'))
    names = {company_id: name for company_id, name, _ in rows}
    successor = {company_id: target for company_id, _, target in rows}
    predecessor_count = Counter(target for _, _, target in rows if target in names)
    developer_ids = {c.id for c in developer.rename_chain()}

    filing = {}
    for company_id in names:
        node, seen = company_id, {company_id}
        while node not in developer_ids:
            target = successor[node]
            if target not in names or predecessor_count[target] != 1 or target in seen:
                break
            seen.add(target)
            node = target
        filing[company_id] = (developer.id, developer.name) if node in developer_ids else (node, names[node])
    return filing


def filing_studio(studios, filing_map, developer):
    """Which studio a game (or a stage of several games) is filed under, as `(company_id, name)`.

    `studios` is the `{company_id: name}` map of family studios credited as developer, and `filing_map` is
    `studio_filing_map()` (computed once by the caller), so a credit to an old name counts as its current
    studio. The developer wins whenever it is credited; otherwise the alphabetically first credited studio,
    ties broken by id so the pick is stable. None when no family studio is credited.
    """
    filed = {filing_map[company_id] for company_id in studios if company_id in filing_map}
    if not filed:
        return None
    developer_row = (developer.id, developer.name)
    if developer_row in filed:
        return developer_row
    return min(filed, key=lambda row: (row[1].lower(), row[0]))


def studio_sort_key(studio, developer):
    """Sort key for a `filing_studio()` result: the badge's developer first, then studios A-Z, then the
    unattributed (None) last."""
    if studio is None:
        return (2, '', 0)
    company_id, name = studio
    if company_id == developer.id:
        return (0, '', 0)
    return (1, name.lower(), company_id)
