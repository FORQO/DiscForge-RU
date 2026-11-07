"""Persisted episode-button settings and geometry shared by preview and authoring.

Old projects retain their exact geometry until a layout mode is explicitly set.
Style presets deliberately exclude geometry, typography and padding.
"""
import copy
import math
import re

STYLE_PRESETS = {
    'outline': dict(variant='outline', opacity=0, width=1, glow_strength=45),
    'glass': dict(variant='glass', fill='#11151c', opacity=58, width=1, glow_strength=45),
    'solid': dict(variant='solid', fill='#2e241b', opacity=96, width=2, glow_strength=65),
    'minimal': dict(variant='minimal', opacity=0, width=2, glow_strength=25),
}
APPEARANCE_KEYS = {'variant', 'fill', 'text', 'border', 'highlight', 'opacity', 'width', 'glow_strength'}
LAYOUT_DEFAULTS = dict(mode='free', columns=2, gap_x=40, gap_y=24, align='left',
                       uniform=True, width=760, height=82, x=150, y=400, snap=16)
ANIMATIONS = {'none': (0, 0), 'restrained': (180, 25), 'cinematic': (220, 45)}


def valid_button_key(key):
    return isinstance(key, str) and bool(re.fullmatch(
        r'(play|chapters|subtitles|sub:\d{1,2}|audio|audio:\d{1,2}|chapter:\d{1,3}|'
        r'(previous|next|home):\d{1,3}|custom:[a-f0-9]{12}|catalog:(movie|previous|next):\d{1,3})', key))


def validate_style(style):
    if not isinstance(style, dict):
        raise ValueError('Некорректное оформление кнопки.')
    ranges = dict(width=(0, 10), font_size=(12, 160), opacity=(0, 100), radius=(0, 540),
                  padding_x=(0, 200), padding_y=(0, 200), glow_strength=(0, 100))
    for name, value in style.items():
        if name in ('fill', 'border', 'text', 'highlight'):
            valid = isinstance(value, str) and re.fullmatch(r'#[a-fA-F0-9]{6}', value)
        elif name == 'shape':
            valid = value in ('rounded', 'rectangle', 'pill')
        elif name == 'variant':
            valid = isinstance(value, str) and value in STYLE_PRESETS
        elif name in ranges:
            low, high = ranges[name]
            valid = type(value) is int and low <= value <= high
        elif name == 'font_family':
            valid = isinstance(value, str) and 1 <= len(value) <= 100 and not any(ord(c) < 32 for c in value)
        elif name in ('bold', 'italic', 'disabled'):
            valid = type(value) is bool
        else:
            valid = False
        if not valid:
            raise ValueError('Некорректный параметр оформления кнопки: ' + str(name))


def validate_editor(data):
    if not isinstance(data, dict) or set(data) - {'animation', 'pages', 'order', 'presets'}:
        raise ValueError('Некорректные настройки редактора кнопок.')
    if not isinstance(data.get('animation', 'restrained'), str) or data.get('animation', 'restrained') not in ANIMATIONS:
        raise ValueError('Неизвестный режим анимации.')
    for group in ('pages', 'order', 'presets'):
        if not isinstance(data.get(group, {}), dict) or len(data.get(group, {})) > 1000:
            raise ValueError('Некорректные настройки кнопок: ' + group)
    for scope, layout in data.get('pages', {}).items():
        if not isinstance(scope, str) or not re.fullmatch(r'(catalog:)?\d{1,3}', scope) or not isinstance(layout, dict):
            raise ValueError('Некорректная страница расстановки.')
        for key, value in layout.items():
            if key == 'mode':
                valid = value in ('free', 'column', 'grid')
            elif key == 'align':
                valid = value in ('left', 'center', 'right')
            elif key == 'uniform':
                valid = type(value) is bool
            elif key in ('columns', 'gap_x', 'gap_y', 'width', 'height', 'x', 'y', 'snap'):
                low, high = dict(columns=(1, 8), gap_x=(0, 300), gap_y=(0, 300), width=(32, 1920),
                                 height=(32, 1080), x=(0, 1888), y=(0, 1048), snap=(1, 100))[key]
                valid = type(value) is int and low <= value <= high
            else:
                valid = False
            if not valid:
                raise ValueError('Некорректный параметр сетки: ' + str(key))
    for scope, keys in data.get('order', {}).items():
        if (not isinstance(scope, str) or not re.fullmatch(r'(catalog:)?\d{1,3}', scope)
                or not isinstance(keys, list) or len(keys) > 16 or any(not valid_button_key(k) for k in keys)
                or len(set(keys)) != len(keys)):
            raise ValueError('Некорректный порядок кнопок.')
    if len(data.get('presets', {})) > 100:
        raise ValueError('Достигнут предел собственных пресетов (100).')
    for name, preset in data.get('presets', {}).items():
        if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 60 or any(ord(c) < 32 for c in name)
                or not isinstance(preset, dict) or set(preset) - APPEARANCE_KEYS):
            raise ValueError('Некорректный пресет оформления.')
        validate_style(preset)


def page_layout(design, scope):
    return dict(LAYOUT_DEFAULTS, **design.button_editor.get('pages', {}).get(str(scope), {}))


def ordered_entries(design, scope, entries, keys):
    order = design.button_editor.get('order', {}).get(str(scope), [])
    rank = {key: i for i, key in enumerate(order)}
    pairs = sorted(zip(entries, keys), key=lambda pair: rank.get(pair[1], len(rank)))
    return [p[0] for p in pairs], [p[1] for p in pairs]


def arrange_rects(keys, rects, config):
    """Row-major cells, without shrinking individual buttons or moving the canvas.

    Reject a grid that cannot fit rather than silently overlapping or changing sizes.
    """
    if config['mode'] == 'free' or not keys:
        return dict(zip(keys, rects))
    columns = 1 if config['mode'] == 'column' else config['columns']
    columns = min(columns, len(keys))
    sizes = [(config['width'], config['height']) if config['uniform'] else tuple(r[2:]) for r in rects]
    col_widths = [max(sizes[i][0] for i in range(col, len(keys), columns)) for col in range(columns)]
    rows = math.ceil(len(keys) / columns)
    row_heights = [max(h for _, h in sizes[row*columns:(row+1)*columns]) for row in range(rows)]
    total_w = sum(col_widths) + config['gap_x'] * (columns-1)
    total_h = sum(row_heights) + config['gap_y'] * (rows-1)
    if total_w > 1620 or total_h > 540:
        raise ValueError('Кнопки не помещаются в область списка. Уменьшите размеры/интервалы или измените число колонок.')
    x = config['x'] if config['align'] == 'left' else (1920-total_w)//2 if config['align'] == 'center' else 1770-total_w
    y = config['y']
    if x + total_w > 1920 or y + total_h > 960:
        raise ValueError('Сетка выходит за границы области списка.')
    result = {}
    for i, key in enumerate(keys):
        row, col = divmod(i, columns)
        result[key] = [x+sum(col_widths[:col])+col*config['gap_x'],
                       y+sum(row_heights[:row])+row*config['gap_y'], *sizes[i]]
    return result


def resolved_rects(design, scope, keys):
    from menu_layout import default_button_rect
    rects = [list(design.layout.get(key, default_button_rect(i))) for i, key in enumerate(keys)]
    return arrange_rects(keys, rects, page_layout(design, scope))


def overlaps(rect, others):
    x, y, w, h = rect
    return any(x < a+c and x+w > a and y < b+d and y+h > b for a, b, c, d in others)


def appearance(style):
    return {k: copy.deepcopy(v) for k, v in style.items() if k in APPEARANCE_KEYS}
