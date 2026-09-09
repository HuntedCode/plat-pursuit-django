"""Every model change has a migration.

The suite reads `model._meta`, so a model and its migrations can disagree and every test stays green --
while the DATABASE is built from the migrations. That gap is not hypothetical here: the Rarity Score board's
membership rule lives as a literal inside two partial index conditions in migration `0335`, and a
guard that pins it against `rarity_score.TOP_N` reads the MODEL's copy. Hand-edit the migration's literal and
the deployed indexes gate at a different number from the board, with nothing failing.

One check closes the whole class, for every app, not just this one.
"""
import pytest
from django.core.management import call_command
from django.core.management.base import SystemCheckError

pytestmark = pytest.mark.django_db


def test_no_model_change_is_missing_a_migration():
    """`makemigrations --check` exits non-zero when the models imply a migration that does not exist.

    It also catches the reverse of the case above: a migration edited by hand so it no longer matches the
    model it was generated from.
    """
    try:
        call_command('makemigrations', '--check', '--dry-run', verbosity=0)
    except SystemExit as exc:                       # --check exits 1 rather than raising
        pytest.fail(
            'models and migrations disagree -- run `python manage.py makemigrations`, or if you edited a '
            f'migration by hand, make the model match it ({exc})'
        )
    except SystemCheckError as exc:                 # pragma: no cover - config error, not drift
        pytest.fail(f'system checks failed before migration state could be compared: {exc}')
