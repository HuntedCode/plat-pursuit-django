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

import re
from dataclasses import dataclass
from datetime import date


#: Anything that has no business in a path we own: every C0 control (the URL parser silently REMOVES
#: tab/LF/CR before parsing, which is how `/<TAB>/evil.com` became `https://evil.com`), DEL, the space,
#: and the backslash in any position (the parser folds it to `/` for http(s)).
_UNSAFE_IN_PATH = re.compile(r'[\x00-\x20\x7f\\]')


@dataclass(frozen=True)
class Entry:
    """One announcement.

    `id` is the identity the "seen" marker stores, so it must never be reused for different content --
    changing an entry's words is fine and stays seen, which is what you want for a typo and NOT what you
    want for a new announcement. Give a new announcement a new id.

    `beats` are (label, copy) pairs rendered as the numbered stack the Career explainer and the 1.0
    greeting both use. Two or three; this is a notice, not a changelog page.

    `link_url` is a path we own, never an external URL: the modal is a trusted surface that opens itself
    over the page, and a link out of it is a phishing shape. Enforced by `safe_link_url`, which the
    templates render instead of the raw value -- see there for why a test was not enough.
    """
    id: str
    published: date
    title: str
    beats: tuple[tuple[str, str], ...]
    link_label: str = ''
    link_url: str = ''

    @property
    def safe_link_url(self) -> str:
        """`link_url` if it is genuinely a path on this site, else '' (the template drops the link).

        THE TEST THAT GUARDED THIS WAS BYPASSABLE, which is why the check now runs at render time as
        well. It asserted `startswith('/')` and `not startswith('//')` -- and `/\\evil.com/login`
        satisfies both. For a special scheme the WHATWG URL parser treats a backslash exactly as a
        forward slash, so the browser resolves that href to `https://evil.com/login`, `link.href` reads
        back as the external URL, and the modal's dismissing-link handler calls `location.assign` on it.
        A green suite was asserting a guarantee it did not provide.

        The rule is a SHAPE ALLOWLIST, not a list of known tricks. The first cut denied the specific
        characters it could think of and was bypassed by one it could not: see the comment below.
        """
        url = self.link_url
        if not url.startswith('/'):
            return ''
        # ANY C0 control, DEL, space or backslash, ANYWHERE -- an allowlist of shapes rather than a
        # denylist of tricks, because the denylist was already wrong once. The WHATWG URL parser REMOVES
        # every ASCII tab, LF and CR from its input before parsing anything, so `/<TAB>/evil.com` passed
        # a second-character check literally and then resolved to `https://evil.com`. Django does not
        # escape tab either, so it survived all the way into the href. Rejecting the whole class means
        # the next variant of that trick is covered before somebody finds it.
        if _UNSAFE_IN_PATH.search(url):
            return ''
        if url[1:2] == '/':
            return ''
        if url[1:].lower().startswith(('%2f', '%5c')):
            return ''
        return url


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
    #: The rebuild, dated to PP_LAUNCH_DATE on prod. ARCHIVE-ONLY as a consequence of being older than
    #: the entry above it, not by a flag: `latest()` is ENTRIES[0], so nothing below the top can fire.
    #: That is deliberate here -- 1.0 has its own greeting modal (_launch_welcome.html), and an entry
    #: that could also pop would show a hunter the same announcement twice, once in each modal.
    #: If a future entry ever needs to be recorded without popping while it IS the newest, that is when
    #: to add the flag; it does not exist yet because this case does not need it.
    Entry(
        id='2026-09-platpursuit-1-0',
        published=date(2026, 9, 1),
        title='PlatPursuit 1.0',
        beats=(
            ('Career and Contracts',
             'Twenty-five jobs across five disciplines. Contracts are sets of games matched to '
             'your library: finish one, claim it, and the XP levels the jobs it covers.'),
            ('Badges became medallions',
             'Every badge series has handcrafted artwork you can pick up and inspect. The ones '
             'you have earned are in your Collection.'),
            ('A rebuilt site',
             'New home page, leaderboards, game pages and profiles. Everything you had tracked '
             'is still here.'),
        ),
        link_label='See your Career',
        link_url='/career/',
    ),
)


def latest() -> Entry | None:
    """The entry a hunter is measured against. None when there are none, which every caller handles."""
    return ENTRIES[0] if ENTRIES else None


def by_id(entry_id: str) -> Entry | None:
    return next((e for e in ENTRIES if e.id == entry_id), None)


def unseen_ids(user, previewing: bool = False) -> frozenset[str]:
    """The ids to mark "New" on the archive: everything published since this reader last looked.

    ENTRIES is newest-first, so the single stored marker is enough -- everything ABOVE the entry they
    last dismissed is newer than it. No second piece of state, and nothing that can disagree with the
    modal about what counts as seen.

    NEVER LOOKED = THE NEWEST ONE ONLY, not all of them. Marking every historical entry for a fresh
    account is technically true and useless: a page where every row is flagged has flagged nothing, and
    a hunter who joined last week is not owed a "New" badge on something from two months ago. This
    matches what the modal shows them, which is also just the newest.

    An unrecognised marker (its entry was pulled after they dismissed it) falls into the same branch and
    flags the newest. It self-corrects on this very page load, which posts the newest id back.

    Anonymous readers get nothing: with no marker there is no "since", and every row would be flagged.
    """
    if not ENTRIES:
        return frozenset()
    if user is None or not getattr(user, 'is_authenticated', False):
        return frozenset()
    # The team door reaches this surface too. Without it, previewing lit the modal and the avatar marker
    # and then dropped a staff reader onto an archive with no pills -- the half-open door `previewing`
    # was written to prevent, one surface later.
    if previewing:
        return frozenset({ENTRIES[0].id})

    flags = getattr(user, 'ui_flags', None)
    if not isinstance(flags, dict):
        # Defensive: `is_due`'s copy of this read is swallowed by the context processor's except, but
        # this one is called straight from the view, so a hand-edited non-dict would 500 the archive
        # for that account alone while every other page rendered.
        return frozenset({ENTRIES[0].id})
    seen = flags.get('whats_new_seen')
    ids = [e.id for e in ENTRIES]
    if seen not in ids:
        return frozenset({ids[0]})
    return frozenset(ids[:ids.index(seen)])


#: The querystring that forces the whole feature on for the team, dismissed or not.
PREVIEW = 'whats-new'


def previewing(request) -> bool:
    """`?preview=whats-new`, team-gated -- the retest door for a one-shot that has been spent.

    ONE definition, used by both the modal's gate and the avatar dot's, because they are two halves of
    one thing and a door that opens only half of it is not a preview. The modal had this inline and the
    dot had nothing, so after dismissing you could re-open the modal but never see the marker again
    without clearing the flag in a shell.

    Mirrors the launch-welcome / syncing / landing doors: staff or moderator, on the live page, with no
    state written -- so previewing never spends anything and never has to be undone.
    """
    if request is None or getattr(request, 'GET', None) is None:
        return False
    if request.GET.get('preview') != PREVIEW:
        return False
    user = getattr(request, 'user', None)
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    return bool(getattr(user, 'is_staff', False) or getattr(user, 'is_moderator', False))


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
    # `user` may be None: the site-wide context processor calls this on every render, including
    # requests that never went through AuthenticationMiddleware. Answering False beats raising into
    # a try/except that would then log a debug line on each of those renders.
    if entry is None or user is None or not getattr(user, 'is_authenticated', False):
        return False
    return (getattr(user, 'ui_flags', None) or {}).get('whats_new_seen') != entry.id
