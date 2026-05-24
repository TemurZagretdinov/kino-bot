from aiogram.fsm.state import State, StatesGroup


class AddMovie(StatesGroup):
    title = State()
    code = State()
    description = State()
    archive_post_link = State()


class AddSerial(StatesGroup):
    """Multi-step FSM for adding a serial with multiple episodes."""

    title = State()         # Step 1: serial title
    code = State()          # Step 2: serial code (e.g. BB01)
    episode_count = State() # Step 3: how many episodes
    episode_link = State()  # Step 4 (repeated): link for each episode


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
