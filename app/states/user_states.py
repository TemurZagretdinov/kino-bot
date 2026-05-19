from aiogram.fsm.state import State, StatesGroup


class UserSearch(StatesGroup):
    waiting_for_movie_code = State()
    waiting_for_movie_name = State()
