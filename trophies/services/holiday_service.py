"""
Holiday computation for the Platinum Calendar Challenge.

The calendar is a perpetual 365-day grid. Holiday highlights are purely
cosmetic. The grid layout uses the current year for weekday alignment,
so holiday dates are also computed for the current year.

Feb 29 is always excluded from the calendar (even in leap years).
"""
import calendar as cal_module
from datetime import date, timedelta

from django.utils import timezone


# Fixed International Holidays (same date every year)
INTL_FIXED = {
    '1-1':   {'name': "New Year's Day",              'color': '#fbbf24'},
    '2-14':  {'name': "Valentine's Day",             'color': '#f472b6'},
    '3-8':   {'name': "International Women's Day",   'color': '#a78bfa'},
    '4-22':  {'name': 'Earth Day',                    'color': '#34d399'},
    '5-1':   {'name': "International Workers' Day",  'color': '#f87171'},
    '10-31': {'name': 'Halloween',                    'color': '#fb923c'},
    '12-24': {'name': 'Christmas Eve',                'color': '#ef4444'},
    '12-25': {'name': 'Christmas Day',                'color': '#22c55e'},
    '12-31': {'name': "New Year's Eve",              'color': '#fbbf24'},
}

# Fixed US Holidays (same date every year)
US_FIXED = {
    '7-4':   {'name': 'Independence Day', 'color': '#ef4444'},
    '11-11': {'name': 'Veterans Day',     'color': '#d97706'},
}

# Ramadan start dates (Umm al-Qura calendar, approximate Gregorian dates).
# May vary +/-1 day by region due to moon sighting.
RAMADAN_STARTS = {
    2020: date(2020, 4, 23),
    2021: date(2021, 4, 13),
    2022: date(2022, 4, 2),
    2023: date(2023, 3, 23),
    2024: date(2024, 3, 11),
    2025: date(2025, 3, 1),
    2026: date(2026, 2, 18),
    2027: date(2027, 2, 8),
    2028: date(2028, 1, 28),
    2029: date(2029, 1, 16),
    2030: date(2030, 1, 6),
    2031: date(2031, 12, 15),
    2032: date(2032, 12, 4),
    2033: date(2033, 11, 23),
    2034: date(2034, 11, 12),
    2035: date(2035, 11, 2),
}

# Eid al-Fitr is approximately 30 days after Ramadan begins
EID_OFFSET_DAYS = 30

# US floating holiday rules: (name, color, month, weekday, n)
# n is 0-based index into occurrences of that weekday in the month.
# Use -1 for "last occurrence".
_US_FLOATING_RULES = [
    ("Martin Luther King Jr. Day", '#2dd4bf', 1,  cal_module.MONDAY,    2),   # 3rd Mon Jan
    ("Presidents' Day",            '#60a5fa', 2,  cal_module.MONDAY,    2),   # 3rd Mon Feb
    ('Memorial Day',               '#818cf8', 5,  cal_module.MONDAY,   -1),   # Last Mon May
    ('Labor Day',                  '#38bdf8', 9,  cal_module.MONDAY,    0),   # 1st Mon Sep
    ('Thanksgiving',               '#f59e0b', 11, cal_module.THURSDAY,  3),   # 4th Thu Nov
]


def _nth_weekday(year, month, weekday, n):
    """
    Return the day-of-month for the nth occurrence of weekday in (year, month).
    weekday: calendar.MONDAY (0) through calendar.SUNDAY (6).
    n: 0-based index into occurrences. Use -1 for last occurrence.
    """
    month_cal = cal_module.monthcalendar(year, month)
    days = [week[weekday] for week in month_cal if week[weekday] != 0]
    return days[n]


def get_us_floating_holidays(year):
    """
    Compute floating US federal holidays for a given year.
    Returns dict of 'M-D' -> {'name': str, 'color': str}.
    """
    holidays = {}
    for name, color, month, weekday, n in _US_FLOATING_RULES:
        day = _nth_weekday(year, month, weekday, n)
        key = f'{month}-{day}'
        if key != '2-29':
            holidays[key] = {'name': name, 'color': color}
    return holidays


def get_ramadan_holidays(year):
    """
    Return Ramadan bookend entries for a given year.
    Returns dict of 'M-D' -> {'name': str, 'color': str}.
    """
    holidays = {}
    start = RAMADAN_STARTS.get(year)
    if start:
        eid = start + timedelta(days=EID_OFFSET_DAYS)
        start_key = f'{start.month}-{start.day}'
        eid_key = f'{eid.month}-{eid.day}'
        if start_key != '2-29':
            holidays[start_key] = {'name': 'Ramadan Begins', 'color': '#2dd4bf'}
        if eid_key != '2-29':
            holidays[eid_key] = {'name': 'Eid al-Fitr', 'color': '#fbbf24'}
    return holidays


def get_all_holidays(year=None, include_us=False):
    """
    Build the complete holiday dict for a given year.
    Returns dict of 'M-D' -> {'name': str, 'color': str}.
    International holidays take priority over US if dates collide.
    """
    if year is None:
        year = timezone.now().year

    holidays = {}

    # US holidays first (lowest priority, overridden by international)
    if include_us:
        holidays.update(US_FIXED)
        holidays.update(get_us_floating_holidays(year))

    # International holidays override US on collision
    holidays.update(INTL_FIXED)
    holidays.update(get_ramadan_holidays(year))

    return holidays


def get_holidays_for_js(year=None):
    """
    Return holiday data structured for the JS detail page.
    Returns (intl_holidays, us_holidays) where each is a dict of
    'M-D' -> {'name': str, 'color': str}.
    """
    if year is None:
        year = timezone.now().year

    intl = dict(INTL_FIXED)
    intl.update(get_ramadan_holidays(year))

    us = dict(US_FIXED)
    us.update(get_us_floating_holidays(year))

    return intl, us


def get_holidays_color_map(year=None, include_us=False):
    """
    Return a simple 'M-D' -> color_hex dict for share card template rendering.
    """
    all_h = get_all_holidays(year=year, include_us=include_us)
    return {key: h['color'] for key, h in all_h.items()}
