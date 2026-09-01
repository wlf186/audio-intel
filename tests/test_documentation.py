from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parent.parent
PUBLIC_DOCS = (
    ROOT / "README.md",
    ROOT / "README_CN.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "docs" / "API.md",
    ROOT / "docs" / "ARCHITECTURE.md",
    ROOT / "docs" / "HTTPS.md",
    ROOT / "docs" / "INSTALL.md",
    ROOT / "docs" / "WINDOWS.md",
    ROOT / "docs" / "TROUBLESHOOTING.md",
    ROOT / "docs" / "UPGRADE.md",
    ROOT / "docs" / "DEPENDENCIES.md",
)

MARKDOWN_TARGET = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
HTML_TARGET = re.compile(r"(?:href|src)=\"([^\"]+)\"")


def _relative_targets(path: Path) -> list[Path]:
    text = path.read_text(encoding="utf-8")
    targets = [*MARKDOWN_TARGET.findall(text), *HTML_TARGET.findall(text)]
    resolved: list[Path] = []
    for raw_target in targets:
        target = raw_target.strip().split("#", 1)[0].split("?", 1)[0]
        if not target or target.startswith(("#", "/", "http://", "https://", "mailto:")):
            continue
        resolved.append((path.parent / unquote(target)).resolve())
    return resolved


def test_public_document_relative_links_exist() -> None:
    missing = [
        f"{document.relative_to(ROOT)} -> {target.relative_to(ROOT) if target.is_relative_to(ROOT) else target}"
        for document in PUBLIC_DOCS
        for target in _relative_targets(document)
        if not target.exists()
    ]
    assert not missing, "Missing documentation targets:\n" + "\n".join(missing)


def test_readmes_keep_parallel_information_architecture() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README_CN.md").read_text(encoding="utf-8")
    section_pairs = (
        ("What it does", "主要能力"),
        ("Quick Start", "快速开始"),
        ("Compatibility and hardware", "兼容性与硬件"),
        ("How it works", "工作原理"),
        ("API and integrations", "API 与集成"),
        ("Documentation", "文档"),
        ("Security and data ownership", "安全与数据归属"),
        ("Support and contributing", "支持与贡献"),
        ("License", "许可证"),
    )
    english_positions = [english.index(f"## {heading}") for heading, _ in section_pairs]
    chinese_positions = [chinese.index(f"## {heading}") for _, heading in section_pairs]
    assert english_positions == sorted(english_positions)
    assert chinese_positions == sorted(chinese_positions)
    assert "README_CN.md" in english
    assert "README.md" in chinese


def test_readme_assets_are_bounded_and_reproducible() -> None:
    asset_dir = ROOT / "docs" / "assets" / "readme"
    expected = {
        "asr-workspace.webp": 500_000,
        "tts-workspace.webp": 500_000,
        "job-history.webp": 500_000,
        "social-preview.png": 1_000_000,
    }
    for name, maximum_bytes in expected.items():
        path = asset_dir / name
        assert path.is_file()
        assert path.stat().st_size < maximum_bytes
    assert (ROOT / "scripts" / "capture_readme_assets.mjs").is_file()


def test_docs_do_not_link_to_removed_chinese_readme_anchor() -> None:
    stale = "README.md#局域网-https-与浏览器录音"
    assert all(stale not in path.read_text(encoding="utf-8") for path in PUBLIC_DOCS)
