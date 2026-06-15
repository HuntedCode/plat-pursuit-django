"""Element presentation foundation (the user-facing "Element" skin over Jobs).

One source of truth for turning a profile's `ProfileJobXP` into the element tiles,
families, and summary the Lab and Research Panel render. The backend models stay
`Job` / `Contract`; this layer maps them to Elements / Families for display.

Presentation data that isn't on the model lives here as constants: the periodic-table
SYMBOLS (a designed 2-char mark per job), the family taglines, and the atom SHAPES
cycle (shape = slot within family; color = family). Flavor copy prefers the seeded
`Job.description`, falling back to DESCRIPTIONS.

Harvested from the `/design/lab/` workshop; the XP math here uses the REAL leveling
helpers + real `ProfileJobXP` rather than the workshop's sample numbers. All assembly
is bounded by the ~25-row Job catalog, so Python iteration is safe (no whale risk).
"""
import json

from trophies.models import Job, ProfileJobXP
from trophies.util_modules.constants import JOB_LEVEL_CAP
from trophies.util_modules.leveling import xp_for_level

# The 5 disciplines (families), in canonical radar/seed order.
DISCIPLINE_LABELS = {
    'combat': 'Combat', 'exploration': 'Exploration', 'mind': 'Mind',
    'heart': 'Heart', 'finesse': 'Finesse',
}
DISCIPLINE_TAGLINE = {
    'combat': 'You fight.', 'exploration': 'You discover.', 'mind': 'You outwit.',
    'heart': 'You feel.', 'finesse': 'You perform.',
}
# Atom shape per family slot: color = family, shape = slot index within the family.
SHAPES = ['circle', 'triangle', 'square', 'pentagon', 'hexagon']
# Curated periodic-table marks (cap + lowercase, all unique). A designed mark, not an
# auto-derived code. Could migrate onto Job.icon later; lives here as presentation for now.
SYMBOLS = {
    'slayer': 'Sl', 'gunslinger': 'Gn', 'vanguard': 'Vg', 'outlaw': 'Ol', 'warrior': 'Wr',
    'pathfinder': 'Pf', 'infiltrator': 'If', 'cartographer': 'Ca', 'mascot': 'Ms', 'survivalist': 'Sv',
    'mastermind': 'Mm', 'tactician': 'Tc', 'architect': 'Ar', 'tycoon': 'Ty', 'card-shark': 'Cs',
    'mage': 'Mg', 'champion': 'Ch', 'librarian': 'Lb', 'jester': 'Js', 'exorcist': 'Ex',
    'gamer': 'Gm', 'driver': 'Dr', 'athlete': 'At', 'maestro': 'Mo', 'freelancer': 'Fl',
}
# Flavor copy, used only when a job has no seeded description.
DESCRIPTIONS = {
    'slayer': "Crowds of enemies are just a to-do list.",
    'gunslinger': "If it moves, it's already in your sights.",
    'vanguard': "First through the door, last to fall back.",
    'outlaw': "Out here, the rules are more of a suggestion.",
    'warrior': "One on one, fists up. Settle it in the ring.",
    'pathfinder': "Every ledge is a question. You answer all of them.",
    'infiltrator': "They never knew you were there. That's the point.",
    'cartographer': "The map fills in behind you, one horizon at a time.",
    'mascot': "Bright worlds, big jumps, a grin the whole way.",
    'survivalist': "Cold, hungry, hunted, still standing.",
    'mastermind': "The solution was obvious. Eventually.",
    'tactician': "You saw the win three moves ago.",
    'architect': "You don't play the world. You build it.",
    'tycoon': "Buy low, plat high.",
    'card-shark': "The house doesn't always win.",
    'mage': "Spellbook in hand, fate in flux.",
    'champion': "Glory, measured in trophies. Naturally.",
    'librarian': "Every page turned is a story finished.",
    'jester': "You came for the story, stayed for the laughs.",
    'exorcist': "You walk toward the thing everyone else runs from.",
    'gamer': "High score isn't a goal, it's a personality.",
    'driver': "The apex belongs to you.",
    'athlete': "Reflexes, timing, and a podium with your name on it.",
    'maestro': "Every beat, right on time.",
    'freelancer': "A little of everything. A specialist in showing up.",
}


def element_dict(job, level, total_xp, *, atomic, slot_index):
    """Build one element tile from a job + the viewer's real level/XP for it.

    `atomic` is the running 1..N periodic number; `slot_index` is the job's slot within
    its family (drives the atom shape). XP fields use the real leveling curve. Every
    element is at least level 1 (the floor), so there is no "locked" state.
    """
    level = max(1, level)
    if level >= JOB_LEVEL_CAP:
        state = 'mastered'
        progress, xp_current, xp_next = 100, 0, 0
    else:
        state = 'active'
        floor = xp_for_level(level)
        ceil = xp_for_level(level + 1)
        into = max(0, total_xp - floor)
        span = max(1, ceil - floor)
        progress = min(100, round(into / span * 100))
        xp_current, xp_next = into, span

    return {
        'number': atomic,
        'name': job.name,
        'slug': job.slug,
        'disc_slug': job.discipline,
        'shape': SHAPES[slot_index % len(SHAPES)],
        'symbol': SYMBOLS.get(job.slug, job.name[:2]),
        'level': level,
        'progress': progress,
        'xp_current': f"{xp_current:,}",
        'xp_next': f"{xp_next:,}",
        'xp_total': f"{total_xp:,}",
        'state': state,
        'description': job.description or DESCRIPTIONS.get(job.slug, ''),
    }


def build_profile_elements(profile):
    """Assemble the full element/family view for a profile from real `ProfileJobXP`.

    Returns the `disciplines` list (each family with its element tiles + average level
    + per-family radar JSON), the overall radar series, the dominant family, the top
    element, and totals. Bounded by the Job catalog (~25 rows); safe to iterate.
    """
    rows = {
        r['job_id']: (r['level'], r['total_xp'])
        for r in ProfileJobXP.objects.filter(profile=profile).values('job_id', 'level', 'total_xp')
    }
    by_disc = {slug: [] for slug in DISCIPLINE_LABELS}
    for job in Job.objects.all():
        by_disc.setdefault(job.discipline, []).append(job)
    for jobs in by_disc.values():
        jobs.sort(key=lambda j: j.display_order)

    disciplines, radar_values, all_tiles = [], [], []
    total_level = total_xp = atomic = 0
    for slug, label in DISCIPLINE_LABELS.items():
        tiles = []
        for i, job in enumerate(by_disc.get(slug, [])):
            atomic += 1
            level, txp = rows.get(job.slug, (0, 0))
            tile = element_dict(job, level, txp, atomic=atomic, slot_index=i)
            tiles.append(tile)
            total_level += tile['level']  # floored (>= 1), so Pursuer Level counts every job
            total_xp += txp
        all_tiles.extend(tiles)
        avg = round(sum(t['level'] for t in tiles) / len(tiles), 1) if tiles else 0
        radar_values.append(avg)
        disciplines.append({
            'slug': slug, 'label': label, 'tagline': DISCIPLINE_TAGLINE[slug],
            'jobs': tiles, 'avg': avg,
            'radar_labels_json': json.dumps([t['name'] for t in tiles]),
            'radar_data_json': json.dumps([t['level'] for t in tiles]),
        })

    return {
        'disciplines': disciplines,
        'radar_labels_json': json.dumps(list(DISCIPLINE_LABELS.values())),
        'radar_data_json': json.dumps(radar_values),
        'dominant': max(disciplines, key=lambda d: d['avg']) if disciplines else None,
        'top_element': max(all_tiles, key=lambda t: (t['level'], t['progress'])) if all_tiles else None,
        'total_level': total_level,
        'total_xp': total_xp,
        'total': atomic,
    }
