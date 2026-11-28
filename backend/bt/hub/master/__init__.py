"""Master curator: judges, folds and ranks every hub item per profile, so feeds show only the best.

Stages stay separate (todo.md §26): rules decide what can never be shown or is plainly weak,
cluster folds copies of the same story, score ranks what is left and tiers it under daily
quotas. Everything here is deterministic; model judging (P2) only refines the uncertain band.
"""

import threading

# One pass at a time, shared with memes.sweep(): both create MemeExample and StoryItem rows
# after checking they are missing, so overlapping writers would collide on the primary key.
LOCK = threading.RLock()

# Bump when rules or scoring change, so stored verdicts are recomputed by the next pass.
PASS_VERSION = 3

# Meme judging uses entertainment criteria rather than article-editor criteria.
# Keep other content's stored decisions valid when only the meme rubric changes.
MEME_PASS_VERSION = 4


def pass_version(content_type: str) -> int:
    return MEME_PASS_VERSION if content_type == 'meme' else PASS_VERSION
