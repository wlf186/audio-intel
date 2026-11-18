"""Shared pure TTS text chunking; no inference/runtime imports."""
import re


def split_text(text: str, limit: int = 300) -> list[str]:
    sentences = [item.strip() for item in re.split(r"(?<=[。！？!?；;.!])\s*", text) if item.strip()]
    chunks: list[str] = []
    current = ""
    for sentence in sentences or [text]:
        if current and len(current) + len(sentence) > limit:
            chunks.append(current); current = ""
        while len(sentence) > limit:
            chunks.append(sentence[:limit]); sentence = sentence[limit:]
        current += sentence
    if current:
        chunks.append(current)
    return chunks
