"""Shared catalog pages for the editor and the authored disc."""
from dataclasses import replace
from episode_buttons import ordered_entries


class CatalogEntries(list):
    """Stable visual identities independent of playback action/page rebinding."""
    def __init__(self, entries, keys, scope):
        super().__init__(entries)
        self.keys, self.scope = keys, scope


def catalog_page(design, titles, page=0, targets=None):
    count = max(1, (len(titles)+4)//5)
    if not 0 <= page < count:
        raise ValueError('Страница коллекции не существует.')
    if targets is not None and len(targets) != len(titles):
        raise ValueError('Для каждого фильма нужна страница меню.')
    entries = [(titles[i], f'page:{targets[i]}' if targets is not None else f'movie:{i}')
               for i in range(page*5, min(len(titles), page*5+5))]
    keys = [f'catalog:movie:{i}' for i in range(page*5, min(len(titles), page*5+5))]
    prefix = 'page' if targets is not None else 'catalog'
    if page:
        entries.append(('Назад' if design.language == 'ru' else 'Previous', f'{prefix}:{page-1}'))
        keys.append(f'catalog:previous:{page}')
    if page+1 < count:
        entries.append(('Далее' if design.language == 'ru' else 'Next', f'{prefix}:{page+1}'))
        keys.append(f'catalog:next:{page}')
    labels = dict(design.labels)
    labels[design.language+':subtitle'] = ('Выберите фильм' if design.language == 'ru' else 'Select a movie') + f' · {page+1}/{count}'
    root = replace(design, labels=labels, audio_tracks=[], subtitle_tracks=[], custom_buttons=[])
    entries, keys = ordered_entries(design, f'catalog:{page}', entries, keys)
    return root, CatalogEntries(entries, keys, f'catalog:{page}')
