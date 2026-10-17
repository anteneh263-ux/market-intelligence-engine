"""Explicit local resource profiles; these do not grant runtime authority."""
V2 = 'p6.capacity.v2'


def limits(capacity=None):
    if capacity is None:
        return (8, 1000, 64, 10000, None, 64)
    if type(capacity) is not str or capacity != V2:
        raise ValueError('Unsupported capacity profile')
    return (16, 4096, 4096, 1000000, 16 * 1024 * 1024, 4096)
