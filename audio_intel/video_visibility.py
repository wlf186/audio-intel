"""Export-only frame visibility; original pictures and transcripts remain intact."""
from __future__ import annotations


def retained_hidden(previous: dict, scenes: list[dict]) -> list[str]:
    """Never transfer a marker solely because a newly detected ID is reused."""
    hidden = set(previous.get('export_hidden_scene_ids') or [])
    def identity(scene: dict) -> tuple:
        return tuple(scene[key] for key in ('start', 'end', 'frame_time', 'image'))
    identities = {identity(scene) for scene in previous['scenes'] if scene['id'] in hidden}
    return [scene['id'] for scene in scenes if identity(scene) in identities]
