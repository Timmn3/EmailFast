import operator

from aiogram_dialog.widgets.kbd import ScrollingGroup, Select
from aiogram_dialog.widgets.text import Format

# def paginated_categories(on_click):
#     return ScrollingGroup(
#         Select(
#             Format('{item.name}'),
#             id='s_scroll_categories',
#             item_id_getter=operator.attrgetter('id'),
#             items='categories',
#             on_click=on_click
#         ),
#         id='categories_ids',
#         width=1, height=SCROLLING_HEIGHT
#     )
