"""운행시간 창 + 창 길이 계산.

창이 자정을 넘어가므로(05:50~00:10) 단순 비교가 안 된다.
피크 창(08:00~18:00)은 넘지 않는다. 두 경우를 다 확인한다.
"""

from datetime import time

from trueeta.window import in_window, parse_hhmm, span_seconds

SERVICE = (parse_hhmm("05:50"), parse_hhmm("00:10"))
PEAK = (parse_hhmm("08:00"), parse_hhmm("18:00"))


def test_midnight_crossing_window_includes_both_sides():
    assert in_window(time(23, 30), *SERVICE)
    assert in_window(time(0, 5), *SERVICE)


def test_midnight_crossing_window_excludes_the_dead_hours():
    assert not in_window(time(0, 30), *SERVICE)
    assert not in_window(time(5, 49), *SERVICE)


def test_peak_window_is_a_plain_interval():
    assert in_window(time(8, 0), *PEAK)
    assert in_window(time(17, 59), *PEAK)
    assert not in_window(time(7, 59), *PEAK)
    assert not in_window(time(18, 0), *PEAK)  # end 는 열린 구간


def test_span_seconds_adds_a_day_when_crossing_midnight():
    assert span_seconds(*SERVICE) == 66_000  # 18시간 20분


def test_span_seconds_of_a_plain_interval():
    assert span_seconds(*PEAK) == 36_000  # 10시간
