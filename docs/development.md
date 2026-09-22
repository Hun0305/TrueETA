# 개발 루프 (코드 고치고 확인하기)

`.py` 를 고쳤을 때 언제 반영되는지, 개발 중 자동 재시작(reload)을 어떻게 켜는지 정리. (2026-09-22)

서버는 **뜰 때 import 한 코드를 그대로 들고 있는다.** 그래서 기본 실행
(`python -m trueeta`)에서는 소스를 고쳐도 떠 있는 프로세스에 반영되지 않는다.
uvicorn 의 reload 는 `TRUEETA_RELOAD=1` 일 때만 켜진다
([`__main__.py`](../src/trueeta/__main__.py)).

---

## 1. 실행 방식 3가지

| 상황 | 명령 | 코드 수정 반영 |
|---|---|---|
| 운영 (상시) | systemd `trueeta.service` | ✗ — `sudo systemctl restart trueeta.service` 필요 |
| 개발 | `TRUEETA_RELOAD=1 TRUEETA_PORT=8098 .venv/bin/python -m trueeta` | ✓ — 저장하면 자동 재시작 |
| 수동 확인 | `.venv/bin/python -m trueeta` | ✗ — Ctrl+C 후 다시 실행 |

**포트를 8098 로 바꾸는 이유**: systemd 서비스가 이미 8099 를 잡고 있다.
그대로 띄우면 `[Errno 98] address already in use` 가 난다.
서비스를 내리고 8099 를 쓰거나, 위처럼 다른 포트로 띄우거나 둘 중 하나.

reload 를 켜면 프로세스가 둘이 된다 — 파일을 감시하는 **reloader** 와, 그 자식인
**server**. 로그의 `Started reloader process [9226]` 이 부모다.

---

## 2. 무엇이 자동 반영되고, 무엇이 아닌가

| 고친 것 | reload 켠 상태 | 필요한 조치 |
|---|---|---|
| `src/trueeta/**.py` | ✓ 자동 재시작 | 없음 |
| `web/index.html` | — | **브라우저 새로고침만**. `FileResponse` 라 요청마다 디스크에서 읽는다 |
| `config.yaml` | ✗ | 서버 재시작. lifespan 에서 한 번만 읽는다 |
| `.env` | ✗ | 서버 재시작 |

감시 대상은 `src/` 아래 `.py` 뿐이다. `reload_dirs` 를 지정하지 않으면 uvicorn 은
현재 디렉터리 전체를 감시해서 `.venv` 의 `.py` 수천 개까지 0.25 초마다 `stat` 한다.
라즈베리파이에서는 그게 그냥 낭비라 `src/` 로 좁혀뒀다.

`watchfiles` 가 설치돼 있지 않아 uvicorn 은 **StatReload**(mtime 폴링) 로 동작한다.
지금 규모(파일 수십 개)에서는 충분하다. 이벤트 기반으로 바꾸려면 `pip install watchfiles`.

---

## 3. 쿼터 주의 — 재시작 1번 = API 2콜

폴러는 기동 직후 곧바로 1사이클을 돈다 ([`poller.py`](../src/trueeta/poller.py) `run_poller`).
정류장이 2곳이니 **재시작·리로드 1번마다 2콜**이 나간다. 개발계정 한도는 하루 1,000건인데,
파일을 100번 저장하면 그것만으로 200콜이다.

그래서 작업 종류별로:

| 작업 | 방법 | 호출 |
|---|---|---|
| 화면·CSS·레이아웃 | `http://localhost:8098/?demo=1` | 0콜 (API 를 안 탄다) |
| 판정 로직 | `.venv/bin/pytest` — `tests/fixtures/` 의 실응답 사용 | 0콜 |
| 실제 도착정보 확인 | reload 서버 또는 `scripts/probe.py` | 1콜 이상 |

오늘 쓴 건수는 `var/quota.json` 에서 본다. 개발 서버와 systemd 서비스가 **같은 파일에**
누적하므로, 둘을 동시에 띄우면 그 시간 동안 소비량이 2배가 된다.

---

## 4. 서버를 끌 때 — `pkill -f trueeta` 금지

패턴이 systemd 가 띄운 프로세스까지 잡는다. 실제로 이 문서를 쓰다가 한 번 죽였고,
그때 알게 된 것: **uvicorn 은 SIGTERM 을 정상 종료로 처리해 exit 0 으로 끝나므로,
`Restart=on-failure` 가 걸리지 않아 systemd 가 되살려주지 않는다.** 조용히 내려간 채로 남는다.

```bash
# 개발 서버만 끈다 — reloader(부모) PID 를 포트로 찾아서 종료
ss -ltnp | grep ':8098'
kill <reloader PID>

# 포그라운드로 띄웠으면 그냥 Ctrl+C

# 서비스는 systemctl 로만
sudo systemctl stop trueeta.service
sudo systemctl restart trueeta.service    # 코드 고친 뒤 운영에 반영
```

내려간 서비스를 되살리는 것도 `sudo systemctl start trueeta.service`.
상태·로그 명령은 [remote-access.md 3장](remote-access.md#3-운영-명령어) 참고.
