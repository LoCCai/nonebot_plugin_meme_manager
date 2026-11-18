"""Local, model-free category selection for host chat plugins."""
from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from .lexicon import load_for_packs, render_hint_lines
from .platform.bridge import to_unified_event
from .state import get_runtime_or_none

IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
MAX_IMAGE_BYTES = 10 * 1024 * 1024


def _context(bot: Any, event: Any):
    runtime = get_runtime_or_none()
    if runtime is None:
        return None, {}
    unified = to_unified_event(bot, event) if event is not None else None
    if event is not None and unified is None:
        return None, {}
    return runtime, runtime._resolve_runtime_pack_context(event=unified)


def _images(context: dict, category: str) -> list[Path]:
    if not category or category in {'.', '..'} or any(c in category for c in '/\\[]\r\n'):
        return []
    images = []
    for pack_dir in context.get('pack_dirs', {}).values():
        root = (Path(pack_dir) / 'memes').resolve()
        directory = root / category
        if not directory.is_dir() or directory.is_symlink():
            continue
        for path in directory.iterdir():
            if path.suffix.lower() not in IMAGE_SUFFIXES or path.is_symlink():
                continue
            try:
                path.resolve().relative_to(root)
                if path.is_file() and 0 < path.stat().st_size <= MAX_IMAGE_BYTES:
                    images.append(path)
            except (OSError, ValueError):
                continue
    return images


def category_catalog(*, bot=None, event=None) -> dict:
    """Return only populated categories from this session's enabled packs.

    No image recognition, embeddings, model calls or sends occur here.
    """
    runtime, context = _context(bot, event)
    if runtime is None:
        return {'categories': {}, 'hints': ''}
    mapping = {name: str(description)[:200] for name, description in
               context.get('category_mapping', {}).items() if _images(context, name)}
    hints = ''
    if runtime._read_bool(('generation', 'prompt', 'lexicon_enabled'), True):
        hints = render_hint_lines(mapping, load_for_packs(context.get('pack_ids', []), runtime.data_dir))
    return {'categories': mapping, 'hints': hints}


def pick_category_image(category: str, *, bot=None, event=None) -> Path | None:
    """Recheck current bindings before selecting a local file; never send it."""
    runtime, context = _context(bot, event)
    if runtime is None or category not in context.get('category_mapping', {}):
        return None
    images = _images(context, category)
    return random.choice(images) if images else None
