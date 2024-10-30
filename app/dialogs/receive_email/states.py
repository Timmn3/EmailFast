from aiogram.fsm.state import StatesGroup, State


class ReceiveEmailMenu(StatesGroup):
    receive_email = State()
    rent_email = State()
    rent_email_confirm = State()
    rent_email_success = State()
    not_enough_balance = State()
    rent_email_discount = State()  # Состояние для аренды с учетом скидки
    rent_email_no_discount = State()  # Состояние для аренды без скидки
