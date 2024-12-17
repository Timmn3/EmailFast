def info():
    import inspect
    frame = inspect.currentframe().f_back
    locals_snapshot = frame.f_locals  # Получаем локальные переменные
    func_name = frame.f_code.co_name  # Имя вызывающей функции

    print(f"Функция {func_name}:\n")
    for name, value in locals_snapshot.items():
        (print(f"{name} = {value}\n"))