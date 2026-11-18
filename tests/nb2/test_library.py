from types import SimpleNamespace

import pytest

from repo.nb2 import library


@pytest.fixture
def pack(tmp_path, monkeypatch):
    directory = tmp_path / 'pack' / 'memes' / 'happy'
    directory.mkdir(parents=True)
    (directory / 'a.png').write_bytes(b'local-image')
    context = {'pack_ids': ['official-001'], 'pack_dirs': {'official-001': directory.parent.parent},
               'category_mapping': {'happy': '开心', 'empty': '空分类'}}
    runtime = SimpleNamespace(data_dir=tmp_path, _read_bool=lambda *a: True,
                              _resolve_runtime_pack_context=lambda **kw: context)
    monkeypatch.setattr(library, 'get_runtime_or_none', lambda: runtime)
    return runtime, context, directory


def test_catalog_uses_local_lexicon_and_only_populated_categories(pack):
    catalog = library.category_catalog()
    assert catalog['categories'] == {'happy': '开心'}
    assert 'happy' in catalog['hints']
    assert library.pick_category_image('happy') == pack[2] / 'a.png'
    assert library.pick_category_image('unknown') is None


def test_lexicon_can_be_disabled(pack):
    pack[0]._read_bool = lambda *a: False
    assert library.category_catalog()['hints'] == ''


def test_selected_packs_combine_images_and_removal_takes_effect_immediately(pack, tmp_path, monkeypatch):
    _, context, first = pack
    second = tmp_path / 'second' / 'memes' / 'happy'
    second.mkdir(parents=True)
    (second / 'b.png').write_bytes(b'other-image')
    context['pack_ids'].append('second')
    context['pack_dirs']['second'] = second.parent.parent
    candidates = []
    monkeypatch.setattr(library.random, 'choice', lambda paths: candidates.extend(paths) or paths[-1])
    assert library.pick_category_image('happy') == second / 'b.png'
    assert set(candidates) == {first / 'a.png', second / 'b.png'}
    del context['pack_dirs']['second']
    assert library.pick_category_image('happy') == first / 'a.png'


def test_actual_session_is_resolved_and_rechecked(pack, monkeypatch):
    bot, event, unified = object(), object(), object()
    monkeypatch.setattr(library, 'to_unified_event', lambda b, e: unified if (b, e) == (bot, event) else None)
    seen = []
    def resolve(**kwargs):
        seen.append(kwargs['event'])
        return pack[1]
    pack[0]._resolve_runtime_pack_context = resolve
    assert library.category_catalog(bot=bot, event=event)['categories']
    pack[1]['pack_dirs'] = {}
    assert library.pick_category_image('happy', bot=bot, event=event) is None
    assert seen == [unified, unified]


def test_unknown_event_and_missing_runtime_are_empty(pack, monkeypatch):
    monkeypatch.setattr(library, 'to_unified_event', lambda *a: None)
    assert library.category_catalog(event=object())['categories'] == {}
    monkeypatch.setattr(library, 'get_runtime_or_none', lambda: None)
    assert library.pick_category_image('happy') is None


def test_outside_symlink_non_image_and_large_files_are_excluded(pack, tmp_path, monkeypatch):
    runtime, context, directory = pack
    (directory / 'a.png').unlink()
    outside = tmp_path / 'outside.png'
    outside.write_bytes(b'outside')
    (directory / 'link.png').symlink_to(outside)
    (directory / 'note.txt').write_text('not an image')
    (directory / 'empty.png').touch()
    (directory / 'large.png').write_bytes(b'12345')
    monkeypatch.setattr(library, 'MAX_IMAGE_BYTES', 4)
    assert library.category_catalog()['categories'] == {}
    assert library._images(context, '../happy') == []
    (directory.parent / 'escape').symlink_to(tmp_path, target_is_directory=True)
    assert library._images(context, 'escape') == []


@pytest.mark.asyncio
async def test_public_webui_not_mounted_by_default(pack, monkeypatch):
    import repo
    from repo.nb2 import web
    pack[0]._read_bool = lambda path, default: default
    monkeypatch.setattr(web, 'mount_webui', lambda runtime: pytest.fail('unprotected routes mounted'))
    await repo.mount_webui(pack[0])


def test_nonebot_data_dir_config_is_supported(tmp_path, monkeypatch):
    import nonebot
    from repo.nb2.paths import resolve_data_dir
    monkeypatch.delenv('MEME_MANAGER_DATA_DIR', raising=False)
    monkeypatch.setattr(nonebot, 'get_driver', lambda: SimpleNamespace(config=SimpleNamespace(meme_manager_data_dir=str(tmp_path / 'persistent'))))
    assert resolve_data_dir() == tmp_path / 'persistent'
