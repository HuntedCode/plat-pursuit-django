"""Shared factory-boy factories.

Build valid model instances in one line so individual tests stay focused on the
behavior under test, not on assembling fixtures. Start small (the objects the
engine touches most) and grow this as the spine test suite expands.

    from tests.factories import ProfileFactory
    profile = ProfileFactory()                 # also creates a linked CustomUser
    profile = ProfileFactory(psn_username="x") # override any field
"""

import factory
from django.utils import timezone

from trophies.models import (
    Badge,
    Comment,
    Company,
    Concept,
    ConceptBundle,
    ConceptCompany,
    ConceptGenre,
    ConceptTrophyGroup,
    EarnedTrophy,
    Game,
    Genre,
    IGDBMatch,
    Profile,
    ProfileGame,
    Review,
    Stage,
    Trophy,
    UserBadge,
    UserBadgeProgress,
    UserConceptRating,
)
from users.models import CustomUser


class UserFactory(factory.django.DjangoModelFactory):
    """A CustomUser created through the manager's create_user (email-based auth)."""

    class Meta:
        model = CustomUser

    email = factory.Sequence(lambda n: f"user{n}@example.com")

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        # Route through create_user so password hashing + email normalization run
        # exactly as they do in production (USERNAME_FIELD is email).
        password = kwargs.pop("password", "password123")
        return model_class.objects.create_user(password=password, **kwargs)


class ProfileFactory(factory.django.DjangoModelFactory):
    """A linked Profile. psn_username must match ^[a-zA-Z0-9_-]{3,16}$ and be unique."""

    class Meta:
        model = Profile

    user = factory.SubFactory(UserFactory)
    psn_username = factory.Sequence(lambda n: f"hunter{n:04d}")


class ConceptFactory(factory.django.DjangoModelFactory):
    """A Concept. Only concept_id is required; unified_title drives the slug."""

    class Meta:
        model = Concept

    concept_id = factory.Sequence(lambda n: f"CUSA{n:05d}")
    unified_title = factory.Sequence(lambda n: f"Test Game {n}")


class ConceptTrophyGroupFactory(factory.django.DjangoModelFactory):
    """A trophy group on a Concept. trophy_group_id 'default' = base game."""

    class Meta:
        model = ConceptTrophyGroup

    concept = factory.SubFactory(ConceptFactory)
    trophy_group_id = "default"
    display_name = "Base Game"


class UserConceptRatingFactory(factory.django.DjangoModelFactory):
    """A rating. concept_trophy_group=None means a base-game rating."""

    class Meta:
        model = UserConceptRating

    profile = factory.SubFactory(ProfileFactory)
    concept = factory.SubFactory(ConceptFactory)
    concept_trophy_group = None
    difficulty = 5
    grindiness = 5
    hours_to_platinum = 20
    fun_ranking = 7
    overall_rating = 4.0


class ReviewFactory(factory.django.DjangoModelFactory):
    """A review. concept defaults to the CTG's concept so the two stay consistent."""

    class Meta:
        model = Review

    profile = factory.SubFactory(ProfileFactory)
    concept_trophy_group = factory.SubFactory(ConceptTrophyGroupFactory)
    concept = factory.SelfAttribute("concept_trophy_group.concept")
    body = "A valid review body, comfortably over the minimum length for testing."
    recommended = True


class CommentFactory(factory.django.DjangoModelFactory):
    """A concept-level comment (trophy_id/checklist_id null)."""

    class Meta:
        model = Comment

    profile = factory.SubFactory(ProfileFactory)
    concept = factory.SubFactory(ConceptFactory)
    body = "A test comment."


# --- IGDB enrichment (a Concept's match + its projected developer/genre rows) ---


class CompanyFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Company

    igdb_id = factory.Sequence(lambda n: 1000 + n)
    name = factory.Sequence(lambda n: f"Studio {n}")
    slug = factory.Sequence(lambda n: f"studio-{n}")


class ConceptCompanyFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ConceptCompany

    concept = factory.SubFactory(ConceptFactory)
    company = factory.SubFactory(CompanyFactory)
    is_developer = True


class GenreFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Genre

    igdb_id = factory.Sequence(lambda n: 2000 + n)
    name = factory.Sequence(lambda n: f"Genre {n}")
    slug = factory.Sequence(lambda n: f"genre-{n}")


class ConceptGenreFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ConceptGenre

    concept = factory.SubFactory(ConceptFactory)
    genre = factory.SubFactory(GenreFactory)


class IGDBMatchFactory(factory.django.DjangoModelFactory):
    """A Concept's IGDB match. concept is OneToOne, so one per concept."""

    class Meta:
        model = IGDBMatch

    concept = factory.SubFactory(ConceptFactory)
    igdb_id = factory.Sequence(lambda n: 3000 + n)
    igdb_name = factory.Sequence(lambda n: f"IGDB Game {n}")
    status = "auto_accepted"


# --- Badges + gamification ----------------------------------------------------


class BadgeFactory(factory.django.DjangoModelFactory):
    """A badge. tier 1=Bronze, 2=Silver, 3=Gold, 4=Platinum; series_slug groups tiers."""

    class Meta:
        model = Badge

    name = factory.Sequence(lambda n: f"Badge {n}")
    series_slug = factory.Sequence(lambda n: f"series-{n}")
    tier = 1


class UserBadgeFactory(factory.django.DjangoModelFactory):
    """An earned badge. Saving fires the gamification-update signal."""

    class Meta:
        model = UserBadge

    profile = factory.SubFactory(ProfileFactory)
    badge = factory.SubFactory(BadgeFactory)


class UserBadgeProgressFactory(factory.django.DjangoModelFactory):
    """Per-(profile, badge) progress. Saving fires the gamification-update signal."""

    class Meta:
        model = UserBadgeProgress

    profile = factory.SubFactory(ProfileFactory)
    badge = factory.SubFactory(BadgeFactory)
    completed_concepts = 0


# --- Games + badge evaluation inputs ------------------------------------------


class GameFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Game

    title_name = factory.Sequence(lambda n: f"Game {n}")
    np_communication_id = factory.Sequence(lambda n: f"NPWR{n:05d}_00")
    concept = factory.SubFactory(ConceptFactory)


class ProfileGameFactory(factory.django.DjangoModelFactory):
    """A profile's play record for a game. has_plat / progress drive badge eval."""

    class Meta:
        model = ProfileGame

    profile = factory.SubFactory(ProfileFactory)
    game = factory.SubFactory(GameFactory)
    progress = 0
    has_plat = False
    most_recent_trophy_date = factory.LazyFunction(timezone.now)


class StageFactory(factory.django.DjangoModelFactory):
    """A badge stage. Add concepts via stage.concepts.add(...); required_tiers
    defaults to [] which means "applies to every tier"."""

    class Meta:
        model = Stage

    series_slug = factory.Sequence(lambda n: f"series-{n}")
    stage_number = 1
    required_tiers = factory.LazyFunction(list)


class ConceptBundleFactory(factory.django.DjangoModelFactory):
    """A bundle of concepts acting as one qualifier on a stage. Add members via
    bundle.concepts.add(...)."""

    class Meta:
        model = ConceptBundle

    stage = factory.SubFactory(StageFactory)
    label = "Bundle"


class TrophyFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Trophy

    game = factory.SubFactory(GameFactory)
    trophy_id = factory.Sequence(lambda n: n)
    trophy_type = "bronze"
    trophy_name = factory.Sequence(lambda n: f"Trophy {n}")


class EarnedTrophyFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = EarnedTrophy

    profile = factory.SubFactory(ProfileFactory)
    trophy = factory.SubFactory(TrophyFactory)
    earned = True
    earned_date_time = factory.LazyFunction(timezone.now)
