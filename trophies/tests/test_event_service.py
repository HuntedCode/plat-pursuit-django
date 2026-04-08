"""Tests for the Event model, EventCollector, and EventService.

Covers:
- EventCollector lifecycle (activate, queue, flush, deactivate, exception path)
- Manager filters (for_profile, pursuit_feed, trophy_feed, since, feed_visible)
- Soft-delete filtering via feed_visible() against deleted Reviews
- EventService recorders for events that don't require Phase 2-4 wiring
- Thread-local isolation between collector contexts
- Phase 2: sync-pipeline emitter integration via
  PsnApiService.create_or_update_earned_trophy_from_trophy_data inside an
  event_collector context (platinum, rare-trophy, non-rare, shovelware skip,
  no-double-emit on sync re-runs)
- Phase 2: Day Zero seed migration produces exactly one system event

These tests use the project Postgres DB via Django's TestCase. They do NOT
touch the actual sync pipeline or any external services — sync-pipeline tests
fake `trophy_data` with SimpleNamespace and call the service method directly.
Run with:

    python manage.py test trophies.tests.test_event_service
"""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone

from trophies.models import (
    Profile, Game, Trophy, Concept, Event, Review, ConceptTrophyGroup,
    EarnedTrophy, Badge, UserBadge, Milestone, UserMilestone,
    Challenge, AZChallengeSlot, ProfileGame,
)
from trophies.services.event_service import (
    EventCollector,
    EventService,
    PURSUIT_FEED_TYPES,
    TROPHY_FEED_TYPES,
    RARE_TROPHY_EARN_RATE_THRESHOLD,
    event_collector,
)
from trophies.services.psn_api_service import PsnApiService
from users.models import CustomUser


def _user_events():
    """Events excluding the Day Zero seed system event.

    Migration 0185 creates a `day_zero` event during test database setup, and
    that row persists across the entire test suite (data migrations run once
    during DB creation; Django's per-test transaction rollback does NOT touch
    migration data). Tests that count Event rows or check feed_visible() must
    use this helper to filter the seed out, otherwise every assertion is
    off-by-one and the failures look like spurious flakes.

    Tests that specifically validate Day Zero behavior live in DayZeroSeedTest
    and use Event.objects directly.
    """
    return Event.objects.exclude(event_type='day_zero')


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
        self.assertEqual(_user_events().count(), 0)

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

        self.assertEqual(_user_events().count(), 3)
        types = set(_user_events().values_list('event_type', flat=True))
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
        self.assertEqual(_user_events().count(), 0)

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
        # _user_events() filters out the Day Zero seed (which has occurred_at=now()
        # and would otherwise satisfy the `since` filter and inflate the count).
        recent = _user_events().filter(occurred_at__gte=cutoff)
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
        # Use _user_events() to filter out the persistent Day Zero seed,
        # then apply feed_visible() to test the soft-delete filter in isolation.
        self.assertEqual(_user_events().feed_visible().count(), 1)

    def test_feed_visible_excludes_soft_deleted_review_events(self):
        review = Review.objects.create(
            profile=self.profile,
            concept=self.concept,
            concept_trophy_group=self.ctg,
            body='Another thoughtful review with enough characters to pass validation.' * 2,
            recommended=True,
        )
        EventService.record_review_posted(review)
        self.assertEqual(_user_events().feed_visible().count(), 1)

        # Soft delete the review
        review.is_deleted = True
        review.save(update_fields=['is_deleted'])

        # The event row is still there but feed_visible() filters it out
        self.assertEqual(_user_events().count(), 1)
        self.assertEqual(_user_events().feed_visible().count(), 0)

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
        self.assertEqual(_user_events().feed_visible().count(), 1)


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


def _make_trophy_data(*, earned, earned_date_time, progress=100, progress_rate=100,
                     trophy_hidden=False, progressed_date_time=None):
    """Build a fake trophy_data namespace mimicking the psnawp_api Trophy shape.

    The real type is `psnawp_api.models.trophies.Trophy`. We only need the
    fields that PsnApiService.create_or_update_earned_trophy_from_trophy_data
    actually reads — see psn_api_service.py:395-458 for the live attribute set.
    """
    return SimpleNamespace(
        earned=earned,
        earned_date_time=earned_date_time,
        trophy_hidden=trophy_hidden,
        progress=progress,
        progress_rate=progress_rate,
        progressed_date_time=progressed_date_time,
    )


class SyncPipelineEmitterTest(TestCase):
    """Phase 2: integration tests for the sync-pipeline event emitters.

    Exercises PsnApiService.create_or_update_earned_trophy_from_trophy_data
    inside a real event_collector context, asserting that the right events
    are produced for each trophy type / rarity / shovelware combination.
    Trophy and Game records are created directly (not via the PSN API service)
    so the test scope stays narrow on the event side effects.
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email='sync_emitter@example.com',
            password='testpass123',
        )
        self.profile = Profile.objects.create(
            user=self.user,
            psn_username='sync_emitter',
            account_id='sync-001',
            is_linked=True,
            sync_status='syncing',
        )
        self.concept = Concept.objects.create(
            unified_title='Sync Emitter Test Concept',
            concept_id='CUSA90001',
        )
        self.game = Game.objects.create(
            np_communication_id='NPWR90001_00',
            title_name='Sync Emitter Test Game',
            concept=self.concept,
        )
        # Distinct trophies for each scenario
        self.platinum = Trophy.objects.create(
            trophy_id=1,
            trophy_name='The Platinum Trophy',
            trophy_type='platinum',
            game=self.game,
            trophy_earn_rate=12.5,  # platinums get an event regardless of rarity
        )
        self.ultra_rare_gold = Trophy.objects.create(
            trophy_id=2,
            trophy_name='Hidden Master',
            trophy_type='gold',
            game=self.game,
            trophy_earn_rate=2.1,  # under threshold (5.0) -> rare event
        )
        self.common_bronze = Trophy.objects.create(
            trophy_id=3,
            trophy_name='First Steps',
            trophy_type='bronze',
            game=self.game,
            trophy_earn_rate=78.4,  # well above threshold -> NO event
        )
        self.borderline_silver = Trophy.objects.create(
            trophy_id=4,
            trophy_name='Just Past Threshold',
            trophy_type='silver',
            game=self.game,
            trophy_earn_rate=5.0,  # exactly at threshold -> NO event (strictly less than)
        )

    def test_platinum_creates_event_inside_collector(self):
        """A new platinum earn inside event_collector queues a platinum_earned event."""
        earned = timezone.now() - timedelta(days=3)
        with event_collector(profile_id=self.profile.id):
            PsnApiService.create_or_update_earned_trophy_from_trophy_data(
                self.profile, self.platinum,
                _make_trophy_data(earned=True, earned_date_time=earned),
            )
        events = Event.objects.filter(event_type='platinum_earned')
        self.assertEqual(events.count(), 1)
        ev = events.first()
        self.assertEqual(ev.profile, self.profile)
        self.assertEqual(ev.occurred_at, earned)  # historical truth
        self.assertEqual(ev.target, self.platinum)

    def test_platinum_outside_collector_emits_no_event(self):
        """Without an active collector, no event row is created (the gate works)."""
        earned = timezone.now() - timedelta(days=1)
        PsnApiService.create_or_update_earned_trophy_from_trophy_data(
            self.profile, self.platinum,
            _make_trophy_data(earned=True, earned_date_time=earned),
        )
        self.assertEqual(Event.objects.filter(event_type='platinum_earned').count(), 0)
        # The EarnedTrophy itself was still created — only the EVENT side
        # effect is gated.
        self.assertEqual(EarnedTrophy.objects.filter(profile=self.profile, trophy=self.platinum).count(), 1)

    def test_ultra_rare_trophy_creates_rare_event(self):
        """A new ultra-rare gold (earn_rate < 5%) inside the collector emits rare_trophy_earned."""
        earned = timezone.now() - timedelta(days=2)
        with event_collector(profile_id=self.profile.id):
            PsnApiService.create_or_update_earned_trophy_from_trophy_data(
                self.profile, self.ultra_rare_gold,
                _make_trophy_data(earned=True, earned_date_time=earned),
            )
        events = Event.objects.filter(event_type='rare_trophy_earned')
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.first().metadata['earn_rate'], 2.1)
        self.assertEqual(events.first().metadata['trophy_type'], 'gold')

    def test_common_trophy_emits_no_event(self):
        """A new common-bronze trophy (earn_rate well above threshold) does not emit."""
        earned = timezone.now() - timedelta(hours=4)
        with event_collector(profile_id=self.profile.id):
            PsnApiService.create_or_update_earned_trophy_from_trophy_data(
                self.profile, self.common_bronze,
                _make_trophy_data(earned=True, earned_date_time=earned),
            )
        self.assertEqual(_user_events().count(), 0)

    def test_borderline_threshold_emits_no_rare_event(self):
        """A trophy with earn_rate exactly at the threshold (5.0) is NOT counted as rare.

        The recorder uses `< RARE_TROPHY_EARN_RATE_THRESHOLD`, strictly less than.
        This is a documented behavior — flagging it explicitly so a future
        bored refactor doesn't accidentally relax the comparison to <=.
        """
        self.assertEqual(RARE_TROPHY_EARN_RATE_THRESHOLD, 5.0)
        earned = timezone.now() - timedelta(hours=2)
        with event_collector(profile_id=self.profile.id):
            PsnApiService.create_or_update_earned_trophy_from_trophy_data(
                self.profile, self.borderline_silver,
                _make_trophy_data(earned=True, earned_date_time=earned),
            )
        self.assertEqual(_user_events().count(), 0)

    def test_already_earned_trophy_does_not_double_emit(self):
        """On a sync re-run, a previously-earned platinum should not produce a duplicate event.

        The is_new_earn flag computed inside
        create_or_update_earned_trophy_from_trophy_data correctly returns False
        when the EarnedTrophy already has earned=True. This test guards against
        any future change that would silently bypass that gate.
        """
        earned = timezone.now() - timedelta(days=5)
        # First call: produces an event
        with event_collector(profile_id=self.profile.id):
            PsnApiService.create_or_update_earned_trophy_from_trophy_data(
                self.profile, self.platinum,
                _make_trophy_data(earned=True, earned_date_time=earned),
            )
        self.assertEqual(Event.objects.filter(event_type='platinum_earned').count(), 1)

        # Second call (sync re-run): same data, no duplicate
        with event_collector(profile_id=self.profile.id):
            PsnApiService.create_or_update_earned_trophy_from_trophy_data(
                self.profile, self.platinum,
                _make_trophy_data(earned=True, earned_date_time=earned),
            )
        self.assertEqual(Event.objects.filter(event_type='platinum_earned').count(), 1)

    def test_unearned_trophy_emits_no_event(self):
        """A trophy that is not yet earned never produces an event."""
        with event_collector(profile_id=self.profile.id):
            PsnApiService.create_or_update_earned_trophy_from_trophy_data(
                self.profile, self.platinum,
                _make_trophy_data(earned=False, earned_date_time=None),
            )
        self.assertEqual(_user_events().count(), 0)

    def test_shovelware_platinum_emits_no_event(self):
        """Platinums on shovelware concepts/games are skipped (matches notify_new_platinum behavior)."""
        self.game.shovelware_status = 'auto_flagged'
        self.game.save(update_fields=['shovelware_status'])
        self.game.refresh_from_db()  # ensure trophy.game.is_shovelware reads fresh state

        # Re-fetch the trophy via the relation so .game is the updated row
        platinum = Trophy.objects.select_related('game').get(pk=self.platinum.pk)

        earned = timezone.now() - timedelta(days=1)
        with event_collector(profile_id=self.profile.id):
            PsnApiService.create_or_update_earned_trophy_from_trophy_data(
                self.profile, platinum,
                _make_trophy_data(earned=True, earned_date_time=earned),
            )
        self.assertEqual(Event.objects.filter(event_type='platinum_earned').count(), 0)


class DayZeroSeedTest(TestCase):
    """Verify the Day Zero data migration produced exactly one system event.

    Django runs migrations against the test DB before tests start, so the
    Day Zero event already exists in setUp. We just assert its presence and
    properties.
    """

    def test_day_zero_event_exists_and_is_unique(self):
        events = Event.objects.filter(event_type='day_zero')
        self.assertEqual(events.count(), 1, "Exactly one Day Zero event should exist")

    def test_day_zero_event_has_no_profile(self):
        ev = Event.objects.get(event_type='day_zero')
        self.assertIsNone(ev.profile)

    def test_day_zero_event_metadata_marks_seed(self):
        ev = Event.objects.get(event_type='day_zero')
        self.assertTrue(ev.metadata.get('seed'))
        self.assertIn('chronicle', ev.metadata.get('message', '').lower())

    def test_day_zero_excluded_from_pursuit_feed(self):
        """The Pursuit Feed taxonomy does not include day_zero — it's a system event."""
        # Even though Day Zero exists in the table, pursuit_feed() filters it out.
        self.assertEqual(
            Event.objects.pursuit_feed().filter(event_type='day_zero').count(),
            0,
        )

    def test_day_zero_excluded_from_per_profile_activity(self):
        """A profile's Activity tab uses for_profile() which only matches non-null FKs."""
        user = CustomUser.objects.create_user(
            email='dayzero@example.com',
            password='testpass123',
        )
        profile = Profile.objects.create(
            user=user,
            psn_username='dayzero_test',
            account_id='dz-001',
            is_linked=True,
        )
        # for_profile filters by profile=given_profile, so null-profile rows
        # never match any specific user's activity tab.
        self.assertEqual(Event.objects.for_profile(profile).count(), 0)


class ServiceActionEmitterTest(TestCase):
    """Phase 3: integration tests for service-action event emitters.

    Each test exercises the upstream service method (or API view, for the
    GameList publish-flip case) and asserts that the right Event row is
    produced. These tests catch regressions where someone moves or removes
    the EventService.record_* call site without realizing it powers the
    Pursuit Feed.

    The recorders themselves are unit-tested in EventServiceRecorderTest.
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email='action@example.com',
            password='testpass123',
        )
        self.profile = Profile.objects.create(
            user=self.user,
            psn_username='action_test',
            account_id='act-001',
            is_linked=True,
            guidelines_agreed=True,
        )
        self.concept = Concept.objects.create(
            unified_title='Action Test Concept',
            concept_id='CUSA80001',
            slug='action-test',
        )
        self.ctg = ConceptTrophyGroup.objects.create(
            concept=self.concept,
            trophy_group_id='default',
            display_name='Base Game',
        )

    # ---- Review --------------------------------------------------------

    def test_review_service_emits_review_posted_event(self):
        """ReviewService.create_review must produce a review_posted event."""
        from trophies.services.review_service import ReviewService

        # Mock the access check so we don't need a full ProfileGame setup.
        with patch(
            'trophies.services.concept_trophy_group_service.ConceptTrophyGroupService.can_review_group',
            return_value=(True, None),
        ):
            review, error = ReviewService.create_review(
                profile=self.profile,
                concept=self.concept,
                concept_trophy_group=self.ctg,
                body='A solid review with enough characters to clear the minimum length cleanly.' * 2,
                recommended=True,
            )
        self.assertIsNone(error)
        self.assertIsNotNone(review)

        events = _user_events().filter(event_type='review_posted')
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.first().target, review)

    # ---- Challenges (3 types) -----------------------------------------

    def test_create_az_challenge_emits_challenge_started(self):
        from trophies.services.challenge_service import create_az_challenge
        challenge = create_az_challenge(self.profile, name='Test AZ')
        events = _user_events().filter(event_type='challenge_started')
        self.assertEqual(events.count(), 1)
        ev = events.first()
        self.assertEqual(ev.target, challenge)
        self.assertEqual(ev.metadata['challenge_type'], 'az')
        self.assertEqual(ev.metadata['name'], 'Test AZ')

    def test_create_calendar_challenge_emits_challenge_started(self):
        from trophies.services.challenge_service import create_calendar_challenge
        challenge = create_calendar_challenge(self.profile, name='Test Calendar')
        # The calendar create path calls backfill_calendar_from_history which
        # could potentially produce additional events. Filter strictly to
        # challenge_started rather than asserting total count.
        events = _user_events().filter(event_type='challenge_started')
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.first().metadata['challenge_type'], 'calendar')

    def test_create_genre_challenge_emits_challenge_started(self):
        from trophies.services.challenge_service import create_genre_challenge
        challenge = create_genre_challenge(self.profile, name='Test Genre')
        events = _user_events().filter(event_type='challenge_started')
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.first().metadata['challenge_type'], 'genre')

    # ---- GameList publish flip ----------------------------------------

    def test_game_list_publish_flip_emits_event(self):
        """GameListUpdateView.patch must emit on a real false->true publish flip."""
        from trophies.models import GameList
        from api.game_list_views import GameListUpdateView
        from rest_framework.test import APIRequestFactory, force_authenticate

        # Create a private list with at least one game (game_count > 0).
        game_list = GameList.objects.create(
            profile=self.profile,
            name='Test List',
            is_public=False,
            game_count=3,
        )

        factory = APIRequestFactory()
        request = factory.patch(
            f'/api/v1/lists/{game_list.id}/',
            {'is_public': True},
            format='json',
        )
        force_authenticate(request, user=self.user)
        view = GameListUpdateView.as_view()
        response = view(request, list_id=game_list.id)
        self.assertEqual(response.status_code, 200, msg=response.data)

        events = _user_events().filter(event_type='game_list_published')
        self.assertEqual(events.count(), 1)
        ev = events.first()
        self.assertEqual(ev.target, game_list)
        self.assertEqual(ev.metadata['list_name'], 'Test List')
        self.assertEqual(ev.metadata['game_count'], 3)

    def test_game_list_publish_already_public_no_event(self):
        """Re-PATCHing is_public=True on an already-public list does not double-emit."""
        from trophies.models import GameList
        from api.game_list_views import GameListUpdateView
        from rest_framework.test import APIRequestFactory, force_authenticate

        game_list = GameList.objects.create(
            profile=self.profile,
            name='Already Public',
            is_public=True,
            game_count=2,
        )

        factory = APIRequestFactory()
        request = factory.patch(
            f'/api/v1/lists/{game_list.id}/',
            {'is_public': True},  # No flip — already true
            format='json',
        )
        force_authenticate(request, user=self.user)
        view = GameListUpdateView.as_view()
        response = view(request, list_id=game_list.id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_user_events().filter(event_type='game_list_published').count(), 0)

    def test_game_list_publish_empty_list_no_event(self):
        """Publishing an empty list (game_count=0) must NOT emit (avoid surfacing empty lists)."""
        from trophies.models import GameList
        from api.game_list_views import GameListUpdateView
        from rest_framework.test import APIRequestFactory, force_authenticate

        game_list = GameList.objects.create(
            profile=self.profile,
            name='Empty List',
            is_public=False,
            game_count=0,
        )

        factory = APIRequestFactory()
        request = factory.patch(
            f'/api/v1/lists/{game_list.id}/',
            {'is_public': True},
            format='json',
        )
        force_authenticate(request, user=self.user)
        view = GameListUpdateView.as_view()
        response = view(request, list_id=game_list.id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_user_events().filter(event_type='game_list_published').count(), 0)

    # ---- Profile link --------------------------------------------------

    def test_link_profile_emits_profile_linked_event(self):
        """VerificationService.link_profile_to_user emits exactly once when newly linking."""
        from trophies.services.verification_service import VerificationService

        # Create a fresh unlinked profile and a fresh user to link it to.
        # Note: CustomUser.username is unique, and the default empty string
        # collides with self.user's empty username from setUp. Pass an
        # explicit username to avoid the unique-constraint violation.
        unlinked_user = CustomUser.objects.create_user(
            email='to_link@example.com',
            password='testpass123',
            username='to_link_user',
        )
        unlinked_profile = Profile.objects.create(
            psn_username='to_link',
            account_id='link-001',
            is_linked=False,
        )

        VerificationService.link_profile_to_user(unlinked_profile, unlinked_user)

        events = _user_events().filter(event_type='profile_linked', profile=unlinked_profile)
        self.assertEqual(events.count(), 1)
        ev = events.first()
        self.assertEqual(ev.metadata['psn_username'], 'to_link')

    def test_link_already_linked_profile_no_event(self):
        """Re-calling link_profile_to_user on an already-linked profile is a no-op."""
        from trophies.services.verification_service import VerificationService

        # self.profile is already linked in setUp. Re-linking should be a no-op.
        VerificationService.link_profile_to_user(self.profile, self.user)

        # No event for self.profile (it was already linked before this test ran)
        self.assertEqual(
            _user_events().filter(event_type='profile_linked', profile=self.profile).count(),
            0,
        )


class SignalAndBulkEmitterTest(TestCase):
    """Phase 4: signal-handled and bulk emitters.

    Covers:
    - The sibling badge_earned signal receiver (fires outside bulk context,
      short-circuits inside bulk context)
    - milestone_hit emission via the direct award path
    - milestone_hit emission via the auto-award path (with mocked criteria)
    - challenge_progress + challenge_completed for AZ challenges with real
      ProfileGame.has_plat=True data

    Calendar and genre challenge progress emitters share the same coalescing
    structure as AZ; they're verified by manual sync against a real PSN
    profile rather than unit tests, since the test setup for them is
    significantly heavier.
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email='signal@example.com',
            password='testpass123',
        )
        self.profile = Profile.objects.create(
            user=self.user,
            psn_username='signal_test',
            account_id='sig-001',
            is_linked=True,
        )

    # ---- Badge sibling receiver ---------------------------------------

    def test_badge_signal_emits_outside_bulk_context(self):
        """A new UserBadge created outside bulk_gamification_update fires the receiver."""
        badge = Badge.objects.create(
            name='Solo Badge',
            series_slug='solo-test',
            tier=1,
        )
        UserBadge.objects.create(profile=self.profile, badge=badge)

        events = _user_events().filter(event_type='badge_earned', profile=self.profile)
        self.assertEqual(events.count(), 1)
        ev = events.first()
        self.assertEqual(ev.metadata['count'], 1)
        self.assertEqual(ev.metadata['badges'][0]['series_slug'], 'solo-test')
        self.assertEqual(ev.metadata['badges'][0]['tier'], 1)

    def test_badge_signal_short_circuits_in_bulk_context(self):
        """Inside bulk_gamification_update, the sibling receiver does NOT emit."""
        from trophies.services.xp_service import bulk_gamification_update

        badge = Badge.objects.create(
            name='Bulk Badge',
            series_slug='bulk-test',
            tier=2,
        )
        with bulk_gamification_update():
            UserBadge.objects.create(profile=self.profile, badge=badge)

        # The receiver short-circuited; no per-badge event was created.
        self.assertEqual(
            _user_events().filter(event_type='badge_earned', profile=self.profile).count(),
            0,
        )

    def test_record_bulk_badges_for_profile_emits_coalesced_event(self):
        """The bulk emitter produces ONE event listing all badges."""
        b1 = Badge.objects.create(name='B1', series_slug='ser1', tier=1)
        b2 = Badge.objects.create(name='B2', series_slug='ser2', tier=3)
        ub1 = UserBadge.objects.create(profile=self.profile, badge=b1)
        # Reset events from the signal receiver firing on those creates so
        # we only see the bulk emission.
        Event.objects.filter(profile=self.profile).delete()
        ub2 = UserBadge.objects.create(profile=self.profile, badge=b2)
        Event.objects.filter(profile=self.profile).delete()

        ev = EventService.record_bulk_badges_for_profile(self.profile, [ub1, ub2])
        self.assertIsNotNone(ev)
        self.assertEqual(ev.event_type, 'badge_earned')
        self.assertTrue(ev.metadata['coalesced'])
        self.assertEqual(ev.metadata['count'], 2)
        slugs = {b['series_slug'] for b in ev.metadata['badges']}
        self.assertEqual(slugs, {'ser1', 'ser2'})

    # ---- Milestone emitters --------------------------------------------

    def test_award_milestone_directly_emits_milestone_hit(self):
        """The direct award path fires record_milestone_hit inside its `if created` block."""
        from trophies.services.milestone_service import award_milestone_directly

        milestone = Milestone.objects.create(
            name='Test Direct Milestone',
            criteria_type='manual',
            criteria_details={'target': 1},
        )
        user_milestone, created = award_milestone_directly(
            self.profile, milestone, notify=False,
        )
        self.assertTrue(created)
        events = _user_events().filter(event_type='milestone_hit', profile=self.profile)
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.first().target, user_milestone)

    def test_award_milestone_directly_idempotent_no_double_emit(self):
        """Re-calling award_milestone_directly does not emit a second event."""
        from trophies.services.milestone_service import award_milestone_directly

        milestone = Milestone.objects.create(
            name='Idempotent Milestone',
            criteria_type='manual',
            criteria_details={'target': 1},
        )
        award_milestone_directly(self.profile, milestone, notify=False)
        award_milestone_directly(self.profile, milestone, notify=False)
        self.assertEqual(
            _user_events().filter(event_type='milestone_hit', profile=self.profile).count(),
            1,
        )

    def test_check_and_award_milestone_emits_when_criteria_met(self):
        """check_and_award_milestone fires the recorder when the handler returns achieved=True."""
        from trophies.services.milestone_service import check_and_award_milestone

        milestone = Milestone.objects.create(
            name='Auto Milestone',
            criteria_type='plat_count',
            criteria_details={'target': 1},
        )

        # Mock the milestone handler to force achievement without needing
        # real platinum data. The handler dict lives at
        # trophies.milestone_handlers.MILESTONE_HANDLERS and is imported by
        # check_and_award_milestone via local import; patch the import
        # location with a stub callable.
        fake_handler = lambda profile, milestone, _cache=None: {'achieved': True, 'progress': 1}
        with patch.dict(
            'trophies.milestone_handlers.MILESTONE_HANDLERS',
            {'plat_count': fake_handler},
            clear=False,
        ):
            result = check_and_award_milestone(self.profile, milestone)

        self.assertTrue(result['awarded'])
        self.assertTrue(result['created'])
        self.assertEqual(
            _user_events().filter(event_type='milestone_hit', profile=self.profile).count(),
            1,
        )

    # ---- AZ Challenge progress + completed -----------------------------

    def test_az_challenge_progress_and_completed_events(self):
        """check_az_challenge_progress emits coalesced progress + completed events."""
        from trophies.services.challenge_service import (
            create_az_challenge, check_az_challenge_progress,
        )

        # Create a challenge and assign games to all 26 slots so a single
        # check call drives it from 0/26 to 26/26 in one pass.
        challenge = create_az_challenge(self.profile, name='Speedrun')

        # Wipe events from create_az_challenge (challenge_started) so the
        # assertions below see only progress + completed.
        Event.objects.filter(profile=self.profile).delete()

        concept = Concept.objects.create(
            unified_title='AZ Test Concept',
            concept_id='CUSA70001',
        )
        # Create 26 games, 26 ProfileGame rows with has_plat=True, and
        # assign each game to a different letter slot.
        slots = list(challenge.az_slots.all())
        for idx, slot in enumerate(slots):
            game = Game.objects.create(
                np_communication_id=f'NPWR700{idx:02d}_00',
                title_name=f'AZ Game {idx}',
                concept=concept,
            )
            ProfileGame.objects.create(
                profile=self.profile, game=game, has_plat=True,
            )
            slot.game = game
            slot.save(update_fields=['game'])

        check_az_challenge_progress(self.profile)

        # Reload challenge from DB to see the is_complete flip
        challenge.refresh_from_db()
        self.assertTrue(challenge.is_complete)

        # ONE coalesced challenge_progress event with all 26 slots in metadata
        progress_events = _user_events().filter(
            event_type='challenge_progress', profile=self.profile,
        )
        self.assertEqual(progress_events.count(), 1)
        progress_ev = progress_events.first()
        self.assertEqual(progress_ev.metadata['count'], 26)
        self.assertEqual(len(progress_ev.metadata['slots']), 26)

        # ONE challenge_completed event for the crossover
        completed_events = _user_events().filter(
            event_type='challenge_completed', profile=self.profile,
        )
        self.assertEqual(completed_events.count(), 1)
        self.assertEqual(completed_events.first().target, challenge)

    def test_az_challenge_partial_progress_no_completed_event(self):
        """Partial slot completion emits challenge_progress but NOT challenge_completed."""
        from trophies.services.challenge_service import (
            create_az_challenge, check_az_challenge_progress,
        )

        challenge = create_az_challenge(self.profile, name='Slow Burn')
        Event.objects.filter(profile=self.profile).delete()

        concept = Concept.objects.create(
            unified_title='Partial Test',
            concept_id='CUSA70100',
        )

        # Only assign games to 3 letter slots (not 26)
        slots = list(challenge.az_slots.all())[:3]
        for idx, slot in enumerate(slots):
            game = Game.objects.create(
                np_communication_id=f'NPWR701{idx:02d}_00',
                title_name=f'Partial Game {idx}',
                concept=concept,
            )
            ProfileGame.objects.create(
                profile=self.profile, game=game, has_plat=True,
            )
            slot.game = game
            slot.save(update_fields=['game'])

        check_az_challenge_progress(self.profile)

        challenge.refresh_from_db()
        self.assertFalse(challenge.is_complete)

        # ONE coalesced progress event with 3 slots
        progress_events = _user_events().filter(
            event_type='challenge_progress', profile=self.profile,
        )
        self.assertEqual(progress_events.count(), 1)
        self.assertEqual(progress_events.first().metadata['count'], 3)

        # NO completed event
        self.assertEqual(
            _user_events().filter(
                event_type='challenge_completed', profile=self.profile,
            ).count(),
            0,
        )


class MetadataSerializationTest(TestCase):
    """Regression tests for the datetime-in-metadata bug.

    During Phase 4 development, the AZ challenge progress recorder put raw
    Python `datetime` objects into the slot dicts via `slot.completed_at`.
    Postgres' JSONField encoder cannot serialize datetimes, so the insert
    raised TypeError, which then poisoned the surrounding `transaction.atomic`
    block (TransactionManagementError on every subsequent query). This is
    one of the failure modes the plan explicitly tries to prevent: events
    must NEVER break the calling transaction.

    The fix has two layers:
    1. `_serialize_metadata` recursively converts datetimes to ISO strings.
    2. `_create_event_safely` wraps the create in `transaction.atomic()` so
       a failure rolls back to the savepoint instead of poisoning the outer
       transaction.

    These tests guard both layers.
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email='meta@example.com',
            password='testpass123',
        )
        self.profile = Profile.objects.create(
            user=self.user,
            psn_username='meta_test',
            account_id='meta-001',
            is_linked=True,
        )

    def test_datetime_in_metadata_serializes_to_iso_string(self):
        """A datetime nested inside metadata must round-trip as an ISO string."""
        from trophies.services.event_service import _serialize_metadata

        now = timezone.now()
        payload = {
            'top': now,
            'nested': {'inner': now},
            'list': [{'completed_at': now}, {'completed_at': now}],
            'unaffected_str': 'plain',
            'unaffected_int': 42,
            'unaffected_none': None,
        }
        result = _serialize_metadata(payload)
        self.assertEqual(result['top'], now.isoformat())
        self.assertEqual(result['nested']['inner'], now.isoformat())
        self.assertEqual(result['list'][0]['completed_at'], now.isoformat())
        self.assertEqual(result['list'][1]['completed_at'], now.isoformat())
        self.assertEqual(result['unaffected_str'], 'plain')
        self.assertEqual(result['unaffected_int'], 42)
        self.assertIsNone(result['unaffected_none'])

    def test_create_event_safely_handles_datetime_in_metadata(self):
        """_create_event_safely converts datetimes before insert and produces a valid Event."""
        from trophies.services.event_service import _create_event_safely

        now = timezone.now()
        ev = _create_event_safely(
            profile=self.profile,
            event_type='challenge_progress',
            occurred_at=now,
            metadata={
                'slots': [
                    {'letter': 'A', 'completed_at': now},
                ],
                'last_slot_completed_at': now,
            },
        )
        self.assertIsNotNone(ev)
        # Metadata should be persisted as JSON-safe primitives
        self.assertEqual(ev.metadata['slots'][0]['completed_at'], now.isoformat())
        self.assertEqual(ev.metadata['last_slot_completed_at'], now.isoformat())

    def test_create_event_safely_savepoint_does_not_poison_outer_transaction(self):
        """A failed _create_event_safely call must not break the surrounding transaction."""
        from django.db import transaction
        from trophies.services.event_service import _create_event_safely

        with transaction.atomic():
            # Force a failure by passing an invalid event_type that exceeds
            # the CharField max_length=32. The savepoint inside
            # _create_event_safely should roll back the failed insert
            # without poisoning the outer transaction.
            ev = _create_event_safely(
                profile=self.profile,
                event_type='x' * 100,  # too long for max_length=32
                occurred_at=timezone.now(),
                metadata={},
            )
            self.assertIsNone(ev)

            # The outer transaction should still be usable: this query
            # would raise TransactionManagementError if the savepoint
            # protection were broken.
            count = Profile.objects.filter(pk=self.profile.pk).count()
            self.assertEqual(count, 1)

    def test_record_challenge_progress_with_datetime_slots(self):
        """End-to-end: record_challenge_progress accepts slot dicts with datetime values."""
        from trophies.services.challenge_service import create_az_challenge

        challenge = create_az_challenge(self.profile, name='Datetime Slot Test')
        # Wipe events from create
        Event.objects.filter(profile=self.profile).delete()

        now = timezone.now()
        slots_meta = [
            {'letter': 'A', 'game_id': 1, 'completed_at': now},
            {'letter': 'B', 'game_id': 2, 'completed_at': now - timedelta(minutes=5)},
        ]
        ev = EventService.record_challenge_progress(challenge, slots_meta)
        self.assertIsNotNone(ev)
        # Verify the datetime fields are now ISO strings, not raw datetimes
        self.assertEqual(ev.metadata['slots'][0]['completed_at'], now.isoformat())
        self.assertIsInstance(ev.metadata['last_slot_completed_at'], str)
        self.assertEqual(ev.metadata['count'], 2)


class ProfileActivityTabTest(TestCase):
    """Phase 5: per-user Activity tab on ProfileDetailView.

    Verifies the new 7th profile tab queries events correctly, applies the
    v1 semantics (events authored BY the target user only), excludes system
    events (Day Zero) and soft-deleted-target events, and renders the right
    template on both full-page and AJAX requests.
    """

    def setUp(self):
        # Profile.psn_username has max_length=16, so test values must fit.
        self.user = CustomUser.objects.create_user(
            email='activity_tab@example.com',
            password='testpass123',
        )
        self.profile = Profile.objects.create(
            user=self.user,
            psn_username='activity_user',  # 13 chars
            account_id='at-001',
            is_linked=True,
        )
        # A second profile so we can verify the tab does NOT leak other
        # users' events into this profile's tab.
        self.other_user = CustomUser.objects.create_user(
            email='other_activity@example.com',
            password='testpass123',
            username='other_activity',
        )
        self.other_profile = Profile.objects.create(
            user=self.other_user,
            psn_username='other_user',  # 10 chars
            account_id='at-002',
            is_linked=True,
        )

    def _make_event(self, profile, event_type='platinum_earned', when=None):
        return Event.objects.create(
            profile=profile,
            event_type=event_type,
            occurred_at=when or timezone.now(),
        )

    # ---- _build_activity_tab_context unit tests ------------------------

    def test_handler_returns_events_for_target_profile(self):
        """The tab handler returns events whose FK matches the target profile."""
        from trophies.views.profile_views import ProfileDetailView

        self._make_event(self.profile, 'platinum_earned')
        self._make_event(self.profile, 'badge_earned')
        self._make_event(self.profile, 'review_posted')

        view = ProfileDetailView()
        result = view._build_activity_tab_context(self.profile, per_page=50, page_number=1)

        events = list(result['profile_events'])
        self.assertEqual(len(events), 3)
        for ev in events:
            self.assertEqual(ev.profile, self.profile)

    def test_handler_excludes_other_profiles_events(self):
        """Events authored by other profiles must NOT appear on this profile's tab."""
        from trophies.views.profile_views import ProfileDetailView

        self._make_event(self.profile, 'platinum_earned')
        self._make_event(self.other_profile, 'platinum_earned')
        self._make_event(self.other_profile, 'review_posted')

        view = ProfileDetailView()
        result = view._build_activity_tab_context(self.profile, per_page=50, page_number=1)

        events = list(result['profile_events'])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].profile, self.profile)

    def test_handler_excludes_day_zero_system_event(self):
        """Day Zero (profile=None) is naturally excluded by for_profile()."""
        from trophies.views.profile_views import ProfileDetailView

        # Day Zero exists from the migration. Create one user-authored event
        # so we have something to compare against.
        self._make_event(self.profile, 'platinum_earned')

        view = ProfileDetailView()
        result = view._build_activity_tab_context(self.profile, per_page=50, page_number=1)

        events = list(result['profile_events'])
        types = [ev.event_type for ev in events]
        self.assertEqual(types, ['platinum_earned'])
        self.assertNotIn('day_zero', types)

    def test_handler_orders_by_occurred_at_descending(self):
        """Events sort newest-first by occurred_at (NOT created_at)."""
        from trophies.views.profile_views import ProfileDetailView

        old = self._make_event(
            self.profile, 'platinum_earned',
            when=timezone.now() - timedelta(days=10),
        )
        recent = self._make_event(
            self.profile, 'platinum_earned',
            when=timezone.now() - timedelta(hours=1),
        )

        view = ProfileDetailView()
        result = view._build_activity_tab_context(self.profile, per_page=50, page_number=1)

        events = list(result['profile_events'])
        self.assertEqual(events[0].pk, recent.pk)
        self.assertEqual(events[1].pk, old.pk)

    def test_handler_paginates(self):
        """Pagination respects per_page and page_number."""
        from trophies.views.profile_views import ProfileDetailView

        # Create 5 events
        for i in range(5):
            self._make_event(
                self.profile, 'platinum_earned',
                when=timezone.now() - timedelta(hours=i),
            )

        view = ProfileDetailView()

        # Page 1, per_page=2 → should get the 2 newest
        page1 = list(view._build_activity_tab_context(self.profile, per_page=2, page_number=1)['profile_events'])
        self.assertEqual(len(page1), 2)

        # Page 3, per_page=2 → should get the last (1 event remaining)
        page3 = list(view._build_activity_tab_context(self.profile, per_page=2, page_number=3)['profile_events'])
        self.assertEqual(len(page3), 1)

    def test_handler_filters_soft_deleted_review_targets(self):
        """A review_posted event whose review is soft-deleted must NOT appear."""
        from trophies.views.profile_views import ProfileDetailView

        concept = Concept.objects.create(
            unified_title='Filter Test', concept_id='CUSA60001', slug='filter-test',
        )
        ctg = ConceptTrophyGroup.objects.create(
            concept=concept, trophy_group_id='default', display_name='Base Game',
        )
        review = Review.objects.create(
            profile=self.profile,
            concept=concept,
            concept_trophy_group=ctg,
            body='An honest opinion with enough characters to pass validation.' * 2,
            recommended=True,
        )
        EventService.record_review_posted(review)

        view = ProfileDetailView()
        before = list(view._build_activity_tab_context(self.profile, per_page=50, page_number=1)['profile_events'])
        self.assertEqual(len(before), 1)

        # Soft delete the review
        review.is_deleted = True
        review.save(update_fields=['is_deleted'])

        after = list(view._build_activity_tab_context(self.profile, per_page=50, page_number=1)['profile_events'])
        self.assertEqual(len(after), 0)

    # ---- End-to-end view dispatch -------------------------------------

    def test_full_page_request_renders_profile_template_with_activity_context(self):
        """GET /profiles/<u>/?tab=activity renders the profile template and includes profile_events."""
        from django.test import Client

        self._make_event(self.profile, 'platinum_earned')
        self._make_event(self.profile, 'badge_earned')

        client = Client()
        response = client.get(f'/profiles/{self.profile.psn_username}/?tab=activity')
        self.assertEqual(response.status_code, 200)
        self.assertIn('profile_events', response.context)
        self.assertEqual(response.context['current_tab'], 'activity')
        self.assertEqual(len(list(response.context['profile_events'])), 2)
        # The activity count annotation should match
        self.assertEqual(response.context['profile_activity_count'], 2)

    def test_ajax_request_returns_partial_template(self):
        """An XMLHttpRequest with tab=activity returns the activity_list_items partial."""
        from django.test import Client

        self._make_event(self.profile, 'platinum_earned')

        client = Client()
        response = client.get(
            f'/profiles/{self.profile.psn_username}/?tab=activity&page=1',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 200)
        # The partial template should be used (not the full profile_detail.html)
        template_names = [t.name for t in response.templates if t.name]
        self.assertIn(
            'trophies/partials/profile_detail/activity_list_items.html',
            template_names,
        )


class PursuitActivityModuleTest(TestCase):
    """Phase 6: hybrid event-backed dashboard activity module.

    The new `pursuit_activity` provider replaces the legacy `recent_activity`
    and `recent_platinums` providers. It is INTENTIONALLY hybrid:
    event-backed for noteworthy event types (platinum, rare trophy, badge,
    milestone, review, etc.) PLUS a direct EarnedTrophy query for trophy_group
    cards (bronze/silver/gold grouped by game+day). The trophy_group stream
    preserves the existing UX where non-rare trophies group together — those
    aren't tracked in the Event table by design.

    These tests cover:
    - Event-backed cards appear in the merged feed
    - Trophy_group cards appear from EarnedTrophy (non-platinums only)
    - Platinum events are NOT double-counted (Event stream owns them)
    - Empty profile produces an empty events list
    - Limit is honored after merge+sort
    - Module registry: `pursuit_activity` registered, legacy slugs gone
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email='pa_module@example.com',
            password='testpass123',
        )
        self.profile = Profile.objects.create(
            user=self.user,
            psn_username='pa_module_user',  # 14 chars
            account_id='pa-001',
            is_linked=True,
        )

    def test_provider_returns_events_dict(self):
        """The provider returns {'events': [...]} matching the template contract."""
        from trophies.services.dashboard_service import provide_pursuit_activity
        result = provide_pursuit_activity(self.profile)
        self.assertIn('events', result)
        self.assertIsInstance(result['events'], list)

    def test_provider_includes_event_backed_cards(self):
        """Events authored by the user appear in the merged feed."""
        from trophies.services.dashboard_service import provide_pursuit_activity

        Event.objects.create(
            profile=self.profile,
            event_type='platinum_earned',
            occurred_at=timezone.now(),
            metadata={'trophy_name': 'The Plat', 'earn_rate': 4.5},
        )
        Event.objects.create(
            profile=self.profile,
            event_type='review_posted',
            occurred_at=timezone.now() - timedelta(hours=1),
            metadata={'concept_title': 'Reviewed Game', 'recommended': True},
        )

        result = provide_pursuit_activity(self.profile)
        types = [e['type'] for e in result['events']]
        self.assertIn('platinum', types)
        self.assertIn('review', types)

    def test_provider_includes_trophy_group_cards(self):
        """Bronze/silver/gold earnings are grouped by game+day from EarnedTrophy."""
        from trophies.services.dashboard_service import provide_pursuit_activity

        concept = Concept.objects.create(
            unified_title='Group Test', concept_id='CUSA50001',
        )
        game = Game.objects.create(
            np_communication_id='NPWR50001_00',
            title_name='Group Test Game',
            concept=concept,
        )
        # Create bronze + silver trophies and earn them
        for i, (ttype, tname) in enumerate([
            ('bronze', 'B1'), ('bronze', 'B2'), ('silver', 'S1'),
        ]):
            t = Trophy.objects.create(
                trophy_id=i + 1,
                trophy_name=tname,
                trophy_type=ttype,
                game=game,
                trophy_earn_rate=50.0,
            )
            EarnedTrophy.objects.create(
                profile=self.profile,
                trophy=t,
                earned=True,
                earned_date_time=timezone.now() - timedelta(hours=2),
            )

        result = provide_pursuit_activity(self.profile)
        groups = [e for e in result['events'] if e['type'] == 'trophy_group']
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['count'], 3)
        self.assertEqual(groups[0]['type_counts']['bronze'], 2)
        self.assertEqual(groups[0]['type_counts']['silver'], 1)

    def test_provider_excludes_platinums_from_trophy_group_stream(self):
        """Platinums must NOT show up in the trophy_group stream — they're event-backed."""
        from trophies.services.dashboard_service import provide_pursuit_activity

        concept = Concept.objects.create(
            unified_title='Plat Test', concept_id='CUSA50002',
        )
        game = Game.objects.create(
            np_communication_id='NPWR50002_00',
            title_name='Plat Test Game',
            concept=concept,
        )
        plat = Trophy.objects.create(
            trophy_id=99,
            trophy_name='The Platinum',
            trophy_type='platinum',
            game=game,
            trophy_earn_rate=10.0,
        )
        EarnedTrophy.objects.create(
            profile=self.profile,
            trophy=plat,
            earned=True,
            earned_date_time=timezone.now(),
        )

        result = provide_pursuit_activity(self.profile)
        # No trophy_group card should be produced from a platinum-only earn
        groups = [e for e in result['events'] if e['type'] == 'trophy_group']
        self.assertEqual(len(groups), 0)

    def test_provider_empty_profile_returns_empty_list(self):
        from trophies.services.dashboard_service import provide_pursuit_activity
        result = provide_pursuit_activity(self.profile)
        self.assertEqual(result['events'], [])

    def test_provider_honors_limit(self):
        """The result is trimmed to settings['limit'] after merge+sort."""
        from trophies.services.dashboard_service import provide_pursuit_activity

        # Create 10 platinum events; limit=3 should return only 3.
        for i in range(10):
            Event.objects.create(
                profile=self.profile,
                event_type='platinum_earned',
                occurred_at=timezone.now() - timedelta(hours=i),
                metadata={'trophy_name': f'Plat {i}', 'earn_rate': 4.5},
            )

        result = provide_pursuit_activity(self.profile, settings={'limit': 3})
        self.assertEqual(len(result['events']), 3)

    def test_provider_sorts_newest_first(self):
        """The merged feed is ordered by date descending."""
        from trophies.services.dashboard_service import provide_pursuit_activity

        old = Event.objects.create(
            profile=self.profile,
            event_type='review_posted',
            occurred_at=timezone.now() - timedelta(days=5),
            metadata={'concept_title': 'Old'},
        )
        recent = Event.objects.create(
            profile=self.profile,
            event_type='review_posted',
            occurred_at=timezone.now() - timedelta(hours=1),
            metadata={'concept_title': 'Recent'},
        )

        result = provide_pursuit_activity(self.profile)
        names = [e['name'] for e in result['events']]
        self.assertEqual(names[0], 'Recent')
        self.assertEqual(names[1], 'Old')

    # ---- Module registry --------------------------------------------------

    def test_pursuit_activity_module_registered(self):
        """The new module is in DASHBOARD_MODULES with the expected slug."""
        from trophies.services.dashboard_service import DASHBOARD_MODULES
        slugs = [m['slug'] for m in DASHBOARD_MODULES]
        self.assertIn('pursuit_activity', slugs)

    def test_legacy_module_slugs_removed(self):
        """The legacy slugs must NOT exist in the registry anymore."""
        from trophies.services.dashboard_service import DASHBOARD_MODULES
        slugs = [m['slug'] for m in DASHBOARD_MODULES]
        self.assertNotIn('recent_activity', slugs)
        self.assertNotIn('recent_platinums', slugs)


class DashboardConfigSlugMigrationTest(TestCase):
    """Phase 6: data migration helpers that rewrite legacy dashboard slugs.

    The migration logic lives in trophies/migrations/0186_dashboardconfig_pursuit_activity.py
    as pure-functional helpers. We test the helpers directly because they take
    plain Python lists/dicts and don't touch the DB, which gives us tight,
    fast tests that don't require a real DashboardConfig row mutation cycle.
    """

    def _import_helpers(self):
        # The migration module isn't a normal package member; import via
        # the migrations subpackage.
        from importlib import import_module
        return import_module('trophies.migrations.0186_dashboardconfig_pursuit_activity')

    def test_module_order_replaces_both_legacy_slugs_with_one_pursuit(self):
        helpers = self._import_helpers()
        result = helpers._rewrite_module_order([
            'trophy_snapshot', 'recent_platinums', 'challenge_hub',
            'recent_activity', 'badge_progress',
        ])
        # First legacy slug position becomes pursuit_activity, second is dropped
        self.assertEqual(result, [
            'trophy_snapshot', 'pursuit_activity', 'challenge_hub', 'badge_progress',
        ])

    def test_module_order_with_only_one_legacy_slug(self):
        helpers = self._import_helpers()
        result = helpers._rewrite_module_order(['trophy_snapshot', 'recent_activity'])
        self.assertEqual(result, ['trophy_snapshot', 'pursuit_activity'])

    def test_module_order_with_no_legacy_slugs_unchanged(self):
        helpers = self._import_helpers()
        result = helpers._rewrite_module_order(['trophy_snapshot', 'challenge_hub'])
        self.assertEqual(result, ['trophy_snapshot', 'challenge_hub'])

    def test_module_order_dedupes_existing_pursuit_activity(self):
        """If the user somehow has both pursuit_activity AND a legacy slug, dedupe."""
        helpers = self._import_helpers()
        result = helpers._rewrite_module_order([
            'pursuit_activity', 'recent_activity', 'badge_progress',
        ])
        self.assertEqual(result, ['pursuit_activity', 'badge_progress'])

    def test_hidden_modules_promotes_legacy_to_pursuit(self):
        helpers = self._import_helpers()
        result = helpers._rewrite_hidden_modules(['recent_activity', 'quick_settings'])
        self.assertEqual(set(result), {'pursuit_activity', 'quick_settings'})

    def test_hidden_modules_no_legacy_unchanged(self):
        helpers = self._import_helpers()
        result = helpers._rewrite_hidden_modules(['quick_settings'])
        self.assertEqual(result, ['quick_settings'])

    def test_settings_dict_merges_legacy_settings(self):
        helpers = self._import_helpers()
        result = helpers._rewrite_settings_dict({
            'recent_activity': {'limit': 12},
            'recent_platinums': {'limit': 6},
            'unrelated_module': {'foo': 'bar'},
        })
        self.assertNotIn('recent_activity', result)
        self.assertNotIn('recent_platinums', result)
        self.assertEqual(result['pursuit_activity']['limit'], 12)  # recent_activity wins
        self.assertEqual(result['unrelated_module'], {'foo': 'bar'})

    def test_settings_dict_no_legacy_unchanged(self):
        helpers = self._import_helpers()
        original = {'unrelated_module': {'foo': 'bar'}}
        self.assertEqual(helpers._rewrite_settings_dict(original), original)

    def test_settings_dict_existing_pursuit_activity_takes_priority(self):
        """If the user already has pursuit_activity settings, they win the merge."""
        helpers = self._import_helpers()
        result = helpers._rewrite_settings_dict({
            'pursuit_activity': {'limit': 5},  # user-set, should win
            'recent_activity': {'limit': 12},  # legacy, would default
        })
        self.assertEqual(result['pursuit_activity']['limit'], 5)

    def test_full_migration_e2e_against_real_dashboard_config(self):
        """End-to-end: create a DashboardConfig with legacy slugs, run rewrite_configs, verify."""
        from trophies.models import DashboardConfig
        helpers = self._import_helpers()

        user = CustomUser.objects.create_user(
            email='cfg@example.com', password='testpass123',
        )
        profile = Profile.objects.create(
            user=user, psn_username='cfg_user',
            account_id='cfg-001', is_linked=True,
        )
        config = DashboardConfig.objects.create(
            profile=profile,
            module_order=['trophy_snapshot', 'recent_activity', 'recent_platinums', 'challenge_hub'],
            hidden_modules=['recent_activity'],
            module_settings={
                'recent_activity': {'limit': 12},
                'recent_platinums': {'limit': 10},
            },
            tab_config={
                'module_tab_overrides': {
                    'recent_activity': 'My Custom Tab',
                    'recent_platinums': 'My Custom Tab',
                    'badge_progress': 'Other Tab',
                },
            },
        )

        # Patch the apps.get_model lookup so the helper uses the real model
        from unittest.mock import MagicMock
        fake_apps = MagicMock()
        fake_apps.get_model.return_value = DashboardConfig
        helpers.rewrite_configs(fake_apps, None)

        config.refresh_from_db()
        self.assertEqual(config.module_order, [
            'trophy_snapshot', 'pursuit_activity', 'challenge_hub',
        ])
        self.assertEqual(config.hidden_modules, ['pursuit_activity'])
        self.assertEqual(config.module_settings['pursuit_activity']['limit'], 12)
        self.assertNotIn('recent_activity', config.module_settings)
        self.assertNotIn('recent_platinums', config.module_settings)
        overrides = config.tab_config['module_tab_overrides']
        self.assertNotIn('recent_activity', overrides)
        self.assertNotIn('recent_platinums', overrides)
        self.assertEqual(overrides['pursuit_activity'], 'My Custom Tab')
        self.assertEqual(overrides['badge_progress'], 'Other Tab')


class CommunityHubTest(TestCase):
    """Phase 7: Community Hub page at /community/.

    Covers:
    - ReviewHubService.get_top_reviewers (new method)
    - community_hub_service.build_community_hub_context (the assembler)
    - End-to-end view dispatch via the test client (anonymous + logged-in)
    - Module isolation: a single broken module does not break the page
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email='hub@example.com',
            password='testpass123',
        )
        self.profile = Profile.objects.create(
            user=self.user,
            psn_username='hub_user',  # 8 chars
            account_id='hub-001',
            is_linked=True,
            country='United States',
            country_code='US',
        )

    # ---- ReviewHubService.get_top_reviewers ---------------------------

    def test_top_reviewers_returns_profiles_with_helpful_votes(self):
        from trophies.services.review_hub_service import ReviewHubService

        concept = Concept.objects.create(
            unified_title='Top Test', concept_id='CUSA40001', slug='top-test',
        )
        ctg = ConceptTrophyGroup.objects.create(
            concept=concept, trophy_group_id='default', display_name='Base',
        )
        Review.objects.create(
            profile=self.profile,
            concept=concept,
            concept_trophy_group=ctg,
            body='An honest opinion with enough characters to pass validation.' * 2,
            recommended=True,
            helpful_count=42,
        )

        result = ReviewHubService.get_top_reviewers(limit=10)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['psn_username'], 'hub_user')
        self.assertEqual(result[0]['total_helpful'], 42)
        self.assertEqual(result[0]['review_count'], 1)

    def test_top_reviewers_excludes_zero_helpful_count(self):
        """Profiles whose only reviews have 0 helpful votes don't appear."""
        from trophies.services.review_hub_service import ReviewHubService

        concept = Concept.objects.create(
            unified_title='Zero Test', concept_id='CUSA40002', slug='zero-test',
        )
        ctg = ConceptTrophyGroup.objects.create(
            concept=concept, trophy_group_id='default', display_name='Base',
        )
        Review.objects.create(
            profile=self.profile,
            concept=concept,
            concept_trophy_group=ctg,
            body='Brand new review with no votes yet that has enough characters.' * 2,
            recommended=True,
            helpful_count=0,
        )

        result = ReviewHubService.get_top_reviewers(limit=10)
        self.assertEqual(len(result), 0)

    def test_top_reviewers_excludes_deleted_reviews(self):
        """Soft-deleted reviews don't contribute to the helpful_count sum."""
        from trophies.services.review_hub_service import ReviewHubService

        concept = Concept.objects.create(
            unified_title='Del Test', concept_id='CUSA40003', slug='del-test',
        )
        ctg = ConceptTrophyGroup.objects.create(
            concept=concept, trophy_group_id='default', display_name='Base',
        )
        Review.objects.create(
            profile=self.profile,
            concept=concept,
            concept_trophy_group=ctg,
            body='Soft deleted review that should not count toward totals.' * 2,
            recommended=True,
            helpful_count=99,
            is_deleted=True,
        )

        result = ReviewHubService.get_top_reviewers(limit=10)
        self.assertEqual(len(result), 0)

    def test_top_reviewers_orders_by_total_helpful_descending(self):
        """Multiple reviewers are ordered by their summed helpful_count desc."""
        from trophies.services.review_hub_service import ReviewHubService

        # Create a second profile with MORE helpful votes
        user2 = CustomUser.objects.create_user(
            email='hub2@example.com', password='testpass123', username='hub2',
        )
        profile2 = Profile.objects.create(
            user=user2, psn_username='hub_top',
            account_id='hub-002', is_linked=True,
        )

        concept = Concept.objects.create(
            unified_title='Order Test', concept_id='CUSA40004', slug='order-test',
        )
        ctg = ConceptTrophyGroup.objects.create(
            concept=concept, trophy_group_id='default', display_name='Base',
        )

        # self.profile: 50 helpful
        Review.objects.create(
            profile=self.profile, concept=concept, concept_trophy_group=ctg,
            body='Review one with enough characters to pass validation cleanly.' * 2,
            recommended=True, helpful_count=50,
        )
        # profile2: 100 helpful
        concept2 = Concept.objects.create(
            unified_title='Order Test 2', concept_id='CUSA40005', slug='order-test-2',
        )
        ctg2 = ConceptTrophyGroup.objects.create(
            concept=concept2, trophy_group_id='default', display_name='Base',
        )
        Review.objects.create(
            profile=profile2, concept=concept2, concept_trophy_group=ctg2,
            body='Review two with enough characters to pass validation cleanly.' * 2,
            recommended=True, helpful_count=100,
        )

        result = ReviewHubService.get_top_reviewers(limit=10)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['psn_username'], 'hub_top')
        self.assertEqual(result[0]['total_helpful'], 100)
        self.assertEqual(result[1]['psn_username'], 'hub_user')

    # ---- community_hub_service.build_community_hub_context ------------

    def test_build_context_returns_all_expected_keys(self):
        from core.services.community_hub_service import build_community_hub_context

        context = build_community_hub_context(viewer_profile=None)
        self.assertIn('feed_preview', context)
        self.assertIn('xp_leaderboard', context)
        self.assertIn('country_leaderboard', context)
        self.assertIn('top_reviewers', context)
        self.assertIn('active_challenges', context)
        self.assertIn('site_heartbeat', context)

    def test_build_context_anonymous_no_country_leaderboard(self):
        """Anonymous viewers get country_leaderboard=None."""
        from core.services.community_hub_service import build_community_hub_context
        context = build_community_hub_context(viewer_profile=None)
        self.assertIsNone(context['country_leaderboard'])

    def test_build_context_logged_in_with_country_attempts_country_leaderboard(self):
        """A logged-in user with a country code gets a non-None country_leaderboard dict.

        The actual entries list will be empty in tests because the Redis
        leaderboard sorted set is also empty, but the structure should be
        present (not None).
        """
        from core.services.community_hub_service import build_community_hub_context
        context = build_community_hub_context(viewer_profile=self.profile)
        self.assertIsNotNone(context['country_leaderboard'])
        self.assertEqual(context['country_leaderboard']['country_code'], 'US')

    def test_build_context_feed_preview_only_includes_pursuit_feed_types(self):
        """The feed preview filters to PURSUIT_FEED_TYPES (excludes day_zero)."""
        from core.services.community_hub_service import build_community_hub_context

        Event.objects.create(
            profile=self.profile,
            event_type='platinum_earned',
            occurred_at=timezone.now(),
            metadata={'trophy_name': 'The Plat'},
        )
        # day_zero exists from migration but should NOT appear
        context = build_community_hub_context(viewer_profile=None)
        types = [e.event_type for e in context['feed_preview']]
        self.assertIn('platinum_earned', types)
        self.assertNotIn('day_zero', types)

    def test_build_context_active_challenges_filters_to_started_completed(self):
        """active_challenges only contains challenge_started and challenge_completed events."""
        from core.services.community_hub_service import build_community_hub_context

        Event.objects.create(
            profile=self.profile, event_type='challenge_started',
            occurred_at=timezone.now(),
            metadata={'challenge_type': 'az', 'name': 'Test'},
        )
        Event.objects.create(
            profile=self.profile, event_type='challenge_progress',
            occurred_at=timezone.now(),
            metadata={'challenge_type': 'az'},
        )
        Event.objects.create(
            profile=self.profile, event_type='platinum_earned',
            occurred_at=timezone.now(),
            metadata={'trophy_name': 'X'},
        )

        context = build_community_hub_context(viewer_profile=None)
        types = [e.event_type for e in context['active_challenges']]
        self.assertIn('challenge_started', types)
        self.assertNotIn('challenge_progress', types)
        self.assertNotIn('platinum_earned', types)

    def test_build_context_module_isolation_on_failure(self):
        """A single failing module returns its empty fallback rather than breaking the page."""
        from core.services.community_hub_service import build_community_hub_context

        with patch(
            'core.services.community_hub_service._get_top_reviewers',
            side_effect=RuntimeError("simulated failure"),
        ):
            context = build_community_hub_context(viewer_profile=None)

        # Other modules still loaded successfully
        self.assertIn('feed_preview', context)
        self.assertIn('xp_leaderboard', context)
        # Failing module fell back to empty list
        self.assertEqual(context['top_reviewers'], [])

    # ---- End-to-end view dispatch --------------------------------------

    def test_anonymous_get_returns_200(self):
        from django.test import Client
        client = Client()
        response = client.get('/community/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('feed_preview', response.context)

    def test_logged_in_get_includes_viewer_profile_in_context(self):
        """Logged-in users get viewer-specific touches (rank highlight, country leaderboard)."""
        from django.test import Client
        client = Client()
        client.force_login(self.user)
        response = client.get('/community/')
        self.assertEqual(response.status_code, 200)
        # Country leaderboard should be present (we set country_code='US' in setUp)
        self.assertIsNotNone(response.context['country_leaderboard'])

    def test_template_renders_breadcrumb_and_seo(self):
        """The view sets the right context for SEO meta tags + breadcrumb."""
        from django.test import Client
        client = Client()
        response = client.get('/community/')
        self.assertEqual(response.context['seo_title'], 'Community Hub - Platinum Pursuit')
        self.assertIn('Pursuit Feed', response.context['seo_description'])
        # Breadcrumb has Home + Community Hub
        self.assertEqual(len(response.context['breadcrumb']), 2)

    def test_url_is_named_community_hub(self):
        """The URL pattern is registered with name='community_hub' for reverse() lookups."""
        from django.urls import reverse
        self.assertEqual(reverse('community_hub'), '/community/')
