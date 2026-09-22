# TrueETA

용인 죽전 지역 정류장 2곳의 22·25·59번 버스 도착정보를 8인치 화면에 띄우고,
**회차대기** 차량을 실제 도착시간과 구분해 보여주는 라즈베리파이 전광판.

회차지에 서 있는 버스는 지도 앱에서 "곧 도착"처럼 보인다.
특히 25번은 회차점이 정류장에서 4정거장밖에 안 떨어져 있어 착각하기 쉽다.
그걸 눈에 띄게 구분하는 것이 이 프로젝트의 목적이다.

- 설계: [docs/architecture.md](docs/architecture.md)
- **회차대기 판정 알고리즘: [docs/judge-algorithm.md](docs/judge-algorithm.md)**
- API 스펙: [docs/api-endpoints.md](docs/api-endpoints.md)
- 개발 루프(자동 재시작·쿼터): [docs/development.md](docs/development.md)
- 외부 접속·systemd: [docs/remote-access.md](docs/remote-access.md)
- 1차(프로브) 결과: [docs/phase1-probe.md](docs/phase1-probe.md)

## 현재 단계

**2차 진행 중** — 시간대별 폴링(2분/10분) + 회차대기 판정 + 웹 화면까지 동작한다.
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
.venv/bin/pytest                     # 67개, 네트워크·쿼터 0
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

코드를 고치면서 작업할 때는 자동 재시작을 켠다. 소스를 고쳐도 떠 있는 서버에는
반영되지 않기 때문이다.

```bash
TRUEETA_RELOAD=1 TRUEETA_PORT=8098 .venv/bin/python -m trueeta
```

재시작 1번마다 API 2콜을 쓴다. 주의사항은 [docs/development.md](docs/development.md).

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
| 숫자가 **회색** + `회차지 · 더 걸릴 수 있음` | 차가 기점·회차점에 있다. 확정은 아니지만 ETA 를 믿기 어렵다 |
| `운행종료` | 막차 후 |
| `—` | 노선은 도는데 지금 오는 차가 없음 |

오른쪽 위에 마지막 갱신 시각이 뜬다. 15분 넘게 안 바뀌면 주황색으로 변한다.

**남은 시간은 화면에서 1초씩 깎인다.** 서버가 길게는 10분에 한 번만 조회하므로,
조회 시각을 기준으로 보간하지 않으면 화면이 멈춰 보인다.

다만 실제 ETA 는 정차·신호 때문에 시계보다 느리게 줄어든다. 보간이 길어질수록
화면이 실제보다 빠르게(낙관적으로) 나온다 — 버스 타는 시간대의 주기를 2분으로 줄인 이유다.

## 회차대기를 어떻게 가려내는가

> 전체 알고리즘과 튜닝 절차는 [docs/judge-algorithm.md](docs/judge-algorithm.md).

회차지에 선 버스는 "4분"으로 뜨고 실제로는 15분이 걸린다. 이걸 가리는 게 목적이다.

**시간표와 비교하지 않는다 — 존재하지 않기 때문이다.** 마을버스는 정시 운행이
아니라 배차간격 운행이라 "몇 시 몇 분 도착" 데이터가 GBIS·TAGO 어디에도 없다
(고속·시외버스만 시간표 API 가 있다). 둘 다 첫차·막차·배차간격만 준다.

대신 신호 세 가지를 쓴다.

| 신호 | 언제 알 수 있나 | 세기 |
|---|---|---|
| `flag = WAIT` | 조회 1회 | 확정. 다만 마을버스에선 잘 안 온다 |
| **위치** — `staOrder − locationNo` 가 기점(1)이나 회차점(`turnSeq`) | **조회 1회** | 확정은 못 하지만 "못 믿는다"는 즉시 표시 |
| ETA 감소율 | 조회 2회 이상 | 확신을 높인다 |

**위치가 핵심이다.** 나가기 직전에 보는 화면이라 여러 번 관측할 시간이 없다.
정류장마다 회차점이 몇 정거장 전인지가 다르다.

| 노선 | `staOrder` | `turnSeq` | 회차점에 서 있으면 |
|---|---|---|---|
| 22 | 31 | 20 | `locationNo = 11` |
| **25** | **27** | **23** | **`locationNo = 4`** |
| 59 | 22 | 28 | 회차점이 뒤라 기점만 해당 |

25 번은 회차점이 4정거장 전이라 거기 선 차가 "4분"으로 뜬다.

**감소율**은 시간표를 대신한다.

```
비율 = (이전 ETA − 현재 ETA) / 경과 시간

정상 주행  0.6 ~ 1.0   시간 흐른 만큼 ETA 도 줄어든다
회차대기   0 근처       시계는 가는데 ETA 는 제자리
```

횟수가 아니라 **누적 초**로 센다. 주기가 120초/600초로 달라져 "3회 연속"이
6분일 수도 30분일 수도 있기 때문이다.

### 임계값 정하기

`stall_ratio` 와 `stall_seconds` 는 **아직 근거가 없는 추정치**다.
폴러가 `var/observations.db` 에 관측을 남기므로 (API 호출은 안 늘어난다),
며칠 쌓은 뒤 분석해서 정한다.

```bash
.venv/bin/python scripts/analyze.py --days 7
```

정상 주행과 회차지의 감소율 분포를 각각 내고, 두 분포 사이의 경계를 제안한다.
분포가 겹치면 감소율만으로는 못 가른다는 것도 알려준다.

## 설정

`config.yaml` 에서 바꾼다. 고친 뒤 서버를 재시작해야 적용된다.

| 항목 | 기본값 | 설명 |
|---|---|---|
| `stops` | 정류장 2곳 | `station_id` 와 표시할 `routes` |
| `polling.peak_window` | `08:00`~`18:00` | 이 안에서는 `interval_sec.peak` 주기 |
| `polling.interval_sec.peak` | `120` | 피크 주기(초). 2분 |
| `polling.interval_sec.far` | `600` | 그 외 주기(초). 10분 |
| `polling.service_window` | `05:50`~`00:10` | 이 밖에는 **조회하지 않는다** |
| `display.vehicles_per_route` | `2` | 노선당 표시할 차량 수 |
| `judge.stall_ratio` | `0.3` | ETA 감소율이 이보다 낮으면 '안 줄어드는 중' |
| `judge.stall_seconds` | `240` | 그 상태가 이만큼 이어지면 회차대기로 확정 |
| `judge.log_observations` | `true` | 관측을 `var/observations.db` 에 기록 |

운행시간 밖에는 API 를 아예 호출하지 않고 노선 목록만 "운행종료"로 띄운다.
빈 응답을 받으려고 쿼터를 쓸 이유가 없기 때문이다.

### 호출 예산

개발계정 한도는 **1,000건/일**. 1사이클 = 정류장 2곳 = 2콜.

실제로 버스를 타는 08:00~18:00 만 2분 주기로 돌리고, 나머지는 10분으로 둔다.

| 구간 | 길이 | 주기 | 하루 호출 |
|---|---|---|---|
| 피크 `08:00~18:00` | 36,000초 | 120초 | 600콜 |
| 그 외 운행시간 | 30,000초 | 600초 | 100콜 |
| | | | **700콜** (여유 300) |

균일 주기로는 같은 예산에서 180초(733콜)가 한계다. 운행시간 전체가 66,000초라
`하루 콜 = 132,000 / 주기` 이고, 150초면 880콜로 여유가 120콜밖에 안 남는다.
피크를 10시간으로 좁히면 그 시간대만 2분 주기를 쓸 수 있다.

한도를 넘기면 GBIS 가 거절하기 시작해 **그날 남은 시간 내내 화면이 에러**가 된다.
[quota.py](src/trueeta/quota.py) 의 카운터는 세기만 하고 막지 않으므로,
주기를 바꿀 때는 `test_daily_calls_stay_under_the_dev_quota` 가 방어선이다.

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
  poller.py              시간대별 주기 조회 -> 판정 -> 보드
  state.py               최신 보드 + 차량 정체 이력
  api.py                 FastAPI · lifespan 에서 폴러 기동
  quota.py               일일 호출 카운터
  storage.py             관측 로그 (SQLite) — 임계값 튜닝 근거
  web/index.html         전광판 화면 (빌드 없음)
  gbis/
    operations.py        오퍼레이션 10종 정의 (probe CLI 가 여기서 생성된다)
    client.py            HTTP 호출 (재시도 없음 - 실패를 그대로 드러낸다)
    parser.py            실응답 -> 도메인 객체 (빈 문자열·키 부재·타입 혼재 흡수)
    envelope.py          JSON/XML 판별 · resultCode 해석 (순수 함수)
    errors.py            GbisError / GbisAuthError
scripts/probe.py         프로브 CLI
scripts/analyze.py       관측 로그 분석 (임계값 후보 제안)
tests/                   67개 · fixtures 에 실응답 보관
```

폴러와 웹서버는 **한 프로세스**다. 많아야 2분에 2콜 규모라 나눌 이유가 없고,
나누면 IPC 왕복만 는다. 상태는 메모리로 공유한다.

## 다음 단계

- 관측 로그로 `stall_ratio`·`stall_seconds` 튜닝 (며칠 필요)
- 적응형 폴링 주기 (도착 임박 시 단축)
- `deploy/trueeta.service` (systemd) · labwc autostart 키오스크
- 야간 화면 off (`wlr-randr`)

판정 로직을 건드리기 전에 [phase1-probe.md 5장](docs/phase1-probe.md#5-실응답에서-알아낸-것)의
실응답 특성(빈 문자열·키 부재·타입 혼재)을 먼저 볼 것.
