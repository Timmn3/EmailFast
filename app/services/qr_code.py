import asyncio
from io import BytesIO

import qrcode


def _generate_qr_code(link: str):
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(link)
    bytes_io = BytesIO()
    qr.make_image().save(bytes_io, 'PNG')
    bytes_io.seek(0)
    return bytes_io


async def generate_qr_code(link: str):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _generate_qr_code, link)
