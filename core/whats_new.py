"""What's New: the entries, and the rule for who is due one.

The content is CODE, not a model. A feature ships in a deploy, so the sentence announcing it ships in
the same deploy, reviewable in the same PR as the thing it describes -- and a wrong claim about a
feature is caught by the person reviewing that feature. A model would have bought publishing without a
deploy and cost a table, a migration, a staff CRUD, an audit-log entry and a preview door for it.

Deploying does not touch these entries and does not re-announce anything. "Seen" is stored per user as
the id of the newest entry they dismissed, so a deploy that adds no entry shows nobody anything, and
editing a typo in an existing entry (same id) stays seen.

ADDING AN ENTRY: put it at the TOP of ENTRIES with a new id, and add nothing else. The modal shows the
newest entry a hunter has not dismissed; older ones live on /whats-new/. Ordering is asserted by test
rather than sorted here, because a misdated entry is a mistake to be told about, not to be quietly
tidied away.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Entry:
    """One announcement.

    `id` is the identity the "seen" marker stores, so it must never be reused for different content --
    changing an entry's words is fine and stays seen, which is what you want for a typo and NOT what you
    want for a new announcement. Give a new announcement a new id.

    `beats` are (label, copy) pairs rendered as the numbered stack the Career explainer and the 1.0
    greeting both use. Two or three; this is a notice, not a changelog page.

    `link_url` is a path we own, never an external URL: the modal is a trusted surface and a link out of
    it is a phishing shape. Asserted by test.
    """
    id: str
    published: date
    title: str
    beats: tuple[tuple[str, str], ...]
    link_label: str = ''
    link_url: str = ''


#: NEWEST FIRST. See the module docstring before adding one.
ENTRIES: tuple[Entry, ...] = (
    Entry(
        id='2026-09-rarity-score-board',
        published=date(2026, 9, 9),
        title='Two new trophy leaderboards',
        beats=(
            ('Rarity Score',
             'Scores your 1,000 rarest base-game trophies. The rarer a trophy is, the more it is '
             'worth, so a 1% trophy scores 100 and a 10% trophy scores 10.'),
            ('Shovelware Free',
             'Ranks platinums with shovelware games left out.'),
            ('Where to find them',
             'Both sit under the Trophies tab on the leaderboards. They update once a night '
             'rather than live.'),
        ),
        link_label='See the boards',
        link_url='/leaderboards/?tab=rarity',
    ),
)


def latest() -> Entry | None:
    """The entry a hunter is measured against. None when there are none, which every caller handles."""
    return ENTRIES[0] if ENTRIES else None


def by_id(entry_id: str) -> Entry | None:
    return next((e for e in ENTRIES if e.id == entry_id), None)


def is_due(user) -> bool:
    """Whether `user` should be shown the newest entry.

    One rule: there is an entry, and you have not dismissed that entry.

    NEW ACCOUNTS ARE INCLUDED, on purpose. An earlier cut skipped anyone who signed up after the entry
    was published, reasoning that a feature they have always had cannot be new to them. That is true
    about the FEATURE and wrong about the MESSAGE. What a new hunter takes from the notice is that the
    site is actively being built, which is worth more to them than the literal accuracy of the word
    "new" -- and the date on the entry says when it landed, so nothing is being misrepresented.

    Deliberately does not consider the 1.0 greeting: precedence between the two lives in the view that
    can see both, not in either one's own rule.
    """
    entry = latest()
    if entry is None or not user.is_authenticated:
        return False
    return (user.ui_flags or {}).get('whats_new_seen') != entry.id
