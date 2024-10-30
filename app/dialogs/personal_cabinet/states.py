from aiogram.fsm.state import StatesGroup, State


class PersonalMenu(StatesGroup):
    user_info = State()
    deposit = State()
    payment_method = State()
    payment_method_anypay = State()
    payment_method_minimum_pay = State()
    payment_method_anypay_min = State()
    deposit_choose_method = State()
    enter_amount = State()


