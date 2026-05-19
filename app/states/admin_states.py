from aiogram.fsm.state import State, StatesGroup


class AddMovie(StatesGroup):
    title = State()
    code = State()
    description = State()
    archive_post_link = State()


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
