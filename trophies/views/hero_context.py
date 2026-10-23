"""What the Game page hero and the Trophy List page hero share (the trophy list pass, 2026-10-22).

Both heroes answer the same three questions with the same parts: where the hunter stands (the status card),
whether there is a platinum and how rare it is (the side stat), and what they can do. The Game page asks it of
the hunter's best list across the game; the Trophy List page asks it of one list. These are the per-list
answers, so the two cards cannot drift apart. Each costs at most one indexed query.
"""
from core.templatetags.custom_filters import psn_rarity_label
from trophies.models import Trophy

# PSN's four rarity tiers (`Trophy.trophy_rarity`) onto the site's four rarity grades (rarity.css
# `data-rarity`), so the hero's platinum gem takes the house grade colours instead of one-off ones.
# The tier NAMES come from the `psn_rarity_label` filter the trophy rows use.
PSN_RARITY_GRADES = {0: 'mythic', 1: 'rare', 2: 'uncommon', 3: 'common'}


def list_label(game):
    """A trophy list told apart from its siblings by what the hunter sees on its chip: platforms, then
    regions when the list is regional ("PS4", "PS5 / PS4", "PSVITA EU")."""
    label = ' / '.join(game.title_platform or [])
    if game.is_regional and game.region:
        label = f"{label} {'/'.join(game.region)}".strip()
    return label


def defined_total(game):
    """Every trophy the list defines, all grades summed (DLC included, as `progress` counts it)."""
    defined = game.defined_trophies if isinstance(game.defined_trophies, dict) else {}
    total = 0
    for key in ('bronze', 'silver', 'gold', 'platinum'):
        try:
            total += int(defined.get(key) or 0)
        except (TypeError, ValueError):
            pass
    return total


def has_platinum(game):
    defined = game.defined_trophies if isinstance(game.defined_trophies, dict) else {}
    try:
        return int(defined.get('platinum') or 0) > 0
    except (TypeError, ValueError):
        return False


def plat_rarity(game, label=''):
    """The hero's platinum stat for ONE list, so the corner always answers "is there a platinum?" (owner,
    2026-10-22): a percentage means yes, a hollow gem and a trophy count means no.

    With a platinum: PSN's own rate and tier, as the trophy rows show them. None when PSN has not reported a
    rate yet (0.0 is the unsynced default, not a real 0%): the slot is left out, not shown empty. With no
    platinum: {'none': True, 'total': ...}, or None when the list's trophies are not known yet (an unsynced
    list is not a list without a platinum). `label` names the list on a page that has several."""
    if not has_platinum(game):
        total = defined_total(game)
        return {'none': True, 'total': total, 'list_label': label} if total else None
    row = (
        Trophy.objects.filter(game=game, trophy_type='platinum')
        .values('trophy_earn_rate', 'trophy_rarity').first()
    )
    if not row or not row['trophy_earn_rate'] or row['trophy_rarity'] not in PSN_RARITY_GRADES:
        return None
    return {
        'rate': row['trophy_earn_rate'],
        'label': psn_rarity_label(row['trophy_rarity']),
        'grade': PSN_RARITY_GRADES[row['trophy_rarity']],
        'list_label': label,
    }


def list_status(game, progress, earned, *, platted, plat_date=None, share=None, label=''):
    """The status card for one list the hunter owns: the dict both heroes' cards read.

      state      'plat' | 'complete' (100% with no platinum, or a platinum not synced yet) | 'started'
                 | 'owned' (in the library, nothing earned)
      game       the list it describes
      list_label its platforms, only on a page with more than one list
      progress, earned, total, to_go, plat_date
      share      the share-card link once the list is finished (completion_card_service.share_link)

    `progress` is PSN's percentage, which weighs trophies by grade; `earned` is a count. They answer
    different questions ("how far", "how many"), so the card shows both and never derives one from the
    other. A hunter not owning the list is the caller's case: it has no list to describe."""
    pct = progress or 0
    total = defined_total(game)
    earned = earned or 0
    if platted:
        state = 'plat'
    elif pct == 100:
        state = 'complete'
    elif pct > 0 or earned > 0:   # PSN can floor a sliver of progress to 0% while trophies are earned
        state = 'started'
    else:
        state = 'owned'
    return {
        'state': state,
        'game': game,
        'list_label': label,
        'progress': pct,
        'earned': earned,
        'total': total,
        'to_go': max(total - earned, 0),
        'plat_date': plat_date,
        'share': share,
    }
