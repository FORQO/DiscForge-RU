"""Spatial remote navigation; keep the integer rule in DiscForgeXlet identical."""


def nearest_button(rects, current, direction):
    """Nearest centre in the requested open half-plane; no edge wrapping.

    Doubled centres preserve half-pixel coordinates without floating point.
    Equal distances prefer less sideways displacement, then stable page order.
    Overlapping buttons with identical centres cannot be distinguished spatially.
    """
    if not 0 <= current < len(rects) or direction not in ('up', 'down', 'left', 'right'):
        return current
    x, y, w, h = rects[current]
    cx, cy = 2*x+w, 2*y+h
    best, target = None, current
    for index, (x, y, w, h) in enumerate(rects):
        dx, dy = 2*x+w-cx, 2*y+h-cy
        forward = {'left': -dx, 'right': dx, 'up': -dy, 'down': dy}[direction]
        if index == current or forward <= 0:
            continue
        sideways = abs(dy if direction in ('left', 'right') else dx)
        rank = (dx*dx+dy*dy, sideways, index)
        if best is None or rank < best:
            best, target = rank, index
    return target
