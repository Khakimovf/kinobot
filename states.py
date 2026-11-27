from aiogram.fsm.state import State, StatesGroup


class UserModes(StatesGroup):
    movie_search = State()
    media_download = State()


class AdminStates(StatesGroup):
    waiting_for_movie_video = State()
    waiting_for_movie_code = State()
    waiting_for_broadcast = State()
