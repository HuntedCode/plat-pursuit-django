"""Likes, on the question and on the answer.

Two targets, one implementation, six rules -- and the rules are the interesting part, because a like
is wordless and that makes every one of them look skippable.
"""
import pytest

from prompts.models import (MIN_GAMES_TO_PUBLISH, SHAPE_POLL, SHAPE_TIER, Prompt, PromptLike,
                            PromptResponse, PromptResponseLike)
from prompts.services import prompt_service as svc
from prompts.services import response_service as rsvc
from prompts.services import social_service as social
from tests.factories import ConceptFactory, ProfileFactory
from users.models import UserRestriction

pytestmark = pytest.mark.django_db


def _hunter(psn='hunter'):
    return ProfileFactory(is_linked=True, psn_username=psn)


def _restrict(profile):
    UserRestriction.objects.create(user=profile.user, profile=profile, scope='all_ugc',
                                   reason='testing', created_by_label='Admin')


def _answered(owner, answerer, *, public=True):
    """A published tier list with one answer on it."""
    prompt = svc.create_prompt(owner, shape=SHAPE_TIER, title='Rank them')
    pool = [svc.add_concept(prompt, owner, ConceptFactory())
            for _ in range(MIN_GAMES_TO_PUBLISH[SHAPE_TIER])]
    svc.update_prompt(prompt, owner, is_public=True)
    prompt.refresh_from_db()
    rsvc.place(prompt, answerer, game_id=pool[0].pk,
               bucket_id=prompt.buckets.order_by('position').first().pk)
    response = rsvc.response_for(prompt, answerer)
    if not public:
        rsvc.set_public(prompt, answerer, is_public=False)
        response.refresh_from_db()
    return prompt, response


# ── the counters ──────────────────────────────────────────────────────────────────────────────────


def test_liking_and_unliking_both_targets_moves_the_right_counter():
    owner, answerer, fan = _hunter('owner'), _hunter('answerer'), _hunter('fan')
    prompt, response = _answered(owner, answerer)

    assert social.set_prompt_like(prompt, fan, liked=True) == 1
    assert social.set_response_like(response, fan, liked=True) == 1

    prompt.refresh_from_db()
    response.refresh_from_db()
    assert prompt.like_count == 1
    assert response.like_count == 1
    assert PromptLike.objects.count() == 1
    assert PromptResponseLike.objects.count() == 1

    assert social.set_prompt_like(prompt, fan, liked=False) == 0
    assert social.set_response_like(response, fan, liked=False) == 0
    prompt.refresh_from_db()
    assert prompt.like_count == 0
    assert not PromptLike.objects.exists()


def test_a_double_tap_counts_once():
    """`delta` comes from what the write actually DID, so idempotency needs no separate existence
    check -- and therefore has no race between checking and writing."""
    owner, answerer, fan = _hunter('owner'), _hunter('answerer'), _hunter('fan')
    prompt, response = _answered(owner, answerer)

    assert social.set_prompt_like(prompt, fan, liked=True) == 1
    assert social.set_prompt_like(prompt, fan, liked=True) == 1
    assert social.set_response_like(response, fan, liked=True) == 1
    assert social.set_response_like(response, fan, liked=True) == 1

    prompt.refresh_from_db()
    assert prompt.like_count == 1
    assert PromptLike.objects.count() == 1

    # ...and unliking twice does not go negative, which a PositiveIntegerField would raise over.
    social.set_prompt_like(prompt, fan, liked=False)
    assert social.set_prompt_like(prompt, fan, liked=False) == 0
    prompt.refresh_from_db()
    assert prompt.like_count == 0


def test_the_two_likes_are_independent():
    """Same hunter, same page, two different acts -- and two tables, so neither can collide with the
    other."""
    owner, answerer, fan = _hunter('owner'), _hunter('answerer'), _hunter('fan')
    prompt, response = _answered(owner, answerer)

    social.set_prompt_like(prompt, fan, liked=True)
    social.set_response_like(response, fan, liked=True)
    social.set_prompt_like(prompt, fan, liked=False)

    prompt.refresh_from_db()
    response.refresh_from_db()
    assert prompt.like_count == 0
    assert response.like_count == 1, "unliking the question took the answer's like with it"


# ── the refusals ──────────────────────────────────────────────────────────────────────────────────


def test_you_cannot_like_your_own():
    """Self-liking would let an author push their own work up the ranking their own count drives."""
    owner, answerer = _hunter('owner'), _hunter('answerer')
    prompt, response = _answered(owner, answerer)

    with pytest.raises(svc.PromptError):
        social.set_prompt_like(prompt, owner, liked=True)
    with pytest.raises(svc.PromptError):
        social.set_response_like(response, answerer, liked=True)

    prompt.refresh_from_db()
    assert prompt.like_count == 0


def test_a_like_cannot_confirm_that_a_private_prompt_exists():
    """Liking by id is an oracle if it answers differently for "private" and "not there"."""
    owner, fan = _hunter('owner'), _hunter('fan')
    draft = svc.create_prompt(owner, shape=SHAPE_POLL, title='Not yet')

    with pytest.raises(svc.PromptError):
        social.set_prompt_like(draft, fan, liked=True)
    draft.refresh_from_db()
    assert draft.like_count == 0


def test_an_answer_is_only_likeable_while_both_it_and_its_prompt_are_public():
    """TWO QUESTIONS, NOT ONE. A response and its prompt carry independent visibility flags by design,
    so asking only about the response would let a like confirm an answer to a withdrawn draft."""
    owner, answerer, fan = _hunter('owner'), _hunter('answerer'), _hunter('fan')

    prompt, private_answer = _answered(owner, answerer, public=False)
    with pytest.raises(svc.PromptError):
        social.set_response_like(private_answer, fan, liked=True)

    # Now make the answer public but take the prompt down. Nobody has answered but its own author's
    # responder, so the unpublish is refused -- reach the state the way the schema allows instead.
    rsvc.set_public(prompt, answerer, is_public=True)
    private_answer.refresh_from_db()
    Prompt.objects.filter(pk=prompt.pk).update(is_public=False)
    prompt.refresh_from_db()

    with pytest.raises(svc.PromptError):
        social.set_response_like(private_answer, fan, liked=True)
    private_answer.refresh_from_db()
    assert private_answer.like_count == 0


def test_an_empty_answer_is_not_likeable():
    """`listed_responses` and this refusal ask the same question, because an answer nobody can see on
    the browse page should not be reachable by id either."""
    owner, answerer, fan = _hunter('owner'), _hunter('answerer'), _hunter('fan')
    prompt, response = _answered(owner, answerer)
    rsvc.clear(prompt, answerer)
    empty = PromptResponse.objects.create(prompt=prompt, profile=answerer)

    with pytest.raises(svc.PromptError):
        social.set_response_like(empty, fan, liked=True)


def test_a_restricted_hunter_cannot_like_but_can_still_unlike():
    """A like is wordless, which is what makes this look skippable -- but it is a public signal
    attributed to a profile and it feeds a public ranking. Withdrawing one is never refused."""
    owner, answerer, fan = _hunter('owner'), _hunter('answerer'), _hunter('fan')
    prompt, response = _answered(owner, answerer)
    social.set_prompt_like(prompt, fan, liked=True)
    social.set_response_like(response, fan, liked=True)
    _restrict(fan)

    with pytest.raises(svc.PromptError):
        social.set_prompt_like(prompt, fan, liked=True)
    with pytest.raises(svc.PromptError):
        social.set_response_like(response, fan, liked=True)

    social.set_prompt_like(prompt, fan, liked=False)
    social.set_response_like(response, fan, liked=False)
    prompt.refresh_from_db()
    response.refresh_from_db()
    assert prompt.like_count == 0
    assert response.like_count == 0


def test_an_unlinked_hunter_cannot_like():
    owner, answerer = _hunter('owner'), _hunter('answerer')
    prompt, _response = _answered(owner, answerer)
    stranger = ProfileFactory(is_linked=False, psn_username='unlinked')

    with pytest.raises(svc.PromptError):
        social.set_prompt_like(prompt, stranger, liked=True)


def test_deleting_an_answer_takes_its_likes_with_it():
    """The likes belong to the answer, not to the prompt. Clearing an answer is the responder saying
    they never answered, so the applause for it goes too."""
    owner, answerer, fan = _hunter('owner'), _hunter('answerer'), _hunter('fan')
    prompt, response = _answered(owner, answerer)
    social.set_prompt_like(prompt, fan, liked=True)
    social.set_response_like(response, fan, liked=True)

    rsvc.clear(prompt, answerer)

    assert not PromptResponseLike.objects.exists()
    assert PromptLike.objects.count() == 1, "the question's likes went with the answer"

def test_a_like_can_always_be_withdrawn_even_after_the_prompt_goes_private():
    """RULE 1, WHICH THE CODE DID NOT HOLD. Every refusal except the linked check is scoped to
    liking.

    Reachable with no admin: a fan likes a published prompt; the author unpublishes it (legal --
    nobody had answered); the fan taps the lit heart. With visibility checked on the way out too,
    the unlike was refused, the like kept counting, and it re-entered the popular sort the moment
    the author republished. A signal you cannot withdraw is not a signal."""
    owner, fan = _hunter('owner'), _hunter('fan')
    # NOBODY HAS ANSWERED, which is what makes the unpublish legal and the scenario reachable -- an
    # answered prompt cannot be hidden at all. A brand-new prompt with a couple of likes and no
    # answers is the single most likely state in the first week of this feature.
    prompt = svc.create_prompt(owner, shape=SHAPE_TIER, title='Rank them')
    for _ in range(MIN_GAMES_TO_PUBLISH[SHAPE_TIER]):
        svc.add_concept(prompt, owner, ConceptFactory())
    svc.update_prompt(prompt, owner, is_public=True)
    prompt.refresh_from_db()
    social.set_prompt_like(prompt, fan, liked=True)

    svc.update_prompt(prompt, owner, is_public=False)
    prompt.refresh_from_db()

    assert social.set_prompt_like(prompt, fan, liked=False) == 0
    prompt.refresh_from_db()
    assert prompt.like_count == 0, 'a lit heart became permanent'


def test_an_answer_can_be_unliked_after_its_author_hides_it():
    """The same rule on the other target."""
    owner, answerer, fan = _hunter('owner'), _hunter('answerer'), _hunter('fan')
    prompt, response = _answered(owner, answerer)
    social.set_response_like(response, fan, liked=True)

    rsvc.set_public(prompt, answerer, is_public=False)
    response.refresh_from_db()

    assert social.set_response_like(response, fan, liked=False) == 0
    response.refresh_from_db()
    assert response.like_count == 0


def test_a_prompts_author_may_like_the_answers_to_their_own_question():
    """One of the things this feature is for -- and the self-like rule looks like it should
    generalise to the prompt's owner, so it is written down here rather than left to be tightened
    away by a future edit with a green suite."""
    owner, answerer = _hunter('owner'), _hunter('answerer')
    prompt, response = _answered(owner, answerer)

    assert social.set_response_like(response, owner, liked=True) == 1
    response.refresh_from_db()
    assert response.like_count == 1
