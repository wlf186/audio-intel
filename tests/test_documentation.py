from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parent.parent
PUBLIC_DOCS = (
    ROOT / "README.md",
    ROOT / "README_CN.md",
    ROOT / "AGENTS.md",
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
    for locale in ("en-US", "zh-CN"):
        for name in ("asr-workspace.webp", "tts-workspace.webp", "job-history.webp"):
            path = asset_dir / locale / name
            assert path.is_file()
            assert path.stat().st_size < 500_000
    assert (asset_dir / "social-preview.png").stat().st_size < 1_000_000
    for stale_name in ("asr-workspace.webp", "tts-workspace.webp", "job-history.webp"):
        assert not (asset_dir / stale_name).exists()

    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README_CN.md").read_text(encoding="utf-8")
    assert english.count("docs/assets/readme/en-US/") == 3
    assert "docs/assets/readme/zh-CN/" not in english
    assert chinese.count("docs/assets/readme/zh-CN/") == 3
    assert "docs/assets/readme/en-US/" not in chinese

    capture_script = (ROOT / "scripts" / "capture_readme_assets.mjs").read_text(encoding="utf-8")
    assert "audio-intel:ui-locale:v1" in capture_script
    assert "locale:'zh-CN'" in capture_script
    assert "locale:'en-US'" in capture_script


def test_docs_do_not_link_to_removed_chinese_readme_anchor() -> None:
    stale = "README.md#局域网-https-与浏览器录音"
    assert all(stale not in path.read_text(encoding="utf-8") for path in PUBLIC_DOCS)


def test_deployment_docs_keep_full_and_cpu_profile_contracts() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README_CN.md").read_text(encoding="utf-8")
    windows = (ROOT / "docs" / "WINDOWS.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    dependencies = (ROOT / "docs" / "DEPENDENCIES.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "recommended **full** profile" in english
    assert "默认且推荐的 **full 全量配置**" in chinese
    for document in (english, chinese, windows):
        assert r".\service.cmd setup all --profile cpu" in document
    assert r"\.\service.cmd setup all --profile cpu" not in windows
    assert "models plus project runtimes" in english
    assert "下载/安装缓存和任务数据另计" in chinese

    assert "default to GPU at submission time in the recommended full deployment" in architecture
    assert "default to CPU in the CPU-only deployment" in architecture
    assert "503 gpu_runtime_not_installed" in architecture
    assert ".runtime/deployment-profile" in architecture
    assert "complete executor process tree to exit" in architecture
    assert "2.11.0+cu130（full）" in dependencies
    assert "2.11.0+cpu（CPU-only）" in dependencies
    assert "SQLite schema v9 data" in agents


def test_api_markdown_examples_use_public_contract_values() -> None:
    api_guide = (ROOT / "docs" / "API.md").read_text(encoding="utf-8")

    assert "voice_mode=inline_clone" in api_guide
    assert "voice_mode=clone" not in api_guide
    assert "one comma-separated `hotword_list_ids` form field" in api_guide
    assert "`gpu_runtime_not_installed` problem code" in api_guide


def test_contributing_docs_require_translation_parity() -> None:
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")

    assert "frontend/src/i18n/locales/zh-CN.json" in contributing
    assert "en-US.json" in contributing
    assert "--dir frontend check:i18n" in contributing
    assert "every platform/profile lock together" in contributing
