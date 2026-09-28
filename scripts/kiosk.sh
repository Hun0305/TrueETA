#!/usr/bin/env bash
# TrueETA 키오스크 — 전원만 꽂으면 8인치 화면에 전광판이 뜨게 한다.
#
# labwc 가 로그인할 때 ~/.config/labwc/autostart 에서 이 스크립트를 부른다
# (docs/kiosk.md). 로직은 저장소에 두고 autostart 에는 한 줄만 둔다.
#
#   끄기      touch ~/.config/trueeta/kiosk.disabled   (다음 재시작부터 안 뜬다)
#   다른 화면 TRUEETA_KIOSK_URL=http://localhost:8099/?preset=출근-신분당
#
# 하는 일:
#   1. 서버(/api/health)가 응답할 때까지 기다린다. 데스크톱이 서버보다 먼저 뜰 수 있다
#   2. Chromium 을 키오스크로 띄운다. 전용 프로필이라 평소 쓰는 브라우저와 섞이지 않는다
#   3. 꺼지면 5초 뒤 다시 띄운다. 항상 켜두는 화면이다

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${TRUEETA_PORT:-8099}"
URL="${TRUEETA_KIOSK_URL:-http://localhost:${PORT}/}"
HEALTH="http://localhost:${PORT}/api/health"
PROFILE="${XDG_CACHE_HOME:-$HOME/.cache}/trueeta-kiosk"
DISABLE="$HOME/.config/trueeta/kiosk.disabled"
LOG="$ROOT/var/logs/kiosk.log"
WAIT_SEC=180

mkdir -p "$(dirname "$LOG")" "$PROFILE" 2>/dev/null

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

# 키오스크 전용 프로필에 설정을 직접 써 넣는다. Chromium 을 띄우기 직전마다.
#
#   번역 끄기   UI 가 영어라 한국어 페이지에 번역 팝업이 떠서 오른쪽 위 갱신 시각을
#              가렸다. 플래그(--disable-features=Translate, --lang=ko)도, 페이지의
#              <meta name="google" content="notranslate"> 도 이 버전(147)에서는
#              막지 못했다. 실제 화면 캡처로 확인. 프로필 설정은 먹는다
#   정상 종료   전원을 그냥 뽑으면 '비정상 종료'로 기록돼 다음 부팅에 페이지 복원
#   표시       창이 뜬다. 띄울 때마다 정상 종료로 되돌려 둔다
#
# Chromium 이 켜져 있으면 종료할 때 이 파일을 덮어쓰므로 반드시 띄우기 전에 한다.
seed_prefs() {
    python3 - "$PROFILE/Default/Preferences" <<'PY' 2>> "$LOG"
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
try:
    prefs = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    prefs = {}
prefs.setdefault("translate", {})["enabled"] = False
prefs["translate_blocked_languages"] = ["ko"]
prefs.setdefault("intl", {})["selected_languages"] = "ko,en-US,en"
profile = prefs.setdefault("profile", {})
profile["exit_type"] = "Normal"
profile["exited_cleanly"] = True
path.write_text(json.dumps(prefs), encoding="utf-8")
PY
}

if [ -e "$DISABLE" ]; then
    log "비활성화 파일이 있어 키오스크를 띄우지 않는다: $DISABLE"
    exit 0
fi

# 1. 서버 대기
waited=0
until curl -fs -o /dev/null --max-time 2 "$HEALTH"; do
    if [ "$waited" -ge "$WAIT_SEC" ]; then
        log "서버가 ${WAIT_SEC}초 안에 안 떴다 — 그래도 띄운다 (화면이 '서버 연결 실패' 를 보여줄 것)"
        break
    fi
    sleep 2
    waited=$((waited + 2))
done
[ "$waited" -gt 0 ] && log "서버 응답까지 ${waited}초 기다렸다"

# 2~3. 띄우고, 꺼지면 다시
#
# --ozone-platform=wayland Chromium 은 기본으로 X11 을 찾아서 WAYLAND_DISPLAY 만 있으면
#                          "Missing X server" 로 바로 죽는다 (실제로 겪음). labwc 는
#                          Wayland 컴포지터라 네이티브로 띄우는 게 가볍고 확실하다
# --lang=ko + Translate 끄기  UI 가 영어면 한국어 페이지에 번역 팝업이 떠서
#                          오른쪽 위 갱신 시각을 가린다 (실제 화면에서 확인)
# --password-store=basic   키링 잠금 해제 창이 떠서 화면을 가리는 걸 막는다
# --hide-crash-restore-bubble / --disable-session-crashed-bubble
#                          전원을 그냥 뽑으면 다음 부팅에 '페이지 복원' 창이 뜬다
# --check-for-update-interval  업데이트 알림을 사실상 끈다
# 출력은 버린다. Chromium 은 GPU 경고 등을 끝없이 쏟아내고, 이 로그는 교체되지
# 않는 파일이라 커지면 SD 카드를 채운다. 여기에는 기동·종료만 남긴다.
while [ ! -e "$DISABLE" ]; do
    seed_prefs
    log "chromium 시작: $URL"
    chromium \
        --ozone-platform=wayland \
        --kiosk \
        --lang=ko \
        --user-data-dir="$PROFILE" \
        --no-first-run \
        --noerrdialogs \
        --disable-infobars \
        --disable-session-crashed-bubble \
        --hide-crash-restore-bubble \
        --disable-features=Translate,TranslateUI \
        --check-for-update-interval=31536000 \
        --password-store=basic \
        "$URL" > /dev/null 2>&1
    log "chromium 종료 (코드 $?) — 5초 뒤 다시 띄운다"
    sleep 5
done
log "비활성화 파일이 생겨 멈춘다"
