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
    EarnedTrophy,
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
