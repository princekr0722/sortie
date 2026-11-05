from datetime import date

from sortie.durations import Duration


def test_months_counts_whole_months_only():
    assert Duration.since(date(2023, 9, 1), date(2026, 11, 4)).months == 38
    assert Duration.since(date(2023, 9, 15), date(2026, 11, 14)).months == 37
    assert Duration.since(date(2026, 12, 1), date(2026, 11, 4)).months == 0


def test_formats():
    d = Duration(38)
    assert (d.years, d.remaining_months) == (3, 2)
    assert d.decimal_years == 3.1
    assert d.as_text() == "3 years 2 months"
    assert Duration(12).as_text() == "1 year"
    assert Duration(0).as_text() == "0 months"


def test_profile_durations_from_dates(profile):
    today = date(2026, 11, 4)
    assert profile.total_experience(today).years == 3
    assert profile.skill_experience("Spring Boot", today).years == 3
    assert profile.skill_experience("springboot", today).years == 3
    assert profile.skill_experience("Golang", today).years == 1
    assert profile.skill_experience("TypeScript", today).years == 1
    assert profile.skill_experience("Python", today).months == 0
    assert profile.skill_experience("Rust", today) is None
    # grows without editing the file
    assert profile.total_experience(date(2027, 9, 1)).years == 4
