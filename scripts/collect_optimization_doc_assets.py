"""Copy screenshots from the documented synthetic Playwright regression run."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'logs/playwright'
TARGET = ROOT / 'docs/assets/optimization'


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    for width in (1440, 390):
        comparison = SOURCE / f'optimization-workflow-scene-scoped-unified-reading-{width}/comparison.png'
        enrollment = list(SOURCE.glob(f'video-enrollment-*automatic-scenes-{width}-zh-CN/sentence-selection.png'))
        if not comparison.is_file() or len(enrollment) != 1:
            raise SystemExit('Run optimization-workflow.spec.ts and video-enrollment.spec.ts together first')
        shutil.copyfile(comparison, TARGET / f'comparison-{width}.png')
        shutil.copyfile(enrollment[0], TARGET / f'enrollment-{width}.png')


if __name__ == '__main__':
    main()
