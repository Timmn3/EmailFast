from aiogram.fsm.state import StatesGroup, State


class RentCountryMenu(StatesGroup):
    select_country = State()
    deposit = State()
    enter_amount = State()
    country_details = State()
    payment_method = State()
    payment_method_anypay = State()
    payment_method_minimum_pay = State()
    payment_method_anypay_min = State()
    enter_country = State()
    enter_country_error = State()

