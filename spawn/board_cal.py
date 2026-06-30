#!/usr/bin/env python3.13
# board_cal.py — inline date+time picker for card deadlines (ported from yougileTgBot calendarUtils
# to telegram inline buttons). Month grid → pick day → pick time (or "без") → deadline stored as
# "YYYY-MM-DD HH:MM" (sortable for showall) or "YYYY-MM-DD". Callbacks routed via board's poll.
#
# Callback grammar (prefix b_, parsed by board.handle_callback):
#   b_calnav:<cid>:<y>:<m>:<p|n>   prev/next month
#   b_calday:<cid>:<y>:<m>:<d>     pick day → time grid
#   b_caltime:<cid>:<yyyy-mm-dd>:<hhmm|none>   set deadline → card
#   b_calclr:<cid>                 clear deadline
#   b_noop                         inert cell
# Author: pluttan

import json
import calendar as _cal
from datetime import date

MONTHS = ['', 'янв', 'фев', 'мар', 'апр', 'май', 'июн',
          'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
WEEKDAYS = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс']
TIMES = ['06:00', '07:00', '08:00', '09:00', '10:00', '11:00', '12:00', '13:00',
         '14:00', '15:00', '16:00', '17:00', '18:00', '19:00', '20:00', '21:00',
         '22:00', '23:00', '00:00']


def _kb(rows):
    return json.dumps({"inline_keyboard": rows}, ensure_ascii=False)


def _btn(text, data):
    return {"text": text, "callback_data": data}


def view_picker(cid, year=None, month=None):
    today = date.today()
    year = year or today.year
    month = month or today.month
    weeks = _cal.Calendar(firstweekday=0).monthdayscalendar(year, month)
    rows = [[_btn('◂', f'b_calnav:{cid}:{year}:{month}:p'),
             _btn(f'{MONTHS[month]} {year}', 'b_noop'),
             _btn('▸', f'b_calnav:{cid}:{year}:{month}:n')]]
    rows.append([_btn(w, 'b_noop') for w in WEEKDAYS])
    for week in weeks:
        row = []
        for day in week:
            if day == 0:
                row.append(_btn(' ', 'b_noop'))
            else:
                lbl = f'·{day}' if date(year, month, day) == today else str(day)
                row.append(_btn(lbl, f'b_calday:{cid}:{year}:{month}:{day}'))
        rows.append(row)
    rows.append([_btn('✗ снять дедлайн', f'b_calclr:{cid}'), _btn('‹ карточка', f'b_card:{cid}')])
    return '⏱ дедлайн — выбери день:', _kb(rows)


def view_time(cid, year, month, day):
    ds = f'{int(year):04d}-{int(month):02d}-{int(day):02d}'
    rows = []
    for i in range(0, len(TIMES), 4):
        rows.append([_btn(t, f'b_caltime:{cid}:{ds}:{t.replace(":", "")}') for t in TIMES[i:i + 4]])
    rows.append([_btn('без времени', f'b_caltime:{cid}:{ds}:none'),
                 _btn('‹ дни', f'b_deadline:{cid}')])
    return f'⏱ {ds} — во сколько?', _kb(rows)


def nav_month(year, month, direction):
    year, month = int(year), int(month)
    if direction == 'p':
        month -= 1
        if month < 1:
            month, year = 12, year - 1
    else:
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return year, month


def compose_deadline(ds, hhmm):
    if hhmm == 'none':
        return ds
    return f'{ds} {hhmm[:2]}:{hhmm[2:]}'
