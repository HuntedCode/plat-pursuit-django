"""Two authoring affordances in the Django admin: scoped subject pickers, and stage duplication.

Both exist because `Franchise` and `Stage` are shaped for the engine, not for the curator.

`Franchise` holds IGDB franchises AND IGDB collections in one table, so an unscoped picker offers
"Resident Evil" twice with nothing to tell the two rows apart, and `BadgeSeries.franchise` /
`.collection` become interchangeable by accident. The scope used to live in a `FranchiseAdmin`
hook that matched on the request's `model_name == 'badge'`; the badge rebuild renamed the model to
`badgeseries` and the filter silently stopped firing, which is what these tests pin against. It now
lives on the model field as `limit_choices_to`, which `AutocompleteJsonView` applies itself.

`Stage` joins to a series by a bare `series_slug` string, so authoring a second edition of a series
means re-entering the same concept picks stage by stage. The duplicate action copies them.

Only the OWNER can reach any of this (`is_superuser`), asserted in
`test_django_admin_is_the_owners.py` -- these tests are about what happens once they are in.
"""
import re

import pytest

from tests.factories import (BadgeSeriesFactory, ConceptFactory, StageFactory, UserFactory)
from trophies.models import BadgeSeries, ConceptBundle, Franchise, Stage

pytestmark = pytest.mark.django_db

STAGE_CHANGELIST = '/admin/trophies/stage/'
AUTOCOMPLETE = '/admin/autocomplete/'


def _owner(client):
    user = UserFactory()
    user.is_superuser = user.is_staff = True
    user.save()
    client.force_login(user)
    return user


def _subjects():
    """One franchise and one collection sharing a name -- the pair that collided in the picker."""
    franchise = Franchise.objects.create(
        igdb_id=9001, name='Resident Evil', slug='resident-evil-f', source_type='franchise')
    collection = Franchise.objects.create(
        igdb_id=9001, name='Resident Evil', slug='resident-evil-c', source_type='collection')
    return franchise, collection


def _autocomplete(client, field_name, term='Resident'):
    resp = client.get(AUTOCOMPLETE, {
        'app_label': 'trophies', 'model_name': 'badgeseries', 'field_name': field_name, 'term': term,
    })
    assert resp.status_code == 200, resp.content[:200]
    return {int(row['id']) for row in resp.json()['results']}


# ---------------------------------------------------------------------------------------------
#  Subject pickers
# ---------------------------------------------------------------------------------------------

def test_franchise_autocomplete_offers_only_franchises(client):
    _owner(client)
    franchise, collection = _subjects()

    ids = _autocomplete(client, 'franchise')

    assert franchise.pk in ids
    assert collection.pk not in ids, 'the collection double is still in the franchise picker'


def test_collection_autocomplete_offers_only_collections(client):
    _owner(client)
    franchise, collection = _subjects()

    ids = _autocomplete(client, 'collection')

    assert collection.pk in ids
    assert franchise.pk not in ids, 'the franchise double is still in the collection picker'


def test_scope_survives_the_admin_form_not_just_the_autocomplete_view(client):
    """`limit_choices_to` is enforced on VALIDATION too, so a hand-posted id of the wrong type is
    refused. The autocomplete tests above only prove what is OFFERED; a picker can be scoped and
    still accept anything posted into the underlying hidden input -- which is exactly what an
    autocomplete-only fix leaves open, since the widget's value is a plain text field."""
    _owner(client)
    franchise, collection = _subjects()

    resp = client.post('/admin/trophies/badgeseries/add/', {
        'name': 'Resident Evil', 'series_slug': 'resident-evil', 'badge_type': 'franchise',
        'completion_policy': 'all', 'min_required': '0', 'description': '', 'display_series': '',
        'franchise': str(collection.pk),   # the wrong TYPE, not a bogus id
        'group_badges-TOTAL_FORMS': '0', 'group_badges-INITIAL_FORMS': '0',
        'group_badges-MIN_NUM_FORMS': '0', 'group_badges-MAX_NUM_FORMS': '1000',
    })

    assert resp.status_code == 200, 'the wrong-type subject saved and redirected'
    assert not BadgeSeries.objects.filter(series_slug='resident-evil').exists()
    assert 'franchise' in resp.context['adminform'].form.errors


def test_other_franchise_pickers_still_see_both_types(client):
    """The scope is deliberately per-FIELD. `ConceptFranchise.franchise` links a concept to whatever
    IGDB listed, franchise or collection, and narrowing that one would drop half the real links."""
    _owner(client)
    franchise, collection = _subjects()

    resp = client.get(AUTOCOMPLETE, {
        'app_label': 'trophies', 'model_name': 'conceptfranchise',
        'field_name': 'franchise', 'term': 'Resident',
    })
    assert resp.status_code == 200
    ids = {int(row['id']) for row in resp.json()['results']}

    assert {franchise.pk, collection.pk} <= ids


def test_series_with_both_subjects_saves(client):
    """A megamix-ish series can legitimately name a franchise AND a collection; scoping each field
    must not make the pair unsavable."""
    franchise, collection = _subjects()
    series = BadgeSeriesFactory(series_slug='re-both', franchise=franchise, collection=collection)

    series.refresh_from_db()
    assert series.franchise_id == franchise.pk
    assert series.collection_id == collection.pk
    assert BadgeSeries.objects.filter(collection__source_type='collection').count() == 1


# ---------------------------------------------------------------------------------------------
#  Stage duplication
# ---------------------------------------------------------------------------------------------

def _stage(slug, number, **kwargs):
    return StageFactory(series_slug=slug, stage_number=number, **kwargs)


def _duplicate(client, stages, new_slug=None):
    """Run the action. Without `new_slug` this is the first click, which should stop and ask."""
    data = {'action': 'duplicate_to_series',
            '_selected_action': [str(s.pk) for s in stages]}
    if new_slug is not None:
        data['new_series_slug'] = new_slug
        data['_duplicate_confirm'] = '1'
    return client.post(STAGE_CHANGELIST, data, follow=True)


def test_first_click_asks_for_a_slug_and_copies_nothing(client):
    _owner(client)
    stage = _stage('soulsborne', 1, title='Demon’s Souls')

    resp = _duplicate(client, [stage])

    assert 'New series slug' in resp.content.decode()
    assert Stage.objects.count() == 1, 'the action copied before anyone typed a slug'


def test_duplicate_copies_every_field_and_both_qualifier_paths(client):
    _owner(client)
    source = _stage('soulsborne', 2, title='Bloodborne')
    standalone, episodic_a, episodic_b = ConceptFactory(), ConceptFactory(), ConceptFactory()
    source.concepts.add(standalone)
    bundle = ConceptBundle.objects.create(stage=source, label='PS3 Episodic', sort_order=3)
    bundle.concepts.set([episodic_a, episodic_b])
    source.refresh_from_db()   # `concepts.add` re-derived stage_icon under us

    _duplicate(client, [source], 'fromsoft')

    copy = Stage.objects.get(series_slug='fromsoft', stage_number=2)
    assert copy.pk != source.pk
    assert copy.title == 'Bloodborne'
    assert copy.stage_icon == source.stage_icon
    assert list(copy.concepts.all()) == [standalone]

    copied_bundle = copy.concept_bundles.get()
    assert copied_bundle.pk != bundle.pk, 'the bundle was MOVED, not copied'
    assert copied_bundle.label == 'PS3 Episodic'
    assert copied_bundle.sort_order == 3
    assert set(copied_bundle.concepts.all()) == {episodic_a, episodic_b}


def test_hand_set_icon_survives_on_a_stage_with_nothing_to_re_derive_from(client):
    """`stage_icon` is normally derived from the first concept and refreshed by a signal, so the copy
    gets the right icon for free -- EXCEPT on a stage with no concepts and no bundles, where the
    signal never fires and a curator's hand-set icon is the only value there is."""
    _owner(client)
    source = _stage('soulsborne', 1)
    Stage.objects.filter(pk=source.pk).update(stage_icon='https://example.test/hand-set.png')

    _duplicate(client, [source], 'fromsoft')

    copy = Stage.objects.get(series_slug='fromsoft', stage_number=1)
    assert copy.stage_icon == 'https://example.test/hand-set.png'


def test_originals_are_untouched(client):
    _owner(client)
    source = _stage('soulsborne', 1)
    concept = ConceptFactory()
    source.concepts.add(concept)
    ConceptBundle.objects.create(stage=source, label='Keep me')

    _duplicate(client, [source], 'fromsoft')

    source.refresh_from_db()
    assert source.series_slug == 'soulsborne'
    assert list(source.concepts.all()) == [concept]
    assert source.concept_bundles.count() == 1


def test_duplicates_a_whole_multi_stage_selection(client):
    _owner(client)
    stages = [_stage('soulsborne', n) for n in (0, 1, 2)]

    _duplicate(client, stages, 'fromsoft')

    assert sorted(Stage.objects.filter(series_slug='fromsoft')
                  .values_list('stage_number', flat=True)) == [0, 1, 2]


def test_collision_is_skipped_and_reported_never_renumbered(client):
    """`unique_together` would otherwise turn this into an IntegrityError 500, and renumbering would
    silently produce a stage list that is not the one the curator copied."""
    _owner(client)
    source = _stage('soulsborne', 1, title='Source')
    existing = _stage('fromsoft', 1, title='Already here')

    resp = _duplicate(client, [source], 'fromsoft')

    assert Stage.objects.filter(series_slug='fromsoft').count() == 1
    existing.refresh_from_db()
    assert existing.title == 'Already here'
    assert 'Skipped 1 stage' in resp.content.decode()


def test_partial_selection_copies_what_it_can(client):
    _owner(client)
    _stage('fromsoft', 1)
    sources = [_stage('soulsborne', 1), _stage('soulsborne', 2)]

    resp = _duplicate(client, sources, 'fromsoft')

    assert sorted(Stage.objects.filter(series_slug='fromsoft')
                  .values_list('stage_number', flat=True)) == [1, 2]
    body = resp.content.decode()
    assert 'Duplicated 1 stage' in body
    assert 'Skipped 1 stage' in body


def test_two_selected_stages_sharing_a_number_do_not_collide(client):
    """Stage numbers are unique per SLUG, so selecting stage 1 from two different series is a legal
    selection that becomes one collision on the target. The second is skipped like any other, rather
    than raising IntegrityError out of a queryset that had not been written to the DB yet."""
    _owner(client)
    first = _stage('soulsborne', 1, title='From the first series')
    second = _stage('capcom', 1, title='From the second series')

    resp = _duplicate(client, [first, second], 'megamix')

    copies = Stage.objects.filter(series_slug='megamix')
    assert copies.count() == 1
    assert 'Skipped 1 stage' in resp.content.decode()


def test_slug_is_canonicalized_not_trusted(client):
    """`SlugField`'s validator accepts uppercase, so `FromSoft` would save and then join to nothing:
    every stage lookup compares the slug exactly. Same rule as BadgeSeriesCreationForm."""
    _owner(client)
    source = _stage('soulsborne', 1)

    _duplicate(client, [source], '  FromSoft Games  ')

    assert Stage.objects.filter(series_slug='fromsoft-games').exists()
    assert not Stage.objects.filter(series_slug__in=['FromSoft Games', '  FromSoft Games  ']).exists()


def test_blank_slug_re_asks_instead_of_copying(client):
    _owner(client)
    source = _stage('soulsborne', 1)

    resp = _duplicate(client, [source], '   ')

    assert 'Enter a slug' in resp.content.decode()
    assert Stage.objects.count() == 1


def test_slug_of_only_punctuation_re_asks(client):
    """`slugify('---')` is the empty string, which would store a stage nothing can ever join to."""
    _owner(client)
    source = _stage('soulsborne', 1)

    resp = _duplicate(client, [source], '!!!')

    assert 'Enter a slug' in resp.content.decode()
    assert Stage.objects.count() == 1


def test_the_confirm_page_posts_everything_the_action_needs(client):
    """Submit the form the page actually RENDERED. Hand-building the second POST (which the tests
    above do, for brevity) leaves the page's own hidden inputs untested: drop the `action` field
    from the template and all of them still pass, while a browser gets Django's "No action
    selected." and nothing is copied."""
    _owner(client)
    source = _stage('soulsborne', 1)

    page = client.post(STAGE_CHANGELIST, {'action': 'duplicate_to_series',
                                          '_selected_action': [str(source.pk)]})
    html = page.content.decode()
    # The close tag AFTER our form's start -- the admin shell renders its own forms above ours.
    start = html.index('<form method="post"')
    form = html[start:html.index('</form>', start)]
    fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', form))
    selected = re.findall(r'name="_selected_action" value="([^"]+)"', form)
    fields.pop('_selected_action', None)
    fields.pop('csrfmiddlewaretoken', None)

    assert selected == [str(source.pk)]
    resp = client.post(STAGE_CHANGELIST,
                       {**fields, '_selected_action': selected, 'new_series_slug': 'fromsoft'},
                       follow=True)

    assert Stage.objects.filter(series_slug='fromsoft', stage_number=1).exists()
    assert 'Duplicated 1 stage' in resp.content.decode()
