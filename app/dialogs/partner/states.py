from aiogram.fsm.state import StatesGroup, State


class PersonalMenu(StatesGroup):
    user_info = State()
    deposit = State()
    enter_amount = State()

