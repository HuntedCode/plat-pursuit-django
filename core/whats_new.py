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
             'A board that rewards what you hunted, not how much. Your 1,000 rarest base-game '
             'trophies score by how few people have them, so one 0.5% bronze outweighs a pile of '
             'easy platinums.'),
            ('Shovelware Free',
             'The same ranking with the asset flips taken out. Platinums earned on games that '
             'are actually games.'),
            ('Grouped under one tab',
             'All three trophy boards now sit behind Trophies, a click apart. Both new boards '
             'rebuild once a night rather than live.'),
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

    Two ways to not be due, and the second is the one worth explaining. A hunter who signed up AFTER an
    entry was published never used the site without it -- telling them it is new is simply false, and it
    would greet every new account with a notice about something they have only ever seen one version of.
    They are skipped and left unmarked, so the NEXT entry reaches them normally.

    STRICTLY after, so joining ON the publication date still counts as being due. An entry is published
    on a date but deployed at an instant, and an account created that same day may well predate it. The
    two mistakes are not equal: showing the notice to somebody who joined an hour late is mildly odd and
    reads as onboarding, while hiding it from somebody who joined an hour early loses a real
    announcement permanently, because nothing makes an entry due again once a newer one ships.

    Deliberately does not consider the 1.0 greeting: precedence between the two lives in the view that
    can see both, not in either one's own rule.
    """
    entry = latest()
    if entry is None or not user.is_authenticated:
        return False
    if (user.ui_flags or {}).get('whats_new_seen') == entry.id:
        return False
    joined = getattr(user, 'date_joined', None)
    if joined is not None and joined.date() > entry.published:
        return False
    return True
