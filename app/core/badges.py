"""Milestone badges. Earned from real numbers, never self-awarded."""

from typing import List

# (needed, label, what it means), best first. A writer shows the best one they
# have in each kind.
_TIERS = {
    "stories": [
        (25, "पच्चीस रचनाएँ", "25 रचनाएँ प्रकाशित कीं"),
        (10, "दस रचनाएँ", "10 रचनाएँ प्रकाशित कीं"),
        (5, "पाँच रचनाएँ", "5 रचनाएँ प्रकाशित कीं"),
        (1, "पहली रचना", "पहली रचना प्रकाशित की"),
    ],
    "claps": [
        (5000, "5000 तालियाँ", "पाठकों ने 5000 तालियाँ दीं"),
        (500, "500 तालियाँ", "पाठकों ने 500 तालियाँ दीं"),
        (50, "50 तालियाँ", "पाठकों ने 50 तालियाँ दीं"),
    ],
    "followers": [
        (1000, "1000 फ़ॉलोअर्स", "1000 पाठक फ़ॉलो करते हैं"),
        (100, "100 फ़ॉलोअर्स", "100 पाठक फ़ॉलो करते हैं"),
        (10, "10 फ़ॉलोअर्स", "10 पाठक फ़ॉलो करते हैं"),
    ],
}


def earned_badges(stories: int, claps: int, followers: int) -> List[dict]:
    totals = {"stories": stories, "claps": claps, "followers": followers}
    badges = []
    for kind, tiers in _TIERS.items():
        for needed, label, hint in tiers:
            if totals[kind] >= needed:
                badges.append({"key": f"{kind}-{needed}", "kind": kind, "label": label, "hint": hint})
                break
    return badges
