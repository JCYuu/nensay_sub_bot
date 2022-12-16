import asyncio
import aiohttp
import os
import pyromod.listen
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiohttp import ClientSession
from bs4 import BeautifulSoup
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

api_id = os.getenv('API_ID') 
api_hash = os.getenv('API_HASH')
bot_token = '1909716050:AAHSaFsuLzaXOK1OiBhVnqYHsczn7d6udDE'
bot = Client("bot_session", api_id=api_id, api_hash=api_hash, bot_token=bot_token)
session = ''
connector = ''
dic = {}
is_chapter = False
id_list = []
scheduler = AsyncIOScheduler()


async def login():
    print('Starting login')
    global session, connector
    connector = aiohttp.TCPConnector(force_close=True)
    session = ClientSession(cookie_jar=aiohttp.CookieJar(), connector=connector)
    async with session.get("http://nensaysubs.net/") as response:
        soup = BeautifulSoup(await response.text(), 'html.parser')
        captcha = eval(soup.find(name='h1', attrs={'class': 'text4'}).text)
        await asyncio.sleep(1)
    async with session.post("http://nensaysubs.net/ingreso/index.php/", data={'valor': captcha}) as response:
        print(response)


async def reload_filter(soup):
    btn_list = []
    children = soup.find_all(name='td', attrs={'valign': 'top'})
    filtering = [x.findChildren('a', recursive=True) for x in children]
    for i, element in enumerate(filtering):
        title = element[0].text if len(element[0].text) < 61 else element[0].text[:61]
        btn_list.append([InlineKeyboardButton(text=element[0].text, callback_data=f'a_{title}')])
        dic[title] = element[0].text
    print(dic)
    previous = soup.find(name='a', text='Anterior')
    next = soup.find(name='a', text='Siguiente')
    if previous is not None:
        dic['prev'] = previous.get('href')
        btn_list.append([InlineKeyboardButton(text=previous.text, callback_data=f'page_prev')])
    if next is not None:
        dic['next'] = next.get('href')
        btn_list.append([InlineKeyboardButton(text=next.text, callback_data=f'page_next')])
    return btn_list


async def reload_chapters(soup):
    btn_list = []
    title = ''
    dl = ''
    i = 0
    for tag in soup.find_all(['span', 'input']):
        if tag.get('id') == 'bloqueados':
            child = tag.find('a', attrs={'id': 'caramelo'})
            start_pos = child.get('href').find('senos')
            dl = child.get('href')[start_pos:]
        if tag.get('value') == 'Bajar': dl = tag.get('onclick')[13:-3]
        if tag.get('id') == 'animetitu': title = tag.text
        if title != '' and dl != '':
            if len(title) > 58: title = f'{title[0:51]}...{title[-3:]}'
            btn_list.append([InlineKeyboardButton(text=title, callback_data=f'l_{dl}')])
            dic[dl] = title
            i += 1
            title = dl = ''
    previous = soup.find(name='a', text='Anterior')
    next = soup.find(name='a', text='Siguiente')
    if previous is not None:
        dic['prevC'] = previous.get('href')
        btn_list.append([InlineKeyboardButton(text=previous.text, callback_data=f'page_prevC')])
    if next is not None:
        dic['nextC'] = next.get('href')
        btn_list.append([InlineKeyboardButton(text=next.text, callback_data=f'page_nextC')])
    return btn_list


@bot.on_message(filters.command('start'))
async def answer(_, message):
    await bot.send_message(message.chat.id,
                           f'Hello {message.from_user.username}, to search for an anime use the command /search.')


@bot.on_message(filters.command('search'))
async def search(_, message):
    # await login()
    query = message.text.replace('/search', '').strip()
    if not query:
        await bot.send_message(message.chat.id, 'Please add a parameter to /search')
        return
    global is_chapter
    is_chapter = False
    async with session.post(f"http://nensaysubs.net/buscador/?query={query.replace(' ', '+')}") as response:
        soup = BeautifulSoup(await response.text(), 'html.parser')
        btn = await reload_filter(soup)
        print(btn)
        if not btn:
            await bot.send_message(chat_id=message.chat.id,
                                   text='No results were found for your query, try another one')
            return
        await message.reply_text(f"<b> Here is the result for {query}</b>", reply_markup=InlineKeyboardMarkup(btn))


@bot.on_callback_query(filters.regex('page_.+'))
async def change_page(_, callback_query):
    _, page = callback_query.data.split('_')
    async with session.get(dic[page]) as response:
        soup = BeautifulSoup(await response.text(), 'html.parser')
        btn = await reload_chapters(soup) if is_chapter else await reload_filter(soup)
        await callback_query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn))


@bot.on_callback_query(filters.regex('a_.+'))
async def chapters(_, callback_query):
    _, anime = callback_query.data.split('_')
    title = dic[anime]
    print(title)
    global is_chapter
    is_chapter = True
    print(dic)
    async with session.post(f"http://nensaysubs.net/sub/{title.replace(' ', '_')}") as response:
        soup = BeautifulSoup(await response.text(), 'html.parser')
        btn = await reload_chapters(soup)
        await callback_query.edit_message_text(f"<b> Here are {title} subs</b>",
                                               reply_markup=InlineKeyboardMarkup(btn))


@bot.on_callback_query(filters.regex('l_.+'))
async def download(_, callback_query):
    _, link = callback_query.data.split("_")
    zip_name = ''
    try:
        zip_name = dic[link]
    except:
        await bot.send_message(callback_query.message.chat.id, 'Bot has restarted, please try a new search')
    dl_link = f'https://nensaysubs.net/{link}'
    print(dl_link)
    print(zip_name)
    async with session.get(dl_link):
        async with session.get('http://nensaysubs.net/senos/seguro.php') as pic:
            chunk = await pic.content.read()
            with open('photo.png', 'wb') as file:
                file.write(chunk)            
            await bot.send_photo(callback_query.message.chat.id, 'photo.png')
            os.remove('photo.png')
            code = await bot.ask(chat_id=callback_query.message.chat.id, text='**Please send the onscreen code**')
            print(code.text)
            await bot.send_message(callback_query.message.chat.id,
                                   'Sending the zipped file. If file is corrupted then you entered the wrong code')
            async with session.post('http://nensaysubs.net/solicitud/', data={'code': code.text.lower()}) as dl:
                chunk = await dl.content.read()
                with open(f"{zip_name}.zip", 'wb') as file:
                    file.write(chunk)
                await bot.send_document(callback_query.message.chat.id, f'{zip_name}.zip')
                os.remove(f'{zip_name}.zip')
                print("Done!")


scheduler.add_job(login, 'interval', minutes=15, id='login_job', next_run_time=datetime.now())
scheduler.start()
bot.run()
