"""Apply one visual button sample to the project without changing its actions."""
import copy
import math
from dataclasses import replace
from statistics import median
from episode_buttons import TEMPLATE_STYLE_KEYS, page_layout, arrange_rects
from disc_menu import button_style, button_radius, pages, page_elements
from collection_menu import catalog_page


def _starts(rects, axis, tolerance):
    result = []
    for value in sorted(rect[axis] for rect in rects):
        if not result or value - result[-1] > tolerance:
            result.append(value)
    return result


def _fit_layout(config, width, height, count, preferred):
    for columns in sorted(range(1, min(8, count) + 1), key=lambda n: (abs(n-preferred), n)):
        rows = math.ceil(count/columns)
        if columns*width > 1620 or rows*height > 540:
            continue
        gap_x = min(config['gap_x'], (1620-columns*width)//(columns-1)) if columns > 1 else config['gap_x']
        gap_y = min(config['gap_y'], (540-rows*height)//(rows-1)) if rows > 1 else config['gap_y']
        total_w = columns*width + (columns-1)*gap_x
        total_h = rows*height + (rows-1)*gap_y
        result = dict(config, mode='column' if columns == 1 else 'grid', uniform=True,
                      width=width, height=height, columns=columns, gap_x=gap_x, gap_y=gap_y,
                      x=max(0, min(config['x'], 1920-total_w)),
                      y=max(0, min(config['y'], 960-total_h)))
        arrange_rects(list(range(count)), [[0, 0, width, height]]*count, result)
        return result
    raise ValueError('С такими размерами образца все кнопки не помещаются. Уменьшите ширину или высоту выбранной кнопки и повторите применение.')


def project_button_template(design, source_key, source_rect, current_rects, scope, chapters, titles=()):
    """Return a fully checked design; the input and all playback metadata stay intact."""
    width, height = source_rect[2:]
    style = button_style(design, source_key)
    visual = {key: copy.deepcopy(value) for key, value in style.items() if key in TEMPLATE_STYLE_KEYS}
    visual['radius'] = button_radius(style, height)
    config = page_layout(design, scope)
    preferred = 1 if config['mode'] == 'column' else config['columns']
    if config['mode'] == 'free':
        rects = current_rects or [source_rect]
        xs = _starts(rects, 0, max(16, min(48, width//5)))
        ys = _starts(rects, 1, max(8, min(24, height//5)))
        preferred = min(8, max(1, len(xs)))
        config.update(x=min(rect[0] for rect in rects), y=min(rect[1] for rect in rects))
        if len(xs) > 1:
            config['gap_x'] = max(0, min(300, round(median(b-a for a, b in zip(xs, xs[1:])))-width))
        if len(ys) > 1:
            config['gap_y'] = max(0, min(300, round(median(b-a for a, b in zip(ys, ys[1:])))-height))
    graph = pages(chapters, design)
    # Other movies can have full chapter/audio/subtitle pages even if the
    # currently selected movie has only a few chapters or tracks.
    visible_custom = [button for button in design.custom_buttons if button['id'] not in design.hidden_buttons]
    custom_pages = {button['page'] for button in visible_custom}
    custom_peak = max((sum(button['page'] == page for button in visible_custom)
                       + (4 if page == 0 else 8) for page in custom_pages), default=0)
    count = max([8, min(16, custom_peak)] + [len(entries) for entries in graph])
    layout = _fit_layout(config, width, height, count, preferred)
    settings = copy.deepcopy(design.button_editor)
    settings['template'] = {'style': visual, 'layout': layout}
    settings['pages'] = {}
    overrides = {key: {name: copy.deepcopy(value) for name, value in values.items()
                       if name not in TEMPLATE_STYLE_KEYS}
                 for key, values in design.button_styles.items()}
    updated = replace(design, button_editor=settings, button_styles=overrides)
    for page in range(len(graph)):
        page_elements(updated, chapters, page)
    for page in range((len(titles)+4)//5):
        catalog, entries = catalog_page(updated, titles, page)
        page_elements(catalog, [0], 0, entries)
    return updated
