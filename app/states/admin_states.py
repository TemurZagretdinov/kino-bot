from aiogram.fsm.state import State, StatesGroup


class AddMovie(StatesGroup):
    title = State()
    code = State()
    description = State()
    archive_post_link = State()


class SerialAddStates(StatesGroup):
    waiting_title = State()
    waiting_code = State()
    waiting_episode_count = State()
    waiting_episode_link = State()


AddSerial = SerialAddStates


class DeleteMovie(StatesGroup):
    code = State()


class AddChannel(StatesGroup):
    link = State()
    forward_post = State()


class DeleteChannel(StatesGroup):
    identifier = State()


class Broadcast(StatesGroup):
    text = State()
    confirm = State()
