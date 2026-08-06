import asyncio
import ast
import operator
import os
import re
import tempfile
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from urllib.parse import quote, urljoin

import aiohttp
from aiohttp import ClientSession
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bs4 import BeautifulSoup
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

BASE_URL = 'http://nensaysubs.net'
ASK_TIMEOUT = 300
RESTART_NOTICE = 'Bot has restarted, please try a new search'

api_id = int(os.environ['API_ID'])
api_hash = os.environ['API_HASH']
bot_token = os.environ['BOT_TOKEN']
bot = Client('bot_session', api_id=api_id, api_hash=api_hash, bot_token=bot_token)
session = None
scheduler = AsyncIOScheduler()


@dataclass
class UserState:
    """Per-user view of the last search. Shared globals would leak between users."""
    entries: dict = field(default_factory=dict)
    pages: dict = field(default_factory=dict)
    counter: int = 0

    def token(self, payload, label):
        """Store a payload too long for callback_data and return a short handle."""
        self.counter += 1
        handle = str(self.counter)
        self.entries[handle] = (payload, label)
        return handle


states = {}


def state_for(user_id):
    return states.setdefault(user_id, UserState())


_CAPTCHA_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}


def solve_captcha(expression):
    """Evaluate the arithmetic captcha. Never eval() text scraped off the site."""
    def _eval(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -_eval(node.operand)
        if isinstance(node, ast.BinOp) and type(node.op) in _CAPTCHA_OPS:
            return _CAPTCHA_OPS[type(node.op)](_eval(node.left), _eval(node.right))
        raise ValueError(f'unsupported captcha expression: {expression!r}')

    try:
        tree = ast.parse(expression.strip(), mode='eval')
    except SyntaxError as exc:
        raise ValueError(f'unparseable captcha expression: {expression!r}') from exc
    return _eval(tree.body)


def safe_filename(name, fallback='download'):
    """Titles come from the site, so they cannot be trusted as path components."""
    cleaned = re.sub(r'[^\w.\- ]', '_', name).strip(' .')
    return cleaned[:100] or fallback


# --- waiting for a user's reply (replaces pyromod's Client.ask) ---

_pending = {}


async def ask(chat_id, user_id, text, timeout=ASK_TIMEOUT):
    """Send a prompt and wait for that user's next message in that chat."""
    future = asyncio.get_running_loop().create_future()
    _pending[(chat_id, user_id)] = future
    try:
        await bot.send_message(chat_id, text)
        return await asyncio.wait_for(future, timeout)
    finally:
        _pending.pop((chat_id, user_id), None)


@bot.on_message(filters.text & ~filters.command(['start', 'search']), group=-1)
async def capture_reply(_, message):
    if message.from_user is None:
        return
    future = _pending.get((message.chat.id, message.from_user.id))
    if future is not None and not future.done():
        future.set_result(message)
        message.stop_propagation()


async def login():
    """(Re-)authenticate. One session for the process lifetime, so nothing leaks."""
    global session
    print('Starting login')
    if session is None or session.closed:
        session = ClientSession(
            cookie_jar=aiohttp.CookieJar(),
            connector=aiohttp.TCPConnector(force_close=True),
        )
    async with session.get(f'{BASE_URL}/') as response:
        soup = BeautifulSoup(await response.text(), 'html.parser')
        heading = soup.find(name='h1', attrs={'class': 'text4'})
        if heading is None:
            raise RuntimeError('captcha element not found on the landing page')
        captcha = solve_captcha(heading.text)
    await asyncio.sleep(1)
    async with session.post(f'{BASE_URL}/ingreso/index.php/', data={'valor': captcha}) as response:
        print('Login response:', response.status)


def pagination_buttons(state, soup, view):
    """'Anterior'/'Siguiente' links, keyed by view so the two lists cannot collide."""
    rows = []
    for direction, label in (('prev', 'Anterior'), ('next', 'Siguiente')):
        link = soup.find(name='a', string=label)
        if link is None:
            continue
        key = f'{view}_{direction}'
        state.pages[key] = urljoin(BASE_URL, link.get('href'))
        rows.append([InlineKeyboardButton(text=link.text, callback_data=f'page_{key}')])
    return rows


def reload_filter(state, soup):
    btn_list = []
    for children in soup.find_all(name='td', attrs={'valign': 'top'}):
        anchors = children.find_all('a', recursive=True)
        if not anchors:
            continue
        title = anchors[0].text
        btn_list.append([InlineKeyboardButton(text=title, callback_data=f'a_{state.token(title, title)}')])
    btn_list.extend(pagination_buttons(state, soup, 'f'))
    return btn_list


def reload_chapters(state, soup):
    btn_list = []
    title = ''
    dl = ''
    for tag in soup.find_all(['span', 'input']):
        if tag.get('id') == 'bloqueados':
            child = tag.find('a', attrs={'id': 'caramelo'})
            if child is not None:
                start_pos = child.get('href').find('senos')
                dl = child.get('href')[start_pos:]
        if tag.get('value') == 'Bajar': dl = tag.get('onclick')[13:-3]
        if tag.get('id') == 'animetitu': title = tag.text
        if title != '' and dl != '':
            label = title if len(title) <= 58 else f'{title[0:51]}...{title[-3:]}'
            btn_list.append([InlineKeyboardButton(text=label, callback_data=f'l_{state.token(dl, title)}')])
            title = dl = ''
    btn_list.extend(pagination_buttons(state, soup, 'c'))
    return btn_list


@bot.on_message(filters.command('start'))
async def answer(_, message):
    await message.reply_text(
        f'Hello {message.from_user.first_name}, to search for an anime use the command /search. '
        f'Do not specify the anime chapter number only the name EXAMPLE /search Bleach.')


@bot.on_message(filters.command('search'))
async def search(_, message):
    query = ' '.join(message.command[1:]).strip()
    if not query:
        await message.reply_text('Please add a parameter to /search')
        return
    state = state_for(message.from_user.id)
    state.entries.clear()
    async with session.post(f'{BASE_URL}/buscador/', params={'query': query}) as response:
        soup = BeautifulSoup(await response.text(), 'html.parser')
    btn = reload_filter(state, soup)
    if not btn:
        await message.reply_text('No results were found for your query, try another one')
        return
    await message.reply_text(f'<b> Here is the result for {escape(query)}</b>',
                             reply_markup=InlineKeyboardMarkup(btn))


@bot.on_callback_query(filters.regex(r'^page_'))
async def change_page(_, callback_query):
    await callback_query.answer()
    state = state_for(callback_query.from_user.id)
    key = callback_query.data.split('_', 1)[1]
    url = state.pages.get(key)
    if url is None:
        await callback_query.message.reply_text(RESTART_NOTICE)
        return
    async with session.get(url) as response:
        soup = BeautifulSoup(await response.text(), 'html.parser')
    view = key.split('_', 1)[0]
    btn = reload_chapters(state, soup) if view == 'c' else reload_filter(state, soup)
    await callback_query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn))


@bot.on_callback_query(filters.regex(r'^a_'))
async def chapters(_, callback_query):
    await callback_query.answer()
    state = state_for(callback_query.from_user.id)
    entry = state.entries.get(callback_query.data.split('_', 1)[1])
    if entry is None:
        await callback_query.message.reply_text(RESTART_NOTICE)
        return
    title = entry[0]
    async with session.post(f"{BASE_URL}/sub/{quote(title.replace(' ', '_'))}") as response:
        soup = BeautifulSoup(await response.text(), 'html.parser')
    btn = reload_chapters(state, soup)
    await callback_query.edit_message_text(f'<b> Here are {escape(title)} subs</b>',
                                           reply_markup=InlineKeyboardMarkup(btn))


@bot.on_callback_query(filters.regex(r'^l_'))
async def download(_, callback_query):
    await callback_query.answer()
    chat_id = callback_query.message.chat.id
    user_id = callback_query.from_user.id
    state = state_for(user_id)
    entry = state.entries.get(callback_query.data.split('_', 1)[1])
    if entry is None:
        await bot.send_message(chat_id, RESTART_NOTICE)
        return
    link, zip_name = entry
    async with session.get(urljoin(BASE_URL, link)):
        with tempfile.TemporaryDirectory() as tmpdir:
            photo_path = Path(tmpdir) / 'captcha.png'
            async with session.get(f'{BASE_URL}/senos/seguro.php') as pic:
                photo_path.write_bytes(await pic.read())
            await bot.send_photo(chat_id, str(photo_path))
            try:
                code = await ask(chat_id, user_id, '**Please send the onscreen code**')
            except asyncio.TimeoutError:
                await bot.send_message(chat_id, 'Timed out waiting for the code, please try again')
                return
            await bot.send_message(
                chat_id, 'Sending the zipped file. If file is corrupted then you entered the wrong code')
            zip_path = Path(tmpdir) / f'{safe_filename(zip_name)}.zip'
            async with session.post(f'{BASE_URL}/solicitud/', data={'code': code.text.strip().lower()}) as dl:
                zip_path.write_bytes(await dl.read())
            await bot.send_document(chat_id, str(zip_path))
            print('Done!')


async def main():
    await login()
    scheduler.add_job(login, 'interval', minutes=5, id='login_job')
    scheduler.start()
    await bot.start()
    print('Bot started')
    await idle()
    scheduler.shutdown()
    await bot.stop()
    if session is not None and not session.closed:
        await session.close()


if __name__ == '__main__':
    # Must be bot.loop, not asyncio.run(). The @bot.on_* decorators register via
    # dispatcher.add_handler, which defers the work with client.loop.create_task()
    # at import time. A fresh loop would never run those tasks and the bot would
    # start with no handlers at all.
    bot.loop.run_until_complete(main())
