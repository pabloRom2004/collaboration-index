"""Deal seeded private reusable character hands using Multi-Agent-Bench's rules."""

import random
from pathlib import Path

ALPHABET = set("abcdefghijklmnopqrstuvwxyz ,.\n")


def deal(
    sentences_file: str,
    agents: int,
    seed: int,
    copies: int,
    minimum_hand: int,
    candidate_count: int | None = None,
) -> dict[str, object]:
    """Choose common candidate sentences and deal complementary private character sets."""
    pool = [
        line
        for line in Path(sentences_file).read_text().splitlines()
        if line and not line.startswith("#")
    ]
    if (
        copies < 1
        or minimum_hand < 1
        or any(set(s) - (ALPHABET - {"\n"}) for s in pool)
    ):
        raise ValueError("Invalid spelling deck or sentence pool")
    shown_count = min(2 * agents, 100) if candidate_count is None else candidate_count
    if type(shown_count) is not int or shown_count < 1:
        raise ValueError("candidate_count must be positive or null")
    if len(set(pool)) < shown_count:
        raise ValueError(
            "The sentence pool has fewer distinct sentences than candidate_count"
        )
    rng = random.Random(seed)
    shown = rng.sample(sorted(set(pool)), shown_count)
    target = rng.choice(shown)
    cards = [char for char in sorted(set(target) | {"\n"}) for _ in range(copies)]
    rng.shuffle(cards)
    hands = [set(cards[index::agents]) for index in range(agents)]
    for hand in hands:
        while len(hand) < min(minimum_hand, len(set(cards))):
            hand.add(rng.choice(sorted(set(cards) - hand)))
    return {
        "sentences": shown,
        "dealt_sentence": target,
        "hands": {f"agent_{i}": sorted(hand) for i, hand in enumerate(hands)},
    }
