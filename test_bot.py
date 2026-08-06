"""Unit tests for bot.py's pure logic. Run with the scratchpad venv."""
import os
import pathlib
import sys

os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'deadbeef')
os.environ.setdefault('BOT_TOKEN', '1:token')
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import pytest
from bs4 import BeautifulSoup

import bot as b


# --- solve_captcha: the eval() replacement ---

@pytest.mark.parametrize('expr, expected', [
    ('2+3', 5),
    ('10 - 4', 6),
    ('6*7', 42),
    ('9/3', 3),
    ('2+3*4', 14),
    ('-5+8', 3),
    ('  7 + 1  ', 8),
])
def test_solve_captcha_arithmetic(expr, expected):
    assert b.solve_captcha(expr) == expected


@pytest.mark.parametrize('payload', [
    '__import__("os").system("id")',
    'open("/etc/passwd").read()',
    'print(1)',
    '[].__class__',
    'lambda: 1',
    'x + 1',
    '',
    'not python at all !!',
    '2 ** 999999999',
])
def test_solve_captcha_rejects_non_arithmetic(payload):
    """The old code eval()'d this straight off an http:// page."""
    with pytest.raises(ValueError):
        b.solve_captcha(payload)


# --- safe_filename: path traversal via scraped titles ---

@pytest.mark.parametrize('raw, expected', [
    ('../../etc/passwd', '_.._etc_passwd'),  # separators replaced, then leading dots stripped
    ('/absolute/path', '_absolute_path'),
    ('..', 'download'),
    ('', 'download'),
    ('Bleach 01', 'Bleach 01'),
    ('Fullmetal: Brotherhood', 'Fullmetal_ Brotherhood'),
])
def test_safe_filename(raw, expected):
    assert b.safe_filename(raw) == expected


def test_safe_filename_has_no_separators():
    for raw in ['a/b', 'a\\b', '../x', 'x\x00y']:
        assert '/' not in b.safe_filename(raw)
        assert '\\' not in b.safe_filename(raw)


def test_safe_filename_is_bounded():
    assert len(b.safe_filename('x' * 5000)) == 100


# --- tokens: the callback_data underscore + 64-byte fixes ---

def test_token_roundtrip_with_underscores():
    """The old split('_') raised ValueError on any title containing '_'."""
    state = b.UserState()
    payload = 'senos/some_file_name_with_underscores.zip'
    handle = state.token(payload, 'Some_Label')
    data = f'l_{handle}'
    assert state.entries[data.split('_', 1)[1]] == (payload, 'Some_Label')


def test_callback_data_stays_within_telegram_limit():
    """Telegram caps callback_data at 64 bytes; titles here are non-ASCII."""
    state = b.UserState()
    for i in range(500):
        title = f'Añoranza señorial épica número {i} ' + 'ñ' * 200
        for prefix in ('a_', 'l_'):
            data = f'{prefix}{state.token(title, title)}'
            assert len(data.encode('utf-8')) <= 64


def test_tokens_are_unique():
    state = b.UserState()
    handles = [state.token(f'p{i}', f'l{i}') for i in range(100)]
    assert len(set(handles)) == 100


def test_states_are_isolated_per_user():
    b.states.clear()
    a = b.state_for(1)
    c = b.state_for(2)
    a.token('payload-for-user-1', 'one')
    assert c.entries == {}
    assert b.state_for(1) is a


# --- pagination: relative hrefs and per-view keys ---

def test_pagination_resolves_relative_hrefs_and_keys_by_view():
    soup = BeautifulSoup(
        '<a href="/buscador/?p=2">Siguiente</a><a href="/buscador/?p=0">Anterior</a>',
        'html.parser')
    state = b.UserState()
    rows = b.pagination_buttons(state, soup, 'f')
    assert state.pages['f_prev'] == 'http://nensaysubs.net/buscador/?p=0'
    assert state.pages['f_next'] == 'http://nensaysubs.net/buscador/?p=2'
    assert [r[0].callback_data for r in rows] == ['page_f_prev', 'page_f_next']

    b.pagination_buttons(state, soup, 'c')
    # the chapter view must not clobber the filter view's targets
    assert state.pages['f_prev'] == 'http://nensaysubs.net/buscador/?p=0'
    assert state.pages['c_prev'] == 'http://nensaysubs.net/buscador/?p=0'


def test_change_page_key_parsing():
    for view in ('f', 'c'):
        for direction in ('prev', 'next'):
            key = f'page_{view}_{direction}'.split('_', 1)[1]
            assert key.split('_', 1)[0] == view


# --- reload_filter: the IndexError on anchor-less cells ---

def test_reload_filter_skips_cells_without_anchors():
    soup = BeautifulSoup(
        '<td valign="top"></td>'
        '<td valign="top"><a href="/x">Bleach</a></td>',
        'html.parser')
    state = b.UserState()
    rows = b.reload_filter(state, soup)
    assert len(rows) == 1
    assert rows[0][0].text == 'Bleach'


def test_reload_filter_preserves_full_title():
    long_title = 'A' * 300
    soup = BeautifulSoup(f'<td valign="top"><a href="/x">{long_title}</a></td>', 'html.parser')
    state = b.UserState()
    rows = b.reload_filter(state, soup)
    handle = rows[0][0].callback_data.split('_', 1)[1]
    assert state.entries[handle][0] == long_title


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q']))
