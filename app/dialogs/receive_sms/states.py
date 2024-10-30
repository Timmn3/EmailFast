from aiogram.fsm.state import StatesGroup, State


class CountryMenu(StatesGroup):
    select_country = State()
    deposit = State()
    enter_amount = State()
    payment_method = State()
    payment_method_anypay = State()
    payment_method_minimum_pay = State()
    payment_method_anypay_min = State()
    enter_country = State()
    enter_country_error = State()


class ServiceMenu(StatesGroup):
    select_service = State()
    service_info = State()
    enter_service = State()
    enter_service_error = State()
    not_enough_balance = State()

