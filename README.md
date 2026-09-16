# TrueETA

용인 죽전 지역 정류장 2곳의 22·25·59번 버스 도착정보를 8인치 화면에 띄우고,
**회차대기** 차량을 실제 도착시간과 구분해 보여주는 라즈베리파이 전광판.

회차지에 서 있는 버스는 지도 앱에서 "곧 도착"처럼 보인다.
특히 25번은 회차점이 정류장에서 4정거장밖에 안 떨어져 있어 착각하기 쉽다.
그걸 눈에 띄게 구분하는 것이 이 프로젝트의 목적이다.

- 설계: [docs/architecture.md](docs/architecture.md)
- API 스펙: [docs/api-endpoints.md](docs/api-endpoints.md)
- 1차(프로브) 결과: [docs/phase1-probe.md](docs/phase1-probe.md)

## 현재 단계

**2차 진행 중** — 10분 주기 폴링 + 회차대기 판정 + 웹 화면까지 동작한다.
적응형 주기·SQLite 로그·systemd·키오스크 자동실행은 아직.

| 정류장 | stationId | 노선 |
|---|---|---|
| 도담마을아이파크.죽전휴먼빌 | `228001059` | 22(미금역 방향) · 59(수지구청 방향) |
| 대일초교후문 | `228003549` | 25(미금역 방향) |

---

## 설치

```bash
sudo apt install fonts-noto-cjk      # 한글 폰트. 없으면 화면 글자가 두부(□)로 나온다
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env                 # SERVICE_KEY 에 공공데이터포털 '디코딩' 키
.venv/bin/pytest                     # 51개, 네트워크·쿼터 0
```

서비스키는 **디코딩** 키를 넣는다. 인코딩 키를 넣으면 이중 인코딩으로 인증에 실패한다.

## 실행

```bash
.venv/bin/python -m trueeta
```

| 경로 | 내용 |
|---|---|
| `/` | 전광판 화면 |
| `/?demo=1` | 가짜 데이터. 막차 후에 화면 손볼 때 쓴다 (API 를 안 탄다) |
| `/api/board` | 최신 상태 JSON |
| `/api/health` | 폴러 상태 |

기본 포트는 **8099**다. 8080 은 이 라즈베리파이의 다른 서버(`pi-server`)가
쓰고 있어서 피했다. 바꾸려면 `TRUEETA_PORT=9000 .venv/bin/python -m trueeta`.

같은 네트워크의 폰·노트북에서도 `http://<라즈베리파이 IP>:8099` 로 열린다.

## 화면 읽는 법

```
노선    정류장                    첫 번째      두 번째
22     도담마을아이파크          4분          17분
25     대일초교후문             [회차대기]    23분
59     도담마을아이파크          7분          21분
```

| 표시 | 뜻 |
|---|---|
| `4분` (민트색) | 운행 중. 남은 시간 |
| `곧 도착` | 1분 이내 |
| `회차대기` **채운 배지** | API 가 `flag=WAIT` 으로 확정해준 회차대기 |
| `회차대기` 테두리만 | 위치로 추정한 회차대기 (API 가 신호를 안 줬을 때) |
| `운행종료` | 막차 후 |
| `—` | 노선은 도는데 지금 오는 차가 없음 |

오른쪽 위에 마지막 갱신 시각이 뜬다. 20분 넘게 안 바뀌면 주황색으로 변한다.

**남은 시간은 화면에서 1초씩 깎인다.** 서버가 10분에 한 번만 조회하므로,
조회 시각을 기준으로 보간하지 않으면 화면이 멈춰 보인다.

## 설정

`config.yaml` 에서 바꾼다. 고친 뒤 서버를 재시작해야 적용된다.

| 항목 | 기본값 | 설명 |
|---|---|---|
| `stops` | 정류장 2곳 | `station_id` 와 표시할 `routes` |
| `polling.interval_sec.far` | `600` | 조회 주기(초). 10분 = 하루 약 216콜 |
| `polling.service_window` | `05:50`~`00:10` | 이 밖에는 **조회하지 않는다** |
| `display.vehicles_per_route` | `2` | 노선당 표시할 차량 수 |
| `judge.stall_count` | `3` | 회차대기 추정 임계값 (아직 튜닝 전) |

운행시간 밖에는 API 를 아예 호출하지 않고 노선 목록만 "운행종료"로 띄운다.
빈 응답을 받으려고 쿼터를 쓸 이유가 없기 때문이다.

### 호출 예산

개발계정 한도는 **1,000건/일**. 1사이클 = 정류장 2곳 = 2콜.

| 주기 | 하루 호출 | |
|---|---|---|
| 60초 | 2,200콜 | 한도의 2.2배 (초과) |
| 150초 | 880콜 | 여유 120콜 |
| **600초** | **216콜** | 현재 값 |

오늘 쓴 건수는 `var/quota.json` 에 누적된다.

## 프로브 (API 직접 조회)

커맨드 1번 = API 호출 1번. `--dry-run` 은 호출 없이 URL 만 보여준다.

```bash
P=.venv/bin/python
$P scripts/probe.py --help                                   # 오퍼레이션 10종
$P scripts/probe.py arrivals --station 228001059 --routes 22,59
$P scripts/probe.py route-info --route 241423001
$P scripts/probe.py station-list --keyword "도담마을아이파크"
```

응답은 `tests/fixtures/` 에 저장된다. 이렇게 박제해 둔 덕에
판정 로직 개발은 쿼터 0건으로 돌아간다.

## 문제 해결

**`[Errno 98] address already in use`**
서버가 이미 떠 있다. 누가 잡고 있는지 확인하고 끈다.

```bash
ss -ltnp | grep ':8099'
kill <PID>
```

**화면 글자가 네모(□)로 나온다**
한글 폰트가 없다. `sudo apt install fonts-noto-cjk`

**`SERVICE_KEY_IS_NOT_REGISTERED_ERROR`**
`.env` 의 키가 인코딩 키이거나, 활용신청이 아직 승인 전이다.
승인 직후라면 반영까지 최대 1시간 걸린다.

**화면이 전부 `운행종료`**
운행시간(`05:50~00:10`) 밖이다. 레이아웃만 보려면 `/?demo=1`.

## 구조

```
config.yaml              정류장 2곳 · 노선 3개 · 폴링 주기
src/trueeta/
  __main__.py            python -m trueeta 진입점
  config.py              .env + config.yaml -> Settings / BoardConfig
  models.py              도메인 dataclass (GBIS 필드명은 여기까지 안 온다)
  judge.py               회차대기 판정 — 순수 함수, 이 프로젝트의 핵심
  window.py              운행시간 창 (자정을 넘는다)
  poller.py              10분 주기 조회 -> 판정 -> 보드
  state.py               최신 보드 + 차량 정체 이력
  api.py                 FastAPI · lifespan 에서 폴러 기동
  quota.py               일일 호출 카운터
  web/index.html         전광판 화면 (빌드 없음)
  gbis/
    operations.py        오퍼레이션 10종 정의 (probe CLI 가 여기서 생성된다)
    client.py            HTTP 호출 (재시도 없음 - 실패를 그대로 드러낸다)
    parser.py            실응답 -> 도메인 객체 (빈 문자열·키 부재·타입 혼재 흡수)
    envelope.py          JSON/XML 판별 · resultCode 해석 (순수 함수)
    errors.py            GbisError / GbisAuthError
scripts/probe.py         프로브 CLI
tests/                   51개 · fixtures 에 실응답 보관
```

폴러와 웹서버는 **한 프로세스**다. 10분에 2콜 규모라 나눌 이유가 없고,
나누면 IPC 왕복만 는다. 상태는 메모리로 공유한다.

## 다음 단계

- 적응형 폴링 주기 (도착 임박 시 단축)
- SQLite 판정 로그 -> `judge.stall_count` 튜닝
- `deploy/trueeta.service` (systemd) · labwc autostart 키오스크
- 야간 화면 off (`wlr-randr`)

판정 로직을 건드리기 전에 [phase1-probe.md 5장](docs/phase1-probe.md#5-실응답에서-알아낸-것)의
실응답 특성(빈 문자열·키 부재·타입 혼재)을 먼저 볼 것.
