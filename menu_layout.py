"""Geometry shared by editor, image renderer and exported BD-J focus boxes."""
TEXT_RECTS = {'brand': (150, 120, 1590, 50), 'title': (150, 200, 1590, 115),
              'subtitle': (150, 310, 1500, 55), 'chapter_heading': (150, 310, 1500, 55),
              'hint': (150, 966, 1620, 45)}


def element_rect(design, key, default):
    return tuple(design.layout.get(key, default))


def text_style(design, key):
    from disc_menu import THEMES
    size = {'brand': 19, 'title': 60, 'subtitle': 25, 'chapter_heading': 25, 'hint': 20}[key]
    color = THEMES[design.theme][2] if key == 'brand' else '#f5f6f8' if key == 'title' else '#aeb3bc'
    result = dict(font_size=size, font_family='Noto Sans', text=color, bold=key == 'title', italic=False)
    if design.theme in ('amber', 'sapphire') and key == 'title':
        result.update(font_size=68 if design.theme == 'amber' else 76, bold=design.theme == 'sapphire')
    result.update(design.text_styles.get(key, {}))
    return result


def default_button_rect(index):
    from disc_menu import BUTTONS
    if index < len(BUTTONS):
        return BUTTONS[index]
    return (1010, 718 + 106 * ((index-8) % 2), 760, 82)


def snap_rect(rect, others, threshold=8, resize_edges=''):
    """Snap only near edges/centres; threshold is converted from screen pixels."""
    x, y, w, h = rect
    guides = []
    for axis, limit in ((0, 1920), (1, 1080)):
        pos, size = (x, w) if axis == 0 else (y, h)
        targets = [0, limit/2, limit, 150 if axis == 0 else 120, limit-150 if axis == 0 else 960]
        for other in others:
            targets.extend((other[axis], other[axis]+other[axis+2]/2, other[axis]+other[axis+2]))
        near, far = ('w', 'e') if axis == 0 else ('n', 's')
        anchors = [pos, pos+size/2, pos+size] if not resize_edges else ([pos] if near in resize_edges else [pos+size] if far in resize_edges else [])
        choices = [(abs(t-a), t-a, t) for a in anchors for t in targets if abs(t-a) <= threshold]
        if choices:
            _, delta, target = min(choices)
            if not resize_edges:
                pos += delta
            elif near in resize_edges:
                pos += delta
                size -= delta
            else:
                size += delta
            guides.append((axis, round(target)))
        size = max(32, min(limit, round(size)))
        pos = max(0, min(limit-size, round(pos)))
        if axis == 0:
            x, w = pos, size
        else:
            y, h = pos, size
    return [x, y, w, h], guides
