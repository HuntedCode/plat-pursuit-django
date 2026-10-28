"""The breadcrumb row shows only when the trail names a parent, and is slim when it does (owner, 2026-10-28).

A two-crumb trail is Home plus the page ("Home / Games"), which the page header and the sub-nav's lit pill
already say, and it cost 52px above every page's first card on a laptop. Rule and spacing: docs/reference/design-system.md, "Breadcrumbs".
"""
import json
import re

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

pytestmark = pytest.mark.django_db

TRAIL = 'aria-label="Breadcrumb"'


def _crumbs(n):
    return [{'text': f'Crumb {i}', 'url': f'/c{i}/'} for i in range(n - 1)] + [{'text': 'Here'}] if n else []


def _schema_names(body):
    for raw in re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.S):
        data = json.loads(raw)
        if data.get('@type') == 'BreadcrumbList':
            return [i['name'] for i in data['itemListElement']]
    return None


@pytest.mark.parametrize('n, shown', [(0, False), (1, False), (2, False), (3, True), (4, True)])
def test_the_row_shows_only_past_home_and_the_page(n, shown):
    html = render_to_string('partials/breadcrumb.html', {'items': _crumbs(n)})

    assert (TRAIL in html) is shown


class _Message:
    level_tag = 'success'

    def __str__(self):
        return 'Saved your list.'


def test_a_hidden_trail_still_shows_the_page_messages():
    """The partial is also the page's one messages renderer: hiding the row must not hide a "Saved" notice."""
    html = render_to_string('partials/breadcrumb.html', {'items': _crumbs(2), 'messages': [_Message()]})

    assert TRAIL not in html and 'Saved your list.' in html


def test_a_two_crumb_page_keeps_its_schema_and_draws_no_row(client):
    body = client.get(reverse('games_list')).content.decode()

    assert TRAIL not in body, '"Home / Games" repeats the lit Games pill'
    assert _schema_names(body) == ['Home', 'Games'], 'search engines still get the whole trail'


def test_a_detail_page_keeps_its_trail_in_a_slim_row(client):
    body = client.get(reverse('job_detail', args=['slayer'])).content.decode()
    start = body.index(TRAIL)
    row = body[body.rindex('<div', 0, start):body.index('</nav>', start)]

    assert 'Jobs' in row and 'Slayer' in row
    assert row.startswith('<div class="hidden md:flex'), 'the trail stays off phones'
    assert 'md:mb-2' in row and 'md:mb-4' not in row
    assert re.search(r'class="breadcrumbs py-0[ "]', row), "DaisyUI's 8px above and below the text is back"
