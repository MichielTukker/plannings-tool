import pytest


@pytest.fixture
def definition():
    return {
        "schema_version": 1,
        "course": {"title": "Training", "start": "09:00", "end": "17:00"},
        "trainers": [
            {"id": "alex", "name": "Alex Example", "email": "alex@example.org"},
            {"id": "sam", "name": "Sam Example", "email": "sam@example.org"},
        ],
        "days": [
            {
                "label": "Day 1",
                "schedule": [
                    {
                        "title": "Introduction",
                        "type": "Presentation",
                        "duration": "30m",
                        "trainers": ["alex"],
                    }
                ],
            }
        ],
    }
