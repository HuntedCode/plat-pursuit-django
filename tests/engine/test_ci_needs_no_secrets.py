"""The CI contract: the suite must pass with NO real credentials in the environment.

`.github/workflows/tests.yml` states it outright -- "No secrets needed: CI spins up its own
Postgres + Redis service containers and uses settings_test, which requires no real API keys."
Nothing enforced it, and the workflow only runs on `push: [main]` and `pull_request`, so a branch
that never opened a PR never met a secret-less environment. Six tests drifted onto a developer's
`.env` and stayed green locally for as long as that was the only place they ran.

The failure mode is the expensive one: not a red suite while you work, but a red suite the moment
you open the PR you have been building toward, on the day you meant to deploy.

Each setting below is one a test actually depends on. The rule: if a test needs a credential, the
credential gets a deterministic non-secret value in `settings_test`, never a real one from `.env`.
"""
import os

import pytest
from django.conf import settings

#: setting -> what silently breaks when it is None under a secret-less CI run.
REQUIRED_IN_TESTS = {
    'SECRET_KEY': 'Django refuses to start.',
    'BOT_API_KEY': (
        'tests/engine/test_badge_legacy_consumers_repointed.py mints a DRF Token with this as its '
        'key; None makes the bot authenticate as nobody and /recheck-badges 403s.'
    ),
    'PAYPAL_CLIENT_ID': (
        'the support storefront gates its PayPal button on it, so the checkout renders one '
        'provider button instead of two and the form-enclosure guard fails.'
    ),
}


@pytest.mark.parametrize('name', sorted(REQUIRED_IN_TESTS))
def test_the_setting_is_populated_without_a_dotenv(name):
    value = getattr(settings, name, None)
    assert value, (
        f'settings.{name} is empty under settings_test. {REQUIRED_IN_TESTS[name]} '
        f'Give it a deterministic non-secret value in settings_test.py rather than letting it '
        f'fall through to os.getenv.'
    )


@pytest.mark.parametrize('name', sorted(REQUIRED_IN_TESTS))
def test_the_value_does_not_come_from_the_environment(name):
    """The one that actually pins CI. A setting can be populated locally purely because the
    developer's `.env` supplies it -- which is exactly the state that shipped six failures. It has
    to be a literal in settings_test, so it holds on a machine with no .env at all."""
    env_value = os.getenv(name)
    if env_value is None:
        return   # nothing in the environment to be fooled by; the test above already covers it
    assert getattr(settings, name, None) != env_value, (
        f'settings.{name} equals the value in your environment, so it is coming from .env rather '
        f'than from settings_test. It will be None in CI. Pin a literal in settings_test.py.'
    )


def test_the_workflow_still_claims_to_need_no_secrets():
    """If the workflow ever grows a `secrets.` reference, this file's premise is void and the list
    above should be revisited rather than silently kept in step."""
    import pathlib

    wf = (pathlib.Path(__file__).resolve().parents[2]
          / '.github' / 'workflows' / 'tests.yml').read_text(encoding='utf-8')

    assert 'secrets.' not in wf, (
        'the CI workflow now injects secrets -- update this module, which exists to keep the '
        'suite runnable without any'
    )
