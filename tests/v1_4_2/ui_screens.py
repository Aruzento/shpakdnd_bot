"""Earlier draft audit API delegates to the canonical real-renderer fixtures."""
from tests.v1_4_2.test_screen_contracts import render_screen as current_render


def render_screen(sid,w,p,heroes,goods,db):
    _,markup,renderer=current_render(sid)
    return markup,renderer.__module__+':'+renderer.__name__
