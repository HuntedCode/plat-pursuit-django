"""Every seeded job has a Lucide icon that resolves to a real glyph, and the tag renders SVG."""
import pytest

from trophies.models import Job
from trophies.services.job_render import DISCIPLINE_ICON
from trophies.templatetags.job_icons import _ICONS, job_icon

pytestmark = pytest.mark.django_db


def test_every_job_has_a_registered_icon():
    jobs = list(Job.objects.all())
    assert len(jobs) == 25                                  # the 24 specializations + Freelancer
    for job in jobs:
        assert job.icon, f"{job.slug} has no icon assigned"
        assert job.icon in _ICONS, f"{job.slug}'s icon '{job.icon}' is not in the registry"


def test_every_discipline_icon_is_registered():
    # The dossier / character-sheet section headers resolve through the same registry.
    for slug, icon in DISCIPLINE_ICON.items():
        assert icon in _ICONS, f"discipline {slug}'s icon '{icon}' is not in the registry"


def test_job_icon_tag_renders_and_degrades():
    svg = job_icon('swords', 'w-6 h-6')
    assert svg.startswith('<svg') and 'w-6 h-6' in svg and 'viewBox="0 0 24 24"' in svg
    assert 'pathLength="1"' in svg                          # shapes normalized for the stroke-draw
    assert job_icon('') == ''                               # blank -> nothing
    assert job_icon('nonexistent') == ''                   # unknown -> nothing


def test_the_shared_use_tag_keeps_its_attributes():
    """`job_icon_use` is read by ten hosts that rely on its presentation attributes (no CSS supplies them).
    The contract card's trim went into a SEPARATE tag, `job_icon_use_bare`, so this output must not move."""
    from trophies.templatetags.job_icons import job_icon_use

    assert str(job_icon_use('swords', 'w-5 h-5')) == (
        '<svg class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><use href="#jobicon-swords"/></svg>')


def test_the_bare_use_tag_leaves_presentation_to_css():
    from trophies.templatetags.job_icons import job_icon_use_bare

    assert str(job_icon_use_bare('swords')) == '<svg aria-hidden="true"><use href="#jobicon-swords"/></svg>'
    assert job_icon_use_bare('not-an-icon') == ''


def test_the_card_svg_rule_supplies_what_the_bare_tag_dropped():
    """The bare glyph draws only because `.rp-jobcell svg` sets the stroke presentation the attributes used
    to: without it, a <use> of a stroke-only symbol renders filled black, or not at all."""
    import pathlib
    import re

    css = (pathlib.Path(__file__).resolve().parents[2] / 'static' / 'css' / 'components' / 'elements.css'
           ).read_text(encoding='utf-8')
    rule = re.search(r'\.rp-jobcell svg \{([^}]*)\}', css)
    assert rule, 'the card glyph rule is gone'
    for decl in ('fill: none', 'stroke: currentColor', 'stroke-width: 2', 'stroke-linecap: round',
                 'stroke-linejoin: round', 'width: 64%', 'height: 64%'):
        assert decl in rule.group(1), f'`.rp-jobcell svg` lost `{decl}`'
    assert 'aspect-ratio: 1' in rule.group(1), 'the bare glyph has no intrinsic shape without it'
    for disc in ('combat', 'exploration', 'mind', 'heart', 'finesse'):
        assert f'.rp-jobcell--{disc} {{ --disc: var(--disc-{disc}); }}' in css, f'no colour class for {disc}'

    # ...and the BUILT stylesheet the page loads has it too (a forgotten `npm run build` ships the source
    # change to nobody).
    built = (pathlib.Path(__file__).resolve().parents[2] / 'static' / 'css' / 'output.css').read_text(encoding='utf-8')
    built_rule = re.search(r'\.rp-jobcell svg\{([^}]*)\}', built)
    assert built_rule, 'output.css was not rebuilt with the card glyph rule'
    for decl in ('fill:none', 'stroke:currentColor', 'stroke-width:2px', 'aspect-ratio:1'):   # minified, any order
        assert decl in built_rule.group(1), f'output.css is stale: its card glyph rule lacks `{decl}`'
    assert '.rp-jobcell--finesse{--disc:var(--disc-finesse)}' in built
