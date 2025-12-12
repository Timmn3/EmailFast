from app.services.onlinesim.sms_client import OnlineSMS

import asyncio

API_KEY_ONLINESIM = "REDACTED"

async def check_sms(operation_id):
    client = OnlineSMS(api_key=API_KEY_ONLINESIM)

    # Попробуем перезапросить SMS
    try:
        revise_order = await client.revise_order(operation_id=operation_id)
    except Exception as e:
        print("Ошибка при revise_order:", str(e))
    print(revise_order)
    # Теперь ждём SMS
    order_info = await client.get_order_info(
            operation_id=operation_id,
            get_full_message=True,
            form=1,

        )

    if order_info and isinstance(order_info, list) and 'msg' in order_info[0]:
            sms_code = order_info[0]['msg']
            print(f"📬 SMS получено: {sms_code}")
            return sms_code

    print(f"⏳ SMS не пришло. .")


    print("❌ SMS так и не пришло.")
    return None

if __name__ == "__main__":
    activation_id = 160822492
    asyncio.run(check_sms(activation_id))