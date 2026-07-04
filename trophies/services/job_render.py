"""Job presentation foundation.

One source of truth for turning a profile's `ProfileJobXP` into the job tiles, disciplines,
and summary the Career surface + Contracts board render. Maps the `Job` / `Contract` models
to their display shape (tiles grouped by the 5 disciplines).

Presentation data that isn't on the model lives here as constants: the discipline taglines +
header icons, and the flavor/criteria copy (flavor prefers the seeded `Job.description`,
falling back to DESCRIPTIONS). SYMBOLS / SHAPES are legacy marks still read by the `/design/*`
workshops. The XP math uses the REAL leveling helpers + real `ProfileJobXP`. All assembly is
bounded by the ~25-row Job catalog, so Python iteration is safe (no whale risk).
"""
import json
import math
import random

from trophies.models import Job, ProfileJobXP
from trophies.util_modules.leveling import xp_for_level, tier_for_level

# The 5 disciplines (families), in canonical radar/seed order.
DISCIPLINE_LABELS = {
    'combat': 'Combat', 'exploration': 'Exploration', 'mind': 'Mind',
    'heart': 'Heart', 'finesse': 'Finesse',
}
DISCIPLINE_TAGLINE = {
    'combat': 'You fight.', 'exploration': 'You discover.', 'mind': 'You outwit.',
    'heart': 'You feel.', 'finesse': 'You perform.',
}
# Lucide icon per discipline (the dossier/sheet section headers). Resolved via job_icons.
DISCIPLINE_ICON = {
    'combat': 'swords', 'exploration': 'compass', 'mind': 'brain',
    'heart': 'heart', 'finesse': 'sparkles',
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
# What kinds of games feed each element (grounded in the real IGDB genre/theme rules in
# job_detection.py). Shown in the element detail to explain why a game tags as this element.
CRITERIA = {
    'slayer': "Hack-and-slash and beat-'em-ups: crowds of enemies, combo counters, big numbers.",
    'gunslinger': "Shooters where gunplay is the point. (Add a sci-fi setting and it becomes Vanguard.)",
    'vanguard': "Science-fiction shooters: guns, but make it the future.",
    'outlaw': "Open-world games with a violent streak: freedom plus firepower.",
    'warrior': "Fighting games: one on one, frame-perfect, settle it in the ring.",
    'pathfinder': "Platformers: precise jumps and tricky traversal.",
    'infiltrator': "Stealth games: unseen, unheard, gone before they knew you were there.",
    'cartographer': "Open-world games built for exploration: fill in the map, chase the horizon.",
    'mascot': "Comedic platformers: bright worlds, big jumps, a grin the whole way.",
    'survivalist': "Survival games: scrape by against cold, hunger, and whatever's hunting you.",
    'mastermind': "Puzzle games: logic, patterns, and the satisfying click of a solution.",
    'tactician': "Strategy in all its forms: RTS, turn-based, tactics, MOBA.",
    'architect': "Sandbox games: you build the world instead of just playing in it.",
    'tycoon': "Simulators: systems to manage, optimize, and grow.",
    'card-shark': "Card and board games: the deck, the table, the odds.",
    'mage': "Fantasy RPGs: magic, monsters, and a world that needs saving.",
    'champion': "Role-playing games: the build, the levels, the loot. (Add a fantasy setting and it becomes Mage.)",
    'librarian': "Visual novels and point-and-click adventures: stories you read and click through.",
    'jester': "Comedy games played for the laughs.",
    'exorcist': "Horror games: walking toward the thing everyone else runs from.",
    'gamer': "Arcade games: pick-up-and-play, score-chasing, twitch reflexes.",
    'driver': "Racing games: the apex, the perfect lap.",
    'athlete': "Sports games: seasons, podiums, championships.",
    'maestro': "Rhythm and music games: every beat, right on time.",
    'freelancer': "The catch-all. When a game fits no single specialty, its XP lands here.",
}


def job_dict(job, level, total_xp, *, atomic, slot_index):
    """Build one job tile from a job + the viewer's real level/XP for it.

    `slot_index` is the job's slot within its discipline (`atomic` is a legacy running index the
    workshops still read). The curve is FLAT + cap-less, so every job is always climbing toward
    the next level -- there is no "locked" or "maxed" state; the prestige TIER (Initiate..Legend)
    is the milestone label instead.
    """
    level = max(1, level)
    floor = xp_for_level(level)
    ceil = xp_for_level(level + 1)
    into = max(0, total_xp - floor)
    span = max(1, ceil - floor)
    progress = min(100, round(into / span * 100))
    tier = tier_for_level(level)
    next_at = tier['next_level']  # level the next tier unlocks at (None at the top, Legend)

    return {
        'number': atomic,
        'name': job.name,
        'slug': job.slug,
        'disc_slug': job.discipline,
        'icon': job.icon,
        'shape': SHAPES[slot_index % len(SHAPES)],
        'symbol': SYMBOLS.get(job.slug, job.name[:2]),
        'level': level,
        'started': total_xp > 0,  # untouched (0 XP) jobs render dormant, so real progress shows through
        'progress': progress,
        'xp_current': f"{into:,}",
        'xp_next': f"{span:,}",
        'xp_total': f"{total_xp:,}",
        'tier': tier['name'],
        'tier_key': tier['key'],
        'next_tier': tier_for_level(next_at)['name'] if next_at else '',
        'levels_to_next_tier': (next_at - level) if next_at else 0,
        'description': job.description or DESCRIPTIONS.get(job.slug, ''),
        'criteria': CRITERIA.get(job.slug, ''),
    }


def build_profile_jobs(profile):
    """Assemble the full jobs/disciplines view for a profile from real `ProfileJobXP`.

    Returns the `disciplines` list (each discipline with its job tiles + average level, header
    icon, played count, fill + per-discipline radar JSON), the overall radar series, and totals.
    Bounded by the Job catalog (~25 rows); safe to iterate.
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
            tile = job_dict(job, level, txp, atomic=atomic, slot_index=i)
            tiles.append(tile)
            total_level += tile['level']  # floored (>= 1), so Pursuer Level counts every job
            total_xp += txp
        all_tiles.extend(tiles)
        avg = round(sum(t['level'] for t in tiles) / len(tiles), 1) if tiles else 0
        radar_values.append(avg)
        disciplines.append({
            'slug': slug, 'label': label, 'tagline': DISCIPLINE_TAGLINE[slug],
            'icon': DISCIPLINE_ICON.get(slug, ''),
            'jobs': tiles, 'avg': avg,
            'played': sum(1 for t in tiles if t['started']),  # jobs in this discipline you've touched
            'radar_labels_json': json.dumps([t['name'] for t in tiles]),
            'radar_data_json': json.dumps([t['level'] for t in tiles]),
        })

    max_avg = max(radar_values) if radar_values else 0
    radar_max = max(10, int(math.ceil((max_avg + 1) / 5.0)) * 5)
    # Discipline band fill: avg level against the (user-scaled) radar cap, so bands read as an
    # absolute sense of progress -- never misleadingly full for a fresh Pursuer.
    for d in disciplines:
        d['fill'] = min(100, round(d['avg'] / radar_max * 100)) if radar_max else 0

    return {
        'disciplines': disciplines,
        # Aggregates the eye can't read off the grid (varies per user, unlike a
        # top-tier/started count that is ~0 or ~everything for most of the userbase).
        'avg_level': round(total_level / atomic, 1) if atomic else 0,
        'highest_level': max((t['level'] for t in all_tiles), default=1),
        'radar_labels_json': json.dumps(list(DISCIPLINE_LABELS.values())),
        'radar_data_json': json.dumps(radar_values),
        # Axis max scales to the family averages (the overview series), rounded up to a
        # "nice" value, so the overview fills well; a family's drill-down with an outlier
        # job above this just soft-extends (Chart.js suggestedMax).
        'radar_max': radar_max,
        'total_level': total_level,
        'total_xp': total_xp,
        'total': atomic,
    }


# --- Compounds (a Project's molecule) -------------------------------------

def job_atom(job):
    """The atom identity for a job/element inside a Compound: 2-char symbol, family-slot
    shape, family color. (Distinct from the leveled `job_dict` tile.)"""
    return {
        'slug': job.slug,
        'icon': job.icon,
        'symbol': SYMBOLS.get(job.slug) or job.name[:2],
        'shape': SHAPES[job.display_order % len(SHAPES)],
        'disc_slug': job.discipline,
        'name': job.name,
    }


def build_compound(elements, seed):
    """Deterministic molecule from a Project's elements (a list of `job_atom` dicts),
    seeded so each Project is (practically always) visually distinct -- even the many
    1-2 element Projects that share job-sets. Each element is replicated a seeded number
    of times (multiplicity) so small Projects get body and same-job Projects diverge,
    then the atoms are laid out and bonded. Entropy: per-element multiplicity, layout
    choice, a global rotation, per-atom position jitter, and double bonds. Core bonds
    carry their two endpoint family colors + a centerline so a bond can run a gradient
    A -> B. Returns SVG-ready `atoms` + `bonds` in a 200x200 viewBox.
    """
    if not elements:
        return {'atoms': [], 'bonds': []}
    rng = random.Random(seed)

    base = len(elements)
    extra_pool = {1: [1, 1, 2, 2, 3], 2: [0, 0, 1, 1, 2], 3: [0, 0, 0, 1]}.get(base, [0])
    expanded = []
    for a in elements:
        expanded.append(a)
        expanded += [a] * rng.choice(extra_pool)
    rng.shuffle(expanded)
    atoms = expanded[:9]
    n = len(atoms)
    cx = cy = 100.0
    size = 58.0 if n <= 2 else (52.0 if n <= 4 else (46.0 if n <= 6 else 40.0))

    # 1) canonical core positions + core bonds
    if n == 1:
        positions, pairs = [(cx, cy)], []
    elif n == 2:
        d = rng.uniform(34, 46)
        positions = [(cx - d, cy), (cx + d, cy)]
        pairs = [(0, 1, rng.random() < 0.5)]
    else:
        layout = rng.choice(['ring', 'chain']) if n == 3 else rng.choice(['ring', 'hub'])
        if layout == 'hub':
            m = n - 1
            hub_r = rng.uniform(52, 62)
            a0 = rng.uniform(0, 2 * math.pi)
            positions = [(cx, cy)] + [
                (cx + hub_r * math.cos(a0 + k * 2 * math.pi / m), cy + hub_r * math.sin(a0 + k * 2 * math.pi / m))
                for k in range(m)
            ]
            pairs = [(0, k, rng.random() < 0.33) for k in range(1, n)]
        elif layout == 'chain':
            spread, rise = rng.uniform(46, 56), rng.uniform(18, 30)
            positions = [(cx - spread, cy + rise * 0.6), (cx, cy - rise), (cx + spread, cy + rise * 0.6)]
            pairs = [(0, 1, rng.random() < 0.4), (1, 2, rng.random() < 0.4)]
        else:  # ring with one double bond
            ring_r = rng.uniform(48, 58)
            a0 = -math.pi / 2 + rng.uniform(-0.4, 0.4)
            positions = [
                (cx + ring_r * math.cos(a0 + k * 2 * math.pi / n),
                 cy + ring_r * math.sin(a0 + k * 2 * math.pi / n))
                for k in range(n)
            ]
            pairs = [(k, (k + 1) % n, False) for k in range(n)]
            di = rng.randrange(n)
            pairs[di] = (pairs[di][0], pairs[di][1], True)

    # 2) global seeded rotation of the whole core
    ga = rng.uniform(0, 2 * math.pi)
    cgv, sgv = math.cos(ga), math.sin(ga)
    positions = [
        (cx + (x - cx) * cgv - (y - cy) * sgv, cy + (x - cx) * sgv + (y - cy) * cgv)
        for (x, y) in positions
    ]

    # 2b) per-atom position jitter -- the big lever for same-job Projects diverging.
    if n > 1:
        positions = [(x + rng.uniform(-7, 7), y + rng.uniform(-7, 7)) for (x, y) in positions]

    # 3) bond lines (with doubles). No scaffold -- the molecule is the element atoms only.
    bonds = []
    for i, j, dbl in pairs:
        x1, y1 = positions[i]
        x2, y2 = positions[j]
        if dbl:
            ddx, ddy = x2 - x1, y2 - y1
            length = math.hypot(ddx, ddy) or 1.0
            ox, oy = -ddy / length * 3.0, ddx / length * 3.0
            segs = [(x1 + ox, y1 + oy, x2 + ox, y2 + oy), (x1 - ox, y1 - oy, x2 - ox, y2 - oy)]
        else:
            segs = [(x1, y1, x2, y2)]
        bonds.append({
            'lines': [
                {'x1': round(p, 1), 'y1': round(q, 1), 'x2': round(r, 1), 'y2': round(s, 1)}
                for (p, q, r, s) in segs
            ],
            'a': atoms[i]['disc_slug'], 'b': atoms[j]['disc_slug'],
            'gx1': round(x1, 1), 'gy1': round(y1, 1), 'gx2': round(x2, 1), 'gy2': round(y2, 1),
        })

    out_atoms = [
        {
            'x0': round(x - size / 2, 1), 'y0': round(y - size / 2, 1), 'size': round(size, 1),
            'shape': a['shape'], 'symbol': a['symbol'], 'disc_slug': a['disc_slug'],
        }
        for (x, y), a in zip(positions, atoms)
    ]
    return {'atoms': out_atoms, 'bonds': bonds}
