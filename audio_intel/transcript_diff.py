"""Bounded literal differences shared by document renderers, after redaction."""
from difflib import SequenceMatcher


def text_changes(original: str, edited: str) -> tuple[list[tuple[str, bool]], list[tuple[str, bool]]]:
    if len(original) * len(edited) > 2_000_000:
        return [(original, original != edited)], [(edited, original != edited)]
    left, right = [], []
    for kind, a, b, c, d in SequenceMatcher(None, original, edited, autojunk=False).get_opcodes():
        if a != b:
            left.append((original[a:b], kind != 'equal'))
        if c != d:
            right.append((edited[c:d], kind != 'equal'))
    return left, right
