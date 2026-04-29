"""Roadmap notes: create / edit / delete / resolve, plus the mention parser.

Notes are author-only meta-content layered on top of the Roadmap system.
This service centralizes the business rules:

- any writer+ can post a note on any section (no ownership scoping)
- authors can edit / delete their own notes
- editor+ can delete anyone's notes (light moderation)
- anyone can resolve their own; editor+ can resolve anyone's
- @mentions in body are parsed against existing PSN usernames so we can fire
  notifications and turn them into profile links at render time

Posting a note never requires holding the edit lock. Notes live entirely
outside the lock + branch + revision flow.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable, Optional

from django.db import transaction
from django.utils import timezone

from trophies.models import (
    Profile,
    Roadmap,
    RoadmapNote,
    RoadmapNoteRead,
    RoadmapStep,
    RoadmapTab,
    TrophyGuide,
)

logger = logging.getLogger('psn_api')


# PSN usernames are 3-16 chars, [A-Za-z0-9_-]. Allow alpha/digit/_/- and
# require a leading word boundary so we don't pick up email-like fragments.
_MENTION_RE = re.compile(r'(?<![A-Za-z0-9_\-/@])@([A-Za-z0-9_\-]{3,16})')


class NoteError(Exception):
    """Raised on a note operation that fails permission or validation."""


def parse_mention_usernames(body: str) -> list[str]:
    """Return distinct lowercase psn_usernames mentioned in a note body.

    PSN usernames are case-sensitive on PSN, but Profile.psn_username is
    stored lowercased on save (see Profile.save). We lowercase the matched
    handles so the resolution against Profile is reliable.
    """
    seen = []
    for match in _MENTION_RE.finditer(body or ''):
        handle = match.group(1).lower()
        if handle not in seen:
            seen.append(handle)
    return seen


def resolve_mentioned_profiles(body: str) -> list[Profile]:
    """Resolve @mentions in a note body to existing Profiles.

    Self-mentions are intentionally NOT excluded — if an author types their
    own handle, that's an explicit choice (self-reminder, testing, etc.) and
    we should respect it. Unknown handles are silently dropped (typos
    shouldn't block posting).
    """
    handles = parse_mention_usernames(body)
    if not handles:
        return []
    return list(
        Profile.objects.filter(psn_username__in=handles).select_related('user')
    )


def _truncate_excerpt(body: str, max_len: int = 160) -> str:
    """Trim a note body to a single-line excerpt suitable for a notification message."""
    cleaned = ' '.join((body or '').split())  # normalize whitespace + newlines
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + '…'


def _resolve_target_label(note: 'RoadmapNote') -> str:
    """Human-readable description of where the note is anchored.

    Examples:
      - guide-level: "general notes"
      - tab: "Base Game — General Tips"
      - step: "Base Game › Step: Final Boss"
      - trophy guide: "Base Game › Trophy: Cleared the Tutorial"
    """
    if note.target_kind == RoadmapNote.TARGET_GUIDE:
        return 'general notes'

    if note.target_kind == RoadmapNote.TARGET_TAB:
        tab = note.target_tab
        if tab:
            tab_name = tab.concept_trophy_group.display_name
            return f'{tab_name} — General Tips'
        return 'a tab'

    if note.target_kind == RoadmapNote.TARGET_STEP:
        step = note.target_step
        if step:
            tab_name = step.tab.concept_trophy_group.display_name
            step_title = (step.title or '').strip() or 'untitled step'
            return f'{tab_name} › Step: {step_title}'
        return 'a step'

    if note.target_kind == RoadmapNote.TARGET_TROPHY_GUIDE:
        guide = note.target_trophy_guide
        if guide:
            tab_name = guide.tab.concept_trophy_group.display_name
            # Resolve the trophy name from any matching game in the concept
            # (trophy_id is consistent across PS4/PS5 stacks within a concept).
            trophy_name = None
            try:
                game = guide.tab.roadmap.concept.games.first()
                if game:
                    trophy = game.trophies.filter(
                        trophy_id=guide.trophy_id,
                        trophy_group_id=guide.tab.concept_trophy_group.trophy_group_id,
                    ).only('trophy_name').first()
                    if trophy:
                        trophy_name = trophy.trophy_name
            except Exception:
                # Trophy lookup is best-effort cosmetic enrichment.
                trophy_name = None
            if trophy_name:
                return f'{tab_name} › Trophy: {trophy_name}'
            return f'{tab_name} › Trophy #{guide.trophy_id}'
        return 'a trophy guide'

    return 'a note'


def _build_mention_detail(note: 'RoadmapNote', target_label: str, game_title: str) -> str:
    """Markdown-formatted notification detail body.

    Renders into the `detail` field (max 2500 chars). Shows the full note
    body plus the where/who/when context, so the recipient has the full
    picture without having to click into the editor.
    """
    author_name = (
        note.author.display_psn_username or note.author.psn_username
        if note.author else 'Unknown'
    )
    body = (note.body or '').strip()
    # Cap at 2000 chars to leave room for the surrounding markdown framing.
    if len(body) > 2000:
        body = body[:1999].rstrip() + '…'
    timestamp = note.created_at.strftime('%b %d, %Y at %H:%M')
    lines = [
        f'**{author_name}** mentioned you in a roadmap note.',
        '',
        f'**Game:** {game_title}',
        f'**Section:** {target_label}',
        f'**Posted:** {timestamp}',
        '',
        '---',
        '',
        body,
    ]
    return '\n'.join(lines)[:2500]


def notify_new_mentions(note: 'RoadmapNote', *, prior_body: Optional[str] = None) -> int:
    """Fire `roadmap_note_mention` notifications for newly-mentioned profiles.

    For a fresh note (no `prior_body`), every mentioned profile gets a
    notification. For an edit, only profiles mentioned in the new body but
    NOT in the old one get notified — repeated edits to the same body
    don't re-spam already-notified users.

    Recipients are filtered to writer/editor/publisher roadmap roles only.
    Mentioning a random non-author handle (accidentally or otherwise) does
    not page that user — they're not on the roadmap team.

    Returns the number of notifications fired.
    """
    if not note.author_id:
        return 0

    new_mentions = resolve_mentioned_profiles(note.body)
    if prior_body is not None:
        prior_handles = set(parse_mention_usernames(prior_body))
        new_mentions = [p for p in new_mentions if p.psn_username not in prior_handles]

    # Role gate: only ping profiles with writer-or-higher roadmap role.
    # Random PSN handles that happen to match a regular user's username
    # don't get a notification, even if a writer types them by accident.
    new_mentions = [p for p in new_mentions if p.has_roadmap_role('writer')]
    if not new_mentions:
        return 0

    # Lazy imports to avoid circular dependency: notifications -> trophies.
    from notifications.models import NotificationTemplate
    from notifications.services.notification_service import NotificationService
    from notifications.services.template_service import TemplateService

    try:
        template = NotificationTemplate.objects.get(name='roadmap_note_mention')
    except NotificationTemplate.DoesNotExist:
        logger.warning(
            "roadmap_note_mention template missing; skipping mention notifications. "
            "Run loaddata initial_templates.json to install."
        )
        return 0

    roadmap = note.roadmap
    concept = roadmap.concept
    game = concept.games.first() if concept else None
    target_label = _resolve_target_label(note)
    body_excerpt = _truncate_excerpt(note.body)
    game_title = (
        getattr(game, 'title_name', None)
        or getattr(concept, 'unified_title', '')
        or 'a roadmap'
    )
    base_context = {
        'author_username': (
            note.author.display_psn_username or note.author.psn_username
            if note.author else 'someone'
        ),
        'game_title': game_title,
        'game_slug': getattr(game, 'np_communication_id', '') if game else '',
        'note_id': note.id,
        'roadmap_id': roadmap.id,
        'target_label': target_label,
        'body_excerpt': body_excerpt,
    }
    detail_md = _build_mention_detail(note, target_label, game_title)

    fired = 0
    for profile in new_mentions:
        if not profile.user_id:
            # Mentioned profile isn't linked to a User account, no recipient.
            continue
        try:
            ctx = {**base_context, 'username': profile.psn_username}
            rendered = TemplateService.render_template(template, ctx)
            NotificationService.create_notification(
                recipient=profile.user,
                notification_type=template.notification_type,
                title=rendered['title'],
                message=rendered['message'],
                detail=detail_md,
                icon=template.icon,
                action_url=rendered.get('action_url'),
                action_text=template.action_text,
                priority=template.priority,
                metadata=ctx,
                template=template,
            )
            fired += 1
        except Exception:
            logger.exception(
                "Failed to fire roadmap_note_mention notification to profile=%s for note=%s",
                profile.id, note.id,
            )
    return fired


# --------------------------------------------------------------------------- #
#  CRUD
# --------------------------------------------------------------------------- #

def create_note(
    *, roadmap: Roadmap, author: Profile, body: str,
    target_kind: str,
    target_tab_id: Optional[int] = None,
    target_step_id: Optional[int] = None,
    target_trophy_guide_id: Optional[int] = None,
    target_trophy_id: Optional[int] = None,
) -> RoadmapNote:
    """Create a new note. Validates target_kind + target FK consistency.

    For TARGET_TROPHY_GUIDE the caller can pass either:
      - `target_trophy_guide_id` (a TrophyGuide pk, for programmatic access), or
      - `(target_tab_id, target_trophy_id)` — used by the editor since trophy
        guide rows render for every trophy in the group, including those
        that don't have a TrophyGuide DB row yet. We resolve this pair via
        `get_or_create` on (tab, trophy_id) so leaving a note doesn't
        require pre-writing a guide body. Django's TextField permits ''
        at the DB level, so empty-body guides are valid.
    """
    body = (body or '').strip()
    if not body:
        raise NoteError("Note body is required.")
    if len(body) > 5000:
        raise NoteError("Note body is too long (max 5000 characters).")

    target_tab = None
    target_step = None
    target_trophy_guide = None

    if target_kind == RoadmapNote.TARGET_GUIDE:
        if target_tab_id or target_step_id or target_trophy_guide_id:
            raise NoteError("Guide-level notes can't have a target id.")
    elif target_kind == RoadmapNote.TARGET_TAB:
        if not target_tab_id:
            raise NoteError("Tab note requires a target_tab_id.")
        try:
            target_tab = RoadmapTab.objects.select_related('roadmap').get(pk=target_tab_id)
        except RoadmapTab.DoesNotExist:
            raise NoteError("Target tab not found.")
        if target_tab.roadmap_id != roadmap.id:
            raise NoteError("Target tab does not belong to this roadmap.")
    elif target_kind == RoadmapNote.TARGET_STEP:
        if not target_step_id:
            raise NoteError("Step note requires a target_step_id.")
        try:
            target_step = RoadmapStep.objects.select_related('tab__roadmap').get(pk=target_step_id)
        except RoadmapStep.DoesNotExist:
            raise NoteError("Target step not found.")
        if target_step.tab.roadmap_id != roadmap.id:
            raise NoteError("Target step does not belong to this roadmap.")
    elif target_kind == RoadmapNote.TARGET_TROPHY_GUIDE:
        if target_trophy_guide_id:
            # Direct-id path: programmatic access by TrophyGuide.pk.
            try:
                target_trophy_guide = TrophyGuide.objects.select_related('tab__roadmap').get(
                    pk=target_trophy_guide_id
                )
            except TrophyGuide.DoesNotExist:
                raise NoteError("Target trophy guide not found.")
            if target_trophy_guide.tab.roadmap_id != roadmap.id:
                raise NoteError("Target trophy guide does not belong to this roadmap.")
        elif target_tab_id and target_trophy_id is not None:
            # Editor path: get-or-create by (tab, trophy_id). Lets writers
            # leave notes on trophies that don't have a written guide yet.
            try:
                tab = RoadmapTab.objects.get(pk=target_tab_id, roadmap=roadmap)
            except RoadmapTab.DoesNotExist:
                raise NoteError("Target tab not found in this roadmap.")
            target_trophy_guide, _ = TrophyGuide.objects.get_or_create(
                tab=tab,
                trophy_id=int(target_trophy_id),
                defaults={'body': ''},
            )
        else:
            raise NoteError(
                "Trophy-guide note requires either target_trophy_guide_id "
                "or (target_tab_id + target_trophy_id)."
            )
    else:
        raise NoteError(f"Unknown target_kind '{target_kind}'.")

    note = RoadmapNote.objects.create(
        roadmap=roadmap,
        author=author,
        body=body,
        target_kind=target_kind,
        target_tab=target_tab,
        target_step=target_step,
        target_trophy_guide=target_trophy_guide,
    )
    notify_new_mentions(note, prior_body=None)
    return note


def edit_note(*, note: RoadmapNote, actor: Profile, body: str) -> RoadmapNote:
    """Edit a note's body. Authors can edit their own only."""
    if note.author_id != actor.id:
        raise NoteError("You can only edit notes you wrote.")
    body = (body or '').strip()
    if not body:
        raise NoteError("Note body is required.")
    if len(body) > 5000:
        raise NoteError("Note body is too long (max 5000 characters).")
    prior_body = note.body
    note.body = body
    note.save(update_fields=['body', 'updated_at'])
    notify_new_mentions(note, prior_body=prior_body)
    return note


def delete_note(*, note: RoadmapNote, actor: Profile) -> None:
    """Delete a note. Author or editor+ may delete."""
    is_author = note.author_id == actor.id
    is_editor = actor.has_roadmap_role('editor')
    if not (is_author or is_editor):
        raise NoteError("You don't have permission to delete this note.")
    note.delete()


def set_note_status(*, note: RoadmapNote, actor: Profile, resolved: bool) -> RoadmapNote:
    """Toggle a note's resolved/open status.

    Author can resolve their own; editor+ can resolve anyone's. Open->open
    or resolved->resolved is a no-op.
    """
    is_author = note.author_id == actor.id
    is_editor = actor.has_roadmap_role('editor')
    if not (is_author or is_editor):
        raise NoteError("You don't have permission to change this note's status.")

    target_status = RoadmapNote.STATUS_RESOLVED if resolved else RoadmapNote.STATUS_OPEN
    if note.status == target_status:
        return note
    note.status = target_status
    if resolved:
        note.resolved_by = actor
        note.resolved_at = timezone.now()
    else:
        note.resolved_by = None
        note.resolved_at = None
    note.save(update_fields=['status', 'resolved_by', 'resolved_at', 'updated_at'])
    return note


# --------------------------------------------------------------------------- #
#  Read tracking + heads-up
# --------------------------------------------------------------------------- #

def mark_read(*, profile: Profile, roadmap: Roadmap) -> RoadmapNoteRead:
    """Bump the profile's last_read_at for this roadmap to now.

    Called when the editor is opened. Used by the heads-up banner to
    decide whether new notes have been posted since the writer last looked.
    """
    record, _ = RoadmapNoteRead.objects.update_or_create(
        profile=profile, roadmap=roadmap,
        defaults={'last_read_at': timezone.now()},
    )
    return record


def unread_count(*, profile: Profile, roadmap: Roadmap) -> int:
    """Count open notes posted by other authors since profile's last read.

    Resolved notes don't count — if a loop closed before you saw it, treat
    it as handled. Notes you authored yourself don't count either.
    """
    try:
        record = RoadmapNoteRead.objects.get(profile=profile, roadmap=roadmap)
        cutoff = record.last_read_at
    except RoadmapNoteRead.DoesNotExist:
        # First-time visit: no cutoff yet, so all open notes by others
        # count as unread.
        cutoff = None

    qs = roadmap.notes.filter(status=RoadmapNote.STATUS_OPEN).exclude(author=profile)
    if cutoff:
        qs = qs.filter(created_at__gt=cutoff)
    return qs.count()
