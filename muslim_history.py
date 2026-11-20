"""Daily, nonrepeating Muslim-world history facts from before 2005.

The adjacent CSV owns the content. Each display date has one assigned fact.
Exact anniversaries, month matches, and birthday-month spotlights are labeled
separately. A spotlight does not claim the achievement occurred that month.
Historical birth anniversaries follow the dates listed in Wikipedia; they are
not reconstructed Hijri dates or automatically converted Julian dates.
"""

import csv
from datetime import date
from pathlib import Path


def _load_events() -> list[dict]:
    with Path(__file__).with_name('muslim_history_daily.csv').open(
        newline='', encoding='utf-8'
    ) as handle:
        events = list(csv.DictReader(handle))
    for event in events:
        for key in ('month', 'day', 'word_count', 'year_start', 'year_end'):
            event[key] = int(event[key])
    return events


EVENTS = _load_events()


def get_muslim_history(month: int, day: int) -> dict[str, str] | None:
    """Return fact text and Wikipedia URL for a Gregorian month and day.

    Accepts integer month/day values, including February 29. Invalid dates
    raise ValueError; a missing or ineligible CSV row returns None.
    No internet connection is required.
    """
    if type(month) is not int or type(day) is not int:
        raise ValueError('month and day must be integers')
    date(2000, month, day)  # Validate using a leap year so February 29 is valid.
    for event in EVENTS:
        if (event['month'], event['day']) == (month, day):
            if event['year_end'] >= 2005:
                return None
            return {
                'text': event['text'],
                'wikipedia_url': event['wikipedia_url'],
            }
    return None


if __name__ == '__main__':
    import json
    print(json.dumps(get_muslim_history(month=11, day=8), indent=2))
