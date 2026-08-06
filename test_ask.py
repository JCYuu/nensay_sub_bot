"""Exercise the ask()/capture_reply pair that replaces pyromod's Client.ask."""
import asyncio
import os
import pathlib
import sys

os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'deadbeef')
os.environ.setdefault('BOT_TOKEN', '1:token')
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import pytest

import bot as b


class FakeUser:
    def __init__(self, uid):
        self.id = uid


class FakeChat:
    def __init__(self, cid):
        self.id = cid


class StopPropagation(Exception):
    pass


class FakeMessage:
    def __init__(self, chat_id, user_id, text):
        self.chat = FakeChat(chat_id)
        self.from_user = FakeUser(user_id)
        self.text = text

    def stop_propagation(self):
        raise StopPropagation


@pytest.fixture(autouse=True)
def stub_send(monkeypatch):
    sent = []

    async def fake_send_message(chat_id, text, *a, **kw):
        sent.append((chat_id, text))

    monkeypatch.setattr(b.bot, 'send_message', fake_send_message)
    b._pending.clear()
    return sent


async def deliver(message):
    """pyrogram's decorator returns the function unwrapped, so call it directly."""
    await b.capture_reply(b.bot, message)


@pytest.mark.asyncio
async def test_ask_resolves_with_the_users_reply(stub_send):
    task = asyncio.create_task(b.ask(10, 99, 'send the code'))
    await asyncio.sleep(0)
    with pytest.raises(StopPropagation):
        await deliver(FakeMessage(10, 99, 'ABC123'))
    reply = await task
    assert reply.text == 'ABC123'
    assert stub_send == [(10, 'send the code')]


@pytest.mark.asyncio
async def test_pending_entry_is_cleaned_up(stub_send):
    task = asyncio.create_task(b.ask(10, 99, 'prompt'))
    await asyncio.sleep(0)
    assert (10, 99) in b._pending
    with pytest.raises(StopPropagation):
        await deliver(FakeMessage(10, 99, 'x'))
    await task
    assert b._pending == {}


@pytest.mark.asyncio
async def test_another_user_cannot_answer_your_prompt(stub_send):
    """A shared/global listener would let user B answer user A's captcha."""
    task = asyncio.create_task(b.ask(10, 99, 'prompt'))
    await asyncio.sleep(0)
    await deliver(FakeMessage(10, 1234, 'not mine'))  # different user, same chat
    assert not task.done()
    with pytest.raises(StopPropagation):
        await deliver(FakeMessage(10, 99, 'mine'))
    assert (await task).text == 'mine'


@pytest.mark.asyncio
async def test_message_falls_through_when_nothing_is_pending(stub_send):
    """No pending ask => no stop_propagation, so /search still reaches its handler."""
    await deliver(FakeMessage(10, 99, 'just chatting'))


@pytest.mark.asyncio
async def test_ask_times_out_and_does_not_leak(stub_send):
    with pytest.raises(asyncio.TimeoutError):
        await b.ask(10, 99, 'prompt', timeout=0.05)
    assert b._pending == {}


@pytest.mark.asyncio
async def test_concurrent_users_do_not_cross_wires(stub_send):
    a = asyncio.create_task(b.ask(1, 111, 'p'))
    c = asyncio.create_task(b.ask(2, 222, 'p'))
    await asyncio.sleep(0)
    with pytest.raises(StopPropagation):
        await deliver(FakeMessage(2, 222, 'for-c'))
    with pytest.raises(StopPropagation):
        await deliver(FakeMessage(1, 111, 'for-a'))
    assert (await a).text == 'for-a'
    assert (await c).text == 'for-c'


def test_handlers_register_on_the_startup_loop():
    """Regression guard for the startup path.

    dispatcher.add_handler defers registration via client.loop.create_task() at
    import time. Running main() on any loop other than bot.loop leaves those tasks
    unexecuted and the bot answers nothing. This asserts the loop bot.py actually
    uses does register them, in the right groups.
    """
    async def drain():
        await asyncio.sleep(0.1)

    b.bot.loop.run_until_complete(drain())

    groups = b.bot.dispatcher.groups
    names = {g: [h.callback.__name__ for h in groups[g]] for g in groups}
    assert names[-1] == ['capture_reply'], names
    assert set(names[0]) == {'answer', 'search', 'change_page', 'chapters', 'download'}, names
    # capture_reply must sit in a strictly lower group so it intercepts first
    assert min(groups) == -1
