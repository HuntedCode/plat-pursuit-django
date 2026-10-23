"""PSN's completion percentage, recomputed from trophy counts.

PSN does not count trophies: it weighs them by grade (bronze 15, silver 30, gold 90) and leaves the platinum
out, then floors, except that anything earned reads at least 1%. Checked against 204 synced dev lists, where
it reproduced PSN's own `ProfileGame.progress` on every one (2026-10-22).

The group headers and the hero's composite bar use it so a list shows one percentage everywhere (GL-11): a
plain count put Jak 3's base game at 49% under a hero that said 45%.
"""

PSN_POINTS = {'bronze': 15, 'silver': 30, 'gold': 90}


def _n(value):
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def psn_progress(defined, earned):
    """The percentage PSN would show for `earned` ({grade: count}) out of `defined` ({grade: count}).

    A group whose only trophy is a platinum has no weight to measure, so it reads 100 once that platinum is
    earned and 0 before. Earned counts above the defined ones (a list that moved under Sony) are capped, so
    the result never passes 100."""
    defined = defined if isinstance(defined, dict) else {}
    earned = earned if isinstance(earned, dict) else {}
    total = sum(_n(defined.get(grade)) * points for grade, points in PSN_POINTS.items())
    if not total:
        plats = _n(defined.get('platinum'))
        return 100 if plats and _n(earned.get('platinum')) >= plats else 0
    got = sum(min(_n(earned.get(grade)), _n(defined.get(grade))) * points for grade, points in PSN_POINTS.items())
    pct = got * 100 // total
    return 1 if got and not pct else pct
