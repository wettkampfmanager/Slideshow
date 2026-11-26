from datetime import date, datetime

import pytest

from slideshow.schedule import Rule, day_matches, describe, is_active, upcoming


def rule(**kw):
    row = dict(kind="daily", start_time="00:00", end_time="23:59", weekdays="", nths="",
               monthday=None, on_date=None, valid_from=None, valid_until=None)
    row.update(kw)
    return Rule.from_row(row)


# Beispiele aus der Anforderung ------------------------------------------------

def test_daily_lunch_window():
    r = rule(start_time="12:00", end_time="13:45")
    assert not is_active(r, datetime(2026, 11, 26, 11, 59))
    assert is_active(r, datetime(2026, 11, 26, 12, 0))
    assert is_active(r, datetime(2026, 11, 26, 13, 45, 59))  # Endminute zählt mit
    assert not is_active(r, datetime(2026, 11, 26, 13, 46))


def test_every_monday_all_day():
    r = rule(kind="weekly", weekdays="0")
    assert is_active(r, datetime(2026, 11, 30, 0, 0))      # Montag
    assert is_active(r, datetime(2026, 11, 30, 23, 59, 59))
    assert not is_active(r, datetime(2026, 12, 1, 0, 0))   # Dienstag


def test_first_monday_of_month():
    r = rule(kind="monthly_nth", weekdays="0", nths="1", start_time="10:00", end_time="15:00")
    assert is_active(r, datetime(2026, 12, 7, 10, 0))      # 1. Montag im Dezember 2026
    assert not is_active(r, datetime(2026, 12, 14, 10, 0)) # 2. Montag
    assert not is_active(r, datetime(2026, 12, 7, 15, 1))
    assert [d.date() for d in upcoming(r, datetime(2026, 11, 26), count=3)] == [
        date(2026, 12, 7), date(2027, 1, 4), date(2027, 2, 1)]


# Weitere Fälle ---------------------------------------------------------------

def test_last_friday():
    r = rule(kind="monthly_nth", weekdays="4", nths="-1")
    assert day_matches(r, date(2026, 11, 27))
    assert not day_matches(r, date(2026, 11, 20))


def test_monthday_clamps_to_month_end():
    r = rule(kind="monthly_day", monthday=31)
    assert day_matches(r, date(2027, 2, 28))
    assert day_matches(r, date(2026, 12, 31))
    assert not day_matches(r, date(2026, 12, 30))


def test_overnight_window_belongs_to_start_day():
    r = rule(kind="weekly", weekdays="4", start_time="22:00", end_time="02:00")  # Fr-Nacht
    assert is_active(r, datetime(2026, 11, 27, 23, 0))   # Fr
    assert is_active(r, datetime(2026, 11, 28, 1, 30))   # Sa früh
    assert not is_active(r, datetime(2026, 11, 28, 23, 0))
    assert not is_active(r, datetime(2026, 11, 27, 1, 30))  # Fr früh -> Do-Nacht, nicht gewählt


def test_validity_range_and_once():
    r = rule(valid_from="2026-12-01", valid_until="2026-12-24")
    assert not day_matches(r, date(2026, 11, 30))
    assert day_matches(r, date(2026, 12, 24))
    assert not day_matches(r, date(2026, 12, 25))
    once = rule(kind="once", on_date="2026-12-31")
    assert day_matches(once, date(2026, 12, 31)) and not day_matches(once, date(2027, 12, 31))


@pytest.mark.parametrize("kw", [
    dict(kind="weekly"),
    dict(kind="monthly_nth", weekdays="0"),
    dict(kind="monthly_day", monthday=32),
    dict(kind="once"),
    dict(valid_from="2026-12-02", valid_until="2026-12-01"),
])
def test_invalid_rules(kw):
    with pytest.raises(ValueError):
        rule(**kw)


def test_describe():
    assert describe(rule(start_time="12:00", end_time="13:45")) == "Täglich, 12:00–13:45"
    assert describe(rule(kind="weekly", weekdays="0")) == "Jeden Montag, ganztägig"
    assert describe(rule(kind="monthly_nth", weekdays="0", nths="1", start_time="10:00", end_time="15:00")) \
        == "Jeden 1. Montag im Monat, 10:00–15:00"
    assert describe(rule(kind="monthly_nth", weekdays="4", nths="1,-1")).startswith("Jeden 1. und letzten Freitag")


def test_active_segments():
    from slideshow.schedule import active_segments
    assert active_segments(rule(start_time="12:00", end_time="13:45"), date(2026, 11, 26)) == [(720, 825)]
    assert active_segments(rule(), date(2026, 11, 26)) == [(0, 1439)]
    night = rule(kind="weekly", weekdays="4", start_time="22:00", end_time="02:00")  # Fr-Nacht
    assert active_segments(night, date(2026, 11, 27)) == [(1320, 1439)]  # Freitag
    assert active_segments(night, date(2026, 11, 28)) == [(0, 120)]      # Samstag früh
    assert active_segments(rule(kind="weekly", weekdays="0"), date(2026, 11, 26)) == []


def test_second_to_last_monday():
    r = rule(kind="monthly_nth", weekdays="0", nths="-2")
    # November 2026: Montage 2., 9., 16., 23., 30.
    assert day_matches(r, date(2026, 11, 23))
    assert not day_matches(r, date(2026, 11, 30)) and not day_matches(r, date(2026, 11, 16))
    # Dezember 2026: Montage 7., 14., 21., 28.
    assert day_matches(r, date(2026, 12, 21))
    assert describe(rule(kind="monthly_nth", weekdays="0", nths="1,-2,-1")).startswith(
        "Jeden 1., vorletzten und letzten Montag im Monat")


def test_period_over_several_days():
    from slideshow.schedule import active_segments
    r = rule(kind="period", on_date="2026-12-23", start_time="14:00", end_date="2027-01-02", end_time="08:00")
    assert not is_active(r, datetime(2026, 12, 23, 13, 59))
    assert is_active(r, datetime(2026, 12, 23, 14, 0))
    assert is_active(r, datetime(2026, 12, 31, 3, 0))
    assert is_active(r, datetime(2027, 1, 2, 8, 0, 59))
    assert not is_active(r, datetime(2027, 1, 2, 8, 1))
    assert active_segments(r, date(2026, 12, 23)) == [(840, 1439)]
    assert active_segments(r, date(2026, 12, 28)) == [(0, 1439)]
    assert active_segments(r, date(2027, 1, 2)) == [(0, 480)]
    assert upcoming(r, datetime(2026, 11, 26)) == [datetime(2026, 12, 23, 14, 0)]
    assert upcoming(r, datetime(2027, 1, 3)) == []
    assert describe(r) == "Vom 23.12.2026 14:00 bis 02.01.2027 08:00"
    with pytest.raises(ValueError):
        rule(kind="period", on_date="2027-01-02", start_time="08:00", end_date="2026-12-23", end_time="14:00")


def test_find_change_across_days():
    from slideshow.schedule import find_change
    lunch = rule(start_time="12:00", end_time="13:45")
    monday = rule(kind="weekly", weekdays="0")                     # ganztägig montags
    first_mon = rule(kind="monthly_nth", weekdays="0", nths="1", start_time="10:00", end_time="15:00")
    rules = [lunch, monday, first_mon]
    # Do 26.11. 15:00 -> nächster Wechsel Fr 12:00, vorheriger Do 13:46
    assert find_change(rules, datetime(2026, 11, 26, 15, 0)) == datetime(2026, 11, 27, 12, 0)
    assert find_change(rules, datetime(2026, 11, 26, 15, 0), -1) == datetime(2026, 11, 26, 13, 46)
    # Sonntag 29.11. 14:00 -> Montag 00:00 (Montags beginnt)
    assert find_change(rules, datetime(2026, 11, 29, 14, 0)) == datetime(2026, 11, 30, 0, 0)
    # Montag 30.11. 23:00 -> Dienstag 00:00 (Montags endet), rückwärts -> Montag 13:46
    assert find_change(rules, datetime(2026, 11, 30, 23, 0)) == datetime(2026, 12, 1, 0, 0)
    assert find_change(rules, datetime(2026, 11, 30, 23, 0), -1) == datetime(2026, 11, 30, 13, 46)
    # Nur "erster Montag": vom 8.12. aus nächster Wechsel am 4.1.2027 10:00
    assert find_change([first_mon], datetime(2026, 12, 8)) == datetime(2027, 1, 4, 10, 0)
    assert find_change([first_mon], datetime(2026, 12, 8), -1) == datetime(2026, 12, 7, 15, 1)
    # Zeitspanne über mehrere Tage und gar kein Wechsel
    xmas = rule(kind="period", on_date="2026-12-23", start_time="14:00", end_date="2027-01-02", end_time="08:00")
    assert find_change([xmas], datetime(2026, 12, 25)) == datetime(2027, 1, 2, 8, 1)
    assert find_change([xmas], datetime(2027, 1, 5), -1) == datetime(2027, 1, 2, 8, 1)
    assert find_change([rule()], datetime(2026, 11, 26)) is None
    assert find_change([], datetime(2026, 11, 26)) is None
