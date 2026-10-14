"""Shared catalog pages for the editor and the authored disc."""
from dataclasses import replace


def catalog_page(design, titles, page=0, targets=None):
    count = max(1, (len(titles)+4)//5)
    if not 0 <= page < count:
        raise ValueError('Страница коллекции не существует.')
    if targets is not None and len(targets) != len(titles):
        raise ValueError('Для каждого фильма нужна страница меню.')
    entries = [(titles[i], f'page:{targets[i]}' if targets is not None else f'movie:{i}')
               for i in range(page*5, min(len(titles), page*5+5))]
    prefix = 'page' if targets is not None else 'catalog'
    if page:
        entries.append(('Назад' if design.language == 'ru' else 'Previous', f'{prefix}:{page-1}'))
    if page+1 < count:
        entries.append(('Далее' if design.language == 'ru' else 'Next', f'{prefix}:{page+1}'))
    labels = dict(design.labels)
    labels[design.language+':subtitle'] = ('Выберите фильм' if design.language == 'ru' else 'Select a movie') + f' · {page+1}/{count}'
    root = replace(design, labels=labels, audio_tracks=[], subtitle_tracks=[], custom_buttons=[])
    return root, entries
