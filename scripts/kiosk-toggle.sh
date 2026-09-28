#!/usr/bin/env bash
# 키오스크 잠깐 나가기 / 돌아가기. labwc 단축키 Ctrl+Alt+K 에 묶여 있다
# (~/.config/labwc/rc.xml, 저장소 사본 scripts/labwc-rc.xml).
#
# 나가기 표시는 /run/user 에 둔다 → 재부팅하면 저절로 지워져 다시 키오스크가 된다.
# 영구로 끄려면 이것 말고 ~/.config/trueeta/kiosk.disabled 를 쓴다.

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
PAUSE="$RUNTIME/trueeta-kiosk.paused"
PROFILE="${XDG_CACHE_HOME:-$HOME/.cache}/trueeta-kiosk"

# 이름이 정확히 chromium 이고 키오스크 프로필을 쓰는 프로세스만 끈다.
# pkill -f "chromium.*trueeta-kiosk" 로 했더니 그 문자열이 명령줄에 든 다른
# 셸까지 죽였다 (실제로 겪음). 평소 쓰는 Chromium 도 건드리지 않는다.
stop_kiosk_chromium() {
    local pid cmd
    for pid in $(pgrep -x chromium); do
        cmd=$(tr '\0' ' ' 2>/dev/null < "/proc/$pid/cmdline") || continue
        # 메인 프로세스만. 렌더러 등 자식(--type=...)은 메인이 죽으면 따라 죽는다
        case "$cmd" in *--type=*) continue ;; esac
        case "$cmd" in *"--user-data-dir=$PROFILE"*) kill "$pid" 2>/dev/null ;; esac
    done
}

if [ -e "$PAUSE" ]; then
    # 돌아가기. 이전 루프가 아직 살아 있으면 kiosk.sh 의 잠금이 알아서 양보한다
    rm -f "$PAUSE"
    setsid "$ROOT/scripts/kiosk.sh" > /dev/null 2>&1 < /dev/null &
else
    # 나가기. 표시를 먼저 만들어야 루프가 Chromium 을 다시 띄우지 않는다
    touch "$PAUSE"
    stop_kiosk_chromium
fi
