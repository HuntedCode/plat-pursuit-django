"""Tests for the Event model, EventCollector, and EventService.

Covers:
- EventCollector lifecycle (activate, queue, flush, deactivate, exception path)
- Manager filters (for_profile, pursuit_feed, trophy_feed, since, feed_visible)
- Soft-delete filtering via feed_visible() against deleted Reviews
- EventService recorders for events that don't require Phase 2-4 wiring
- Thread-local isolation between collector contexts

These tests use the in-memory SQLite test DB. They do NOT touch the sync
pipeline or any external services. Run with:

    python manage.py test trophies.tests.test_event_service
"""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone

from trophies.models import (
    Profile, Game, Trophy, Concept, Event, Review, ConceptTrophyGroup,
)
from trophies.services.event_service import (
    EventCollector,
    EventService,
    PURSUIT_FEED_TYPES,
    TROPHY_FEED_TYPES,
    event_collector,
)
from users.models import CustomUser


class EventCollectorTest(TestCase):
    """Test the thread-local EventCollector context manager."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email='collector@example.com',
            password='testpass123',
        )
        self.profile = Profile.objects.create(
            user=self.user,
            psn_username='collector_test',
            account_id='collect-001',
            is_linked=True,
        )
        self.concept = Concept.objects.create(
            unified_title='Collector Test Concept',
            concept_id='CUSA99001',
        )
        self.game = Game.objects.create(
            np_communication_id='NPWR99001_00',
            title_name='Collector Test Game',
            concept=self.concept,
        )
        self.platinum = Trophy.objects.create(
            trophy_id=99,
            trophy_name='The Platinum',
            trophy_type='platinum',
            game=self.game,
            trophy_earn_rate=0.5,
        )
        self.rare = Trophy.objects.create(
            trophy_id=100,
            trophy_name='Ultra Rare',
            trophy_type='gold',
            game=self.game,
            trophy_earn_rate=2.3,
        )

    def test_is_active_outside_context_returns_false(self):
        """Outside any event_collector context, is_active() returns False."""
        self.assertFalse(EventCollector.is_active())

    def test_is_active_inside_context_returns_true(self):
        """Inside an event_collector context, is_active() returns True."""
        with event_collector(profile_id=self.profile.id):
            self.assertTrue(EventCollector.is_active())
        self.assertFalse(EventCollector.is_active())

    def test_add_methods_no_op_outside_context(self):
        """add_* methods are no-ops when no context is active. No event row should be created."""
        EventCollector.add_platinum(
            profile_id=self.profile.id,
            trophy=self.platinum,
            earned_at=timezone.now(),
        )
        self.assertEqual(Event.objects.count(), 0)

    def test_collector_flushes_platinum_event(self):
        """A platinum queued inside the context is flushed via bulk_create on exit."""
        earned = timezone.now() - timedelta(days=2)
        with event_collector(profile_id=self.profile.id):
            EventCollector.add_platinum(
                profile_id=self.profile.id,
                trophy=self.platinum,
                earned_at=earned,
            )

        events = Event.objects.filter(event_type='platinum_earned')
        self.assertEqual(events.count(), 1)
        ev = events.first()
        self.assertEqual(ev.profile, self.profile)
        # occurred_at should be the historical earn time, not now()
        self.assertEqual(ev.occurred_at, earned)
        # Generic FK target should resolve to the trophy
        self.assertEqual(ev.target, self.platinum)
        # Metadata should carry concept_id, trophy_name, earn_rate
        self.assertEqual(ev.metadata['concept_id'], self.concept.id)
        self.assertEqual(ev.metadata['trophy_name'], 'The Platinum')
        self.assertEqual(ev.metadata['earn_rate'], 0.5)

    def test_collector_batches_multiple_events(self):
        """Multiple events queued in one context all flush together."""
        with event_collector(profile_id=self.profile.id):
            EventCollector.add_platinum(
                profile_id=self.profile.id,
                trophy=self.platinum,
                earned_at=timezone.now(),
            )
            EventCollector.add_rare_trophy(
                profile_id=self.profile.id,
                trophy=self.rare,
                earned_at=timezone.now(),
            )
            EventCollector.add_concept_100(
                profile_id=self.profile.id,
                concept=self.concept,
                occurred_at=timezone.now(),
            )

        self.assertEqual(Event.objects.count(), 3)
        types = set(Event.objects.values_list('event_type', flat=True))
        self.assertEqual(
            types,
            {'platinum_earned', 'rare_trophy_earned', 'concept_100_percent'},
        )

    def test_collector_flushes_on_exception(self):
        """Even if the context body raises, queued events are still flushed."""
        earned = timezone.now()
        with self.assertRaises(ValueError):
            with event_collector(profile_id=self.profile.id):
                EventCollector.add_platinum(
                    profile_id=self.profile.id,
                    trophy=self.platinum,
                    earned_at=earned,
                )
                raise ValueError("simulated sync failure")

        # The exception propagates BUT the event still got flushed
        self.assertEqual(Event.objects.filter(event_type='platinum_earned').count(), 1)
        # Collector is properly deactivated after the exception
        self.assertFalse(EventCollector.is_active())

    def test_collector_swallows_flush_failures(self):
        """If bulk_create itself raises, the exception is logged and swallowed."""
        with patch.object(Event.objects, 'bulk_create', side_effect=RuntimeError("DB down")):
            # The flush failure must NOT propagate out of the context
            with event_collector(profile_id=self.profile.id):
                EventCollector.add_platinum(
                    profile_id=self.profile.id,
                    trophy=self.platinum,
                    earned_at=timezone.now(),
                )
            # Context exited cleanly despite the flush failure
            self.assertFalse(EventCollector.is_active())
        # Nothing was persisted
        self.assertEqual(Event.objects.count(), 0)

    def test_nested_contexts_share_thread_local(self):
        """Nesting event_collector contexts on the same thread does not corrupt state.

        This is intentionally a soft test: the system isn't designed for
        nested collectors (the sync pipeline opens at most one per worker
        thread), but a stray nested call should not crash and should leave
        is_active() correctly False after both contexts exit.
        """
        with event_collector(profile_id=self.profile.id):
            with event_collector(profile_id=self.profile.id):
                EventCollector.add_platinum(
                    profile_id=self.profile.id,
                    trophy=self.platinum,
                    earned_at=timezone.now(),
                )
        self.assertFalse(EventCollector.is_active())


class EventManagerTest(TestCase):
    """Test the EventManager queryset filters."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email='manager@example.com',
            password='testpass123',
        )
        self.profile = Profile.objects.create(
            user=self.user,
            psn_username='manager_test',
            account_id='mgr-001',
            is_linked=True,
        )
        self.user2 = CustomUser.objects.create_user(
            email='other@example.com',
            password='testpass123',
            username='other_user',
        )
        self.profile2 = Profile.objects.create(
            user=self.user2,
            psn_username='other_user',
            account_id='mgr-002',
            is_linked=True,
        )
        self.concept = Concept.objects.create(
            unified_title='Manager Test Concept',
            concept_id='CUSA99002',
        )
        self.now = timezone.now()

    def _make_event(self, *, event_type, profile=None, occurred_at=None):
        return Event.objects.create(
            profile=profile if profile is not None else self.profile,
            event_type=event_type,
            occurred_at=occurred_at or self.now,
        )

    def test_for_profile_filters_to_target_profile(self):
        self._make_event(event_type='platinum_earned', profile=self.profile)
        self._make_event(event_type='platinum_earned', profile=self.profile2)
        self.assertEqual(Event.objects.for_profile(self.profile).count(), 1)
        self.assertEqual(Event.objects.for_profile(self.profile2).count(), 1)

    def test_pursuit_feed_includes_user_and_trophy_events(self):
        self._make_event(event_type='platinum_earned')
        self._make_event(event_type='review_posted')
        self._make_event(event_type='day_zero', profile=None)
        feed = Event.objects.pursuit_feed()
        types = set(feed.values_list('event_type', flat=True))
        self.assertIn('platinum_earned', types)
        self.assertIn('review_posted', types)
        # day_zero is a system event, not in the Pursuit Feed taxonomy
        self.assertNotIn('day_zero', types)

    def test_trophy_feed_excludes_non_trophy_events(self):
        self._make_event(event_type='platinum_earned')
        self._make_event(event_type='rare_trophy_earned')
        self._make_event(event_type='concept_100_percent')
        self._make_event(event_type='review_posted')
        self._make_event(event_type='badge_earned')
        feed = Event.objects.trophy_feed()
        types = set(feed.values_list('event_type', flat=True))
        self.assertEqual(types, TROPHY_FEED_TYPES)

    def test_pursuit_feed_constants_match_expected(self):
        """Sanity check: TROPHY_FEED_TYPES is a strict subset of PURSUIT_FEED_TYPES."""
        self.assertTrue(TROPHY_FEED_TYPES.issubset(PURSUIT_FEED_TYPES))
        self.assertGreater(len(PURSUIT_FEED_TYPES), len(TROPHY_FEED_TYPES))

    def test_since_filters_by_occurred_at(self):
        cutoff = self.now - timedelta(days=3)
        self._make_event(event_type='platinum_earned', occurred_at=self.now - timedelta(days=10))
        self._make_event(event_type='platinum_earned', occurred_at=self.now - timedelta(days=1))
        recent = Event.objects.since(cutoff)
        self.assertEqual(recent.count(), 1)


class FeedVisibleTest(TestCase):
    """Test that feed_visible() filters out events whose targets are soft-deleted."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email='visible@example.com',
            password='testpass123',
        )
        self.profile = Profile.objects.create(
            user=self.user,
            psn_username='visible_test',
            account_id='vis-001',
            is_linked=True,
        )
        self.concept = Concept.objects.create(
            unified_title='Feed Visible Test',
            concept_id='CUSA99003',
            slug='feed-visible-test',
        )
        # Need a CTG for the Review FK. trophy_group_id='default' is the
        # base game by convention; the model has no explicit is_base_game flag.
        self.ctg = ConceptTrophyGroup.objects.create(
            concept=self.concept,
            trophy_group_id='default',
            display_name='Base Game',
        )

    def test_feed_visible_includes_active_review_events(self):
        review = Review.objects.create(
            profile=self.profile,
            concept=self.concept,
            concept_trophy_group=self.ctg,
            body='A genuinely thoughtful review with enough characters to pass validation.' * 2,
            recommended=True,
        )
        ev = EventService.record_review_posted(review)
        self.assertIsNotNone(ev)
        self.assertEqual(Event.objects.feed_visible().count(), 1)

    def test_feed_visible_excludes_soft_deleted_review_events(self):
        review = Review.objects.create(
            profile=self.profile,
            concept=self.concept,
            concept_trophy_group=self.ctg,
            body='Another thoughtful review with enough characters to pass validation.' * 2,
            recommended=True,
        )
        EventService.record_review_posted(review)
        self.assertEqual(Event.objects.feed_visible().count(), 1)

        # Soft delete the review
        review.is_deleted = True
        review.save(update_fields=['is_deleted'])

        # The event row is still there but feed_visible() filters it out
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(Event.objects.feed_visible().count(), 0)

    def test_feed_visible_does_not_filter_non_review_events(self):
        """Events whose target is not a Review (e.g. trophy events) are unaffected by feed_visible."""
        # Create a non-review event with a Concept target — should NOT be affected
        Event.objects.create(
            profile=self.profile,
            event_type='concept_100_percent',
            occurred_at=timezone.now(),
            target_content_type=ContentType.objects.get_for_model(Concept),
            target_object_id=self.concept.id,
        )
        self.assertEqual(Event.objects.feed_visible().count(), 1)


class EventServiceRecorderTest(TestCase):
    """Test EventService recorders that don't require Phase 2-4 wiring.

    Recorders that depend on models with full setup (UserMilestone, UserBadge,
    Challenge, GameList, Profile linking flows) are exercised in their
    respective phase commits. This file covers the simplest path: review_posted.
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email='recorder@example.com',
            password='testpass123',
        )
        self.profile = Profile.objects.create(
            user=self.user,
            psn_username='recorder_test',
            account_id='rec-001',
            is_linked=True,
        )
        self.concept = Concept.objects.create(
            unified_title='Recorder Test Concept',
            concept_id='CUSA99004',
            slug='recorder-test',
        )
        self.ctg = ConceptTrophyGroup.objects.create(
            concept=self.concept,
            trophy_group_id='default',
            display_name='Base Game (recorder)',
        )

    def test_record_review_posted_creates_event_for_base_game(self):
        review = Review.objects.create(
            profile=self.profile,
            concept=self.concept,
            concept_trophy_group=self.ctg,  # 'default' trophy_group_id = base game
            body='Yet another thoughtful review with enough characters.' * 2,
            recommended=True,
        )
        ev = EventService.record_review_posted(review)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.event_type, 'review_posted')
        self.assertEqual(ev.profile, self.profile)
        self.assertEqual(ev.target, review)
        self.assertEqual(ev.metadata['concept_slug'], 'recorder-test')
        self.assertEqual(ev.metadata['recommended'], True)
        self.assertFalse(ev.metadata['is_dlc_review'])  # 'default' = base game
        self.assertIsNone(ev.metadata['dlc_name'])

    def test_record_review_posted_marks_dlc_reviews(self):
        """A review against a non-default CTG should be flagged is_dlc_review=True."""
        dlc_ctg = ConceptTrophyGroup.objects.create(
            concept=self.concept,
            trophy_group_id='001',
            display_name='Story DLC: The Awakening',
        )
        review = Review.objects.create(
            profile=self.profile,
            concept=self.concept,
            concept_trophy_group=dlc_ctg,
            body='The DLC was a fitting send-off for the series, full of nice moments.' * 2,
            recommended=True,
        )
        ev = EventService.record_review_posted(review)
        self.assertIsNotNone(ev)
        self.assertTrue(ev.metadata['is_dlc_review'])
        self.assertEqual(ev.metadata['dlc_name'], 'Story DLC: The Awakening')

    def test_record_review_posted_swallows_failures(self):
        """Recorder failures must NOT propagate to the caller (review creation)."""
        # Pass an invalid object to force an exception in the recorder path
        ev = EventService.record_review_posted(None)
        self.assertIsNone(ev)
