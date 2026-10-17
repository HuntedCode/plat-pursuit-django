"""The Challenge Optimizer's build line: a fair first-come line for whole-catalogue badge builds when every slot
is taken. Raw Redis (a fresh fakeredis per test, from conftest)."""
import time

import pytest

from challenges.services import build_line

USER, OTHER = 1, 2


def _r():
    return build_line.redis_client


def _fill_slots(started=None):
    """Every slot held by a running build (by default, one that started just now)."""
    started = time.time() if started is None else started
    _r().zadd(build_line.HOLDERS_KEY, {f'busy-{i}': started for i in range(build_line.SLOTS)})


def _free_slots(n=1):
    for member in _r().zrange(build_line.HOLDERS_KEY, 0, n - 1):
        _r().zrem(build_line.HOLDERS_KEY, member)


def _wait(ticket=None, user=USER):
    with pytest.raises(build_line.LineWait) as raised:
        with build_line.slot(ticket, user_id=user):
            pass
    return raised.value


def _build(ticket=None, user=USER):
    with build_line.slot(ticket, user_id=user):
        pass


def test_a_free_slot_builds_at_once_and_is_given_back():
    with build_line.slot(user_id=USER):
        assert _r().zcard(build_line.HOLDERS_KEY) == 1
    assert _r().zcard(build_line.HOLDERS_KEY) == 0


def test_a_slot_is_given_back_even_when_the_build_raises():
    with pytest.raises(ValueError):
        with build_line.slot(user_id=USER):
            raise ValueError('boom')
    assert _r().zcard(build_line.HOLDERS_KEY) == 0


def test_a_crashed_builds_slot_lapses_even_while_the_site_is_busy():
    # The first version's counter TTL was renewed by every take attempt, so under load a slot held by a build that
    # died mid-run was never given back. A lease lapses by AGE, however busy the line is.
    _fill_slots()
    waiting = [_wait(user=100 + i) for i in range(3)]   # a busy site: a line, and everyone re-asking
    crashed = time.time() - build_line.LEASE_SECONDS - 1
    _r().zadd(build_line.HOLDERS_KEY, {member: crashed for member in _r().zrange(build_line.HOLDERS_KEY, 0, -1)})
    _build(waiting[0].ticket, user=100)   # the crashed builds' slots came back: the front of the line goes


def test_full_slots_put_arrivals_in_line_in_order():
    _fill_slots()
    first, second = _wait(), _wait(user=OTHER)
    assert (first.position, second.position) == (1, 2)
    assert first.ticket != second.ticket


def test_a_newcomer_cannot_overtake_the_line_when_a_slot_frees():
    _fill_slots()
    first = _wait()
    _free_slots(1)
    # One free slot, one hunter waiting: the newcomer joins behind them.
    assert _wait(user=OTHER).position == 2
    _build(first.ticket)                       # the front of the line takes it
    assert not build_line.valid_ticket(first.ticket, USER)


def test_when_two_slots_free_the_first_two_in_line_both_go():
    _fill_slots()
    first, second = _wait(), _wait(user=OTHER)
    _free_slots(2)
    # Second place need not wait for first to re-ask: two free slots admit the first two.
    _build(second.ticket, user=OTHER)
    _build(first.ticket)


def test_re_asking_keeps_your_place_and_moves_you_up():
    _fill_slots()
    first, second = _wait(), _wait(user=OTHER)
    _free_slots(1)
    _build(first.ticket)
    _r().zadd(build_line.HOLDERS_KEY, {'newer-build': time.time()})   # the slot is busy again
    assert _wait(second.ticket, user=OTHER).position == 1


def test_a_hunter_who_left_drops_out_of_line():
    _fill_slots()
    gone, staying = _wait(), _wait(user=OTHER)
    _r().zadd(build_line.SEEN_KEY, {gone.ticket: time.time() - build_line.HEARTBEAT - 1})
    assert _wait(staying.ticket, user=OTHER).position == 1
    assert not build_line.valid_ticket(gone.ticket, USER)


def test_a_ticket_only_speaks_for_the_hunter_it_was_issued_to():
    _fill_slots()
    mine = _wait()
    assert build_line.valid_ticket(mine.ticket, USER)
    assert not build_line.valid_ticket(mine.ticket, OTHER)
    # Someone else vouching for it is refused, and the owner keeps their place.
    with pytest.raises(build_line.TicketLost):
        _build(mine.ticket, user=OTHER)
    assert _wait(mine.ticket).position == 1


def test_one_ticket_buys_one_build_however_many_requests_carry_it():
    # A later re-ask with a ticket that already built is refused at the check. (Two requests reaching the claim
    # together is `test_losing_the_claim_race_gives_the_lease_back`.)
    _fill_slots()
    mine = _wait()
    _free_slots(2)
    with build_line.slot(mine.ticket, user_id=USER):
        with pytest.raises(build_line.TicketLost):
            _build(mine.ticket)
        assert _r().zcard(build_line.HOLDERS_KEY) == 1   # only the first holds a slot
    assert _r().zcard(build_line.HOLDERS_KEY) == 0


def test_losing_the_claim_race_gives_the_lease_back(monkeypatch):
    _fill_slots()
    mine = _wait()
    _free_slots(2)
    real_zrem = _r().zrem
    # Another worker claims the ticket between this request's rank check and its claim.
    monkeypatch.setattr(_r(), 'zrem', lambda key, *m: 0 if key == build_line.LINE_KEY else real_zrem(key, *m))
    with pytest.raises(build_line.TicketLost):
        _build(mine.ticket)
    assert _r().zcard(build_line.HOLDERS_KEY) == 0


def test_a_redis_failure_after_taking_a_slot_does_not_leak_it(monkeypatch):
    _fill_slots()
    mine = _wait()
    _free_slots(2)
    real_zrem = _r().zrem

    def flaky(key, *members):
        if key == build_line.LINE_KEY:
            raise ConnectionError('blip')
        return real_zrem(key, *members)
    monkeypatch.setattr(_r(), 'zrem', flaky)
    ran = []
    with build_line.slot(mine.ticket, user_id=USER):   # fails open: the build still runs
        ran.append(True)
    assert ran == [True]
    assert _r().zcard(build_line.HOLDERS_KEY) == 0     # but its lease is not left holding a slot for 120s


def test_a_redis_failure_ranking_a_new_lease_does_not_leak_it(monkeypatch):
    real_zrank = _r().zrank

    def flaky(key, member):
        if key == build_line.HOLDERS_KEY:
            raise ConnectionError('blip')
        return real_zrank(key, member)
    monkeypatch.setattr(_r(), 'zrank', flaky)
    _build()
    assert _r().zcard(build_line.HOLDERS_KEY) == 0


def test_has_room_tells_a_caller_that_will_not_wait_whether_it_could_build():
    assert build_line.has_room()
    _fill_slots()
    assert not build_line.has_room()
    _wait()
    _free_slots(1)
    assert not build_line.has_room()      # the slot that freed is the waiting hunter's
    _free_slots(1)
    assert build_line.has_room()


def test_has_room_fails_open(monkeypatch):
    def down(*args, **kwargs):
        raise ConnectionError('redis is down')
    monkeypatch.setattr(_r(), 'zremrangebyscore', down)
    assert build_line.has_room()


@pytest.mark.parametrize('ticket', ['', None, 'x' * 65, 123, 'never-issued'])
def test_only_issued_tickets_are_valid(ticket):
    assert build_line.valid_ticket(ticket, USER) is False


def test_not_joining_raises_busy_and_leaves_no_ghost_ticket():
    _fill_slots()
    with pytest.raises(build_line.LineBusy):
        with build_line.slot(user_id=USER, join=False):
            pass
    assert _r().zcard(build_line.LINE_KEY) == 0


def test_leaving_takes_a_ticket_out_of_line():
    _fill_slots()
    first, second = _wait(), _wait(user=OTHER)
    build_line.leave(first.ticket, USER)
    assert _wait(second.ticket, user=OTHER).position == 1


def test_nobody_can_take_someone_elses_ticket_out_of_line():
    _fill_slots()
    first = _wait()
    build_line.leave(first.ticket, OTHER)
    assert build_line.valid_ticket(first.ticket, USER)


def test_a_ticket_pruned_mid_check_is_lost_never_the_front_of_the_line(monkeypatch):
    _fill_slots()
    first, second = _wait(), _wait(user=OTHER)
    real_zrank = _r().zrank
    monkeypatch.setattr(_r(), 'zrank', lambda key, member: None if member == second.ticket else real_zrank(key, member))
    _free_slots(1)
    with pytest.raises(build_line.TicketLost):
        with build_line.slot(second.ticket, user_id=OTHER):
            pytest.fail('a ticket that vanished must not be treated as the front of the line')


def test_the_estimate_follows_recent_builds():
    _r().set(build_line.AVG_KEY, '2.0')
    _fill_slots()
    first = _wait()
    assert first.eta_seconds == 4   # nobody ahead: one round of the current builds, then theirs
    for i in range(build_line.SLOTS * 2):
        _wait(user=100 + i)
    assert _wait(user=999).eta_seconds > first.eta_seconds


def test_only_successful_builds_feed_the_average():
    _r().set(build_line.AVG_KEY, '3.0')
    with pytest.raises(ValueError):
        with build_line.slot(user_id=USER):
            raise ValueError('quick failure')
    assert float(_r().get(build_line.AVG_KEY)) == 3.0
    _build()
    assert float(_r().get(build_line.AVG_KEY)) < 3.0


def test_a_broken_redis_never_breaks_a_build(monkeypatch):
    def down(*args, **kwargs):
        raise ConnectionError('redis is down')
    monkeypatch.setattr(_r(), 'zremrangebyscore', down)
    ran = []
    with build_line.slot(user_id=USER):
        ran.append(True)
    assert ran == [True]
