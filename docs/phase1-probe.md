# 1차 — 프로젝트 기반 + API 프로브

작성 2026-09-16 · 설계는 [architecture.md](architecture.md), 엔드포인트는 [api-endpoints.md](api-endpoints.md)

**결과: 실호출 16건으로 필요한 값을 전부 확보했다. `config.yaml` 작성 완료.**

---

## 1. 왜 프로브부터인가

화면도 판정 로직도 만들지 않았다. 1차 목표는 하나였다:
**실제 API 응답을 최소 호출로 받아 `tests/fixtures/` 에 박제한다.**

- **모르는 값이 있었다.** 정류장 2곳의 `stationId`, 22·25·59번의 `routeId`·`staOrder`·`turnSeq`
- **쿼터가 빠듯하다.** 개발계정 1,000건/일. 판정 로직을 고칠 때마다 실 API 를 때릴 수 없다

fixture 를 박제해 둔 덕에 이후 파서·판정 개발은 쿼터 0건으로 돈다.

## 2. 결정사항

| 항목 | 결정 | 이유 |
|---|---|---|
| 패키지명 | `trueeta` | 저장소 이름과 통일. architecture.md 의 `bus-board` 표기는 이쪽으로 맞춘다 |
| 레이아웃 | `src/` | import 경로가 설치 여부와 무관하게 고정 |
| 프로세스 | 폴러 + 웹서버 한 프로세스 (2차) | 2.5분에 2회 호출 규모. 나누면 IPC 왕복만 는다 |
| 재시도 | 안 함 | 실패를 드러내는 게 원인 파악에 낫고 쿼터도 아낀다. 재시도는 폴러 층의 일 |
| 오퍼레이션 정의 | `operations.py` 한 곳 | 경로·파라미터·응답키·표컬럼을 묶어 두면 CLI 가 자동 생성된다 |

## 3. 폴더 구조

`*` 가 1차에서 만든 것.

```
TrueETA/
├── pyproject.toml            *  deps: httpx · python-dotenv · PyYAML
├── config.yaml               *  정류장 2곳 · 노선 3개 (실제 ID)
├── .env                         서비스키 (gitignore)
├── .env.example              *
├── README.md                 *
├── docs/
│   ├── architecture.md          설계
│   ├── api-endpoints.md         GBIS 오퍼레이션 10종 스펙
│   └── phase1-probe.md       *  이 문서
│
├── src/trueeta/
│   ├── config.py             *  .env + config.yaml -> Settings / BoardConfig
│   ├── quota.py              *  일일 호출 카운터
│   └── gbis/                 *  ── 외부 경계
│       ├── operations.py     *    오퍼레이션 10종 정의 (단일 출처)
│       ├── client.py         *    HTTP 호출
│       ├── envelope.py       *    JSON/XML 판별 · resultCode 해석 (순수 함수)
│       └── errors.py         *    GbisError / GbisAuthError
│
├── scripts/probe.py          *  프로브 CLI (서브커맨드 자동 생성)
├── tests/                    *  18개 통과 · fixtures 에 실응답 보관
│
│   ── 2차 예정 ──
├── src/trueeta/models.py        도메인 dataclass
├── src/trueeta/judge.py         회차대기 판정 (순수 함수)
├── src/trueeta/poller.py        적응형 주기 루프
├── src/trueeta/api.py           GET /api/board
├── src/trueeta/web/             키오스크 화면
└── deploy/trueeta.service
```

설계 의도:

- **`gbis/` 격리** — 파서가 도메인 객체만 뱉게 해두면 TAGO 를 백업 소스로 붙일 때 `tago/` 를 옆에 두면 된다
- **`judge.py` 는 I/O 없는 순수 함수로** — 임계값을 계속 고칠 부분이라 fixture 만으로 반복 테스트가 돼야 한다
- **`envelope.py` 분리** — 봉투 해석은 HTTP 없이 테스트돼야 한다
- **런타임 데이터는 `var/`** — 배포 시 systemd `StateDirectory`

## 4. 확보한 값

### 정류장

| 정류장 | stationId | 표지판번호 | 노선 |
|---|---|---|---|
| 도담마을아이파크.죽전휴먼빌 | `228001059` | 29272 | 22(미금역 방향) · 59(수지구청 방향) |
| 대일초교후문 | `228003549` | 56580 | 25(미금역 방향) |

두 정류장 모두 **양방향 쌍**이 존재한다. 반대편은 `228001207` / `228003550` 이고,
`routeDestName`(진행방향 종점)으로 방향을 확정했다.

### 노선

| 노선 | routeId | 기점 → 회차점 | staOrder | turnSeq | 구간 | 첫차~막차(평일) | 배차 |
|---|---|---|---|---|---|---|---|
| 22 | `241423001` | 미금역 → ㈜다우 | 31 | 20 | **회차 후** | 06:00~23:55 | 15/25분 |
| 59 | `241423008` | 단국대차고지 → 수지구청 | 22 | 28 | **회차 전** | 06:30~23:30 | 14/15분 |
| 25 | `241428003` | 미금역 → 꽃메마을현대4차4단지 | 27 | 23 | **회차 후** | 06:30~23:45 | 15/20분 |

셋 다 용인 마을버스이고 회사는 한비운수(22·59) / 죽전운수(25).

### 이 구조가 판정 로직에 주는 의미

`위치 순번 = staOrder − locationNo1` 이 `1`(기점) 또는 `turnSeq`(회차점)이면 정차 중이다.
우리 세 조합에 대입하면 **대기 차량이 몇 정거장 전으로 보이는지가 노선마다 다르다**:

| 노선 | 회차점 대기 시 | 기점 대기 시 |
|---|---|---|
| 22 (staOrder 31, turnSeq 20) | `locationNo1 = 11` | `locationNo1 = 30` |
| 25 (staOrder 27, turnSeq 23) | **`locationNo1 = 4`** | `locationNo1 = 26` |
| 59 (staOrder 22, turnSeq 28) | 해당 없음 (회차점이 우리 뒤) | `locationNo1 = 21` |

**25번이 이 프로젝트의 핵심 사례다.** 회차점이 우리 정류장에서 겨우 4정거장 전이라,
회차지에 서 있는 차가 화면에는 "곧 도착"처럼 보인다. 이게 원래 만들려던 이유 그대로다.

공식이 세 경우 모두에 그대로 들어맞으므로 **노선별 하드코딩은 필요 없다.**

## 5. 실응답에서 알아낸 것

매뉴얼에 없거나 다른 것들. 파서를 쓰기 전에 알아야 한다.

### 빈 값은 `null` 이 아니라 빈 문자열

응답 전체에 `None` 이 하나도 없다. 차량이 없으면 `predictTime1: ""`, `vehId1: ""` 이다.
`is None` 으로 검사하면 전부 통과해 버린다.

### 차량이 없으면 키 자체가 사라진다

| 상태 | 키 개수 | `predictTimeSec1` | `stateCd1` |
|---|---|---|---|
| 1호차만 있음 | 29 | 있음 | 있음 |
| 1·2호차 있음 | 31 | 있음 (+`predictTimeSec2`, `stateCd2`) | 있음 |
| 차량 없음 | 27 | **없음** | **없음** |

`[...]` 접근은 KeyError 로 죽는다. 전부 `.get()` 이어야 한다.

### `flag` 는 노선 단위 운행상태이지 차량 상태가 아니다

차량이 하나도 없는 57A 도 `flag: PASS` 였다. 즉 `flag` 만으로는 회차대기를 판정할 수 없고,
**"도착정보 있음?" 을 먼저 봐야 한다.** architecture.md 3장 흐름도가 이미 그 순서라 그대로 쓰면 된다.

### 타입이 섞여 있다

`routeName` 이 숫자 노선은 `int 22`, 가지 노선은 `str "57A"` 다.
`routeId`·`stationId`·`vehId1` 도 `int` 로 온다.
설정 파일은 전부 문자열이므로 비교 전에 `config.route_key()` 로 정규화한다.

### 매뉴얼에 없던 필드

`crowded1·2`(혼잡도), `remainSeatCnt1·2`(빈자리, 마을버스는 `-1`),
`lowPlate1·2`(저상), `taglessCd1·2`, `routeDestId`, `routeTypeCd`.

`routeDestName` 은 그 방향의 종점 이름이라 **양방향 정류장에서 방향을 가르는 데 쓸 수 있다.**
실제로 이걸로 우리 정류장 방향을 확정했다.

### 서비스키

발급된 키가 64자리 16진수라 인코딩/디코딩 값이 같다.
이중 인코딩 문제는 이 키에선 발생하지 않는다. (다른 키로 바꾸면 다시 주의해야 한다.)

## 6. 쿼터 예산

```
운행창 05:50~00:10 = 1,100분
1사이클 = 정류장 2곳 = 2콜

far= 60s -> 2,200콜/일   한도의 2.2배
far=120s -> 1,100콜/일   초과
far=150s ->   880콜/일   여유 120콜   <- 채택
far=180s ->   732콜/일   여유 268콜
```

한도에 딱 맞는 주기는 **132초**. 여유를 두고 `far=150s`, `near=60s` 로 잡았다.
화면이 `predictTimeSec` 를 1초씩 깎아 보여주므로 2.5분 주기여도 끊겨 보이지 않는다.

운영계정 트래픽 증가가 승인되면 `far=60` / `near=20` 으로 낮춘다.

## 7. 프로브 사용법

커맨드 1번 = API 호출 1번. 루프나 자동 반복은 넣지 않았다.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env          # 디코딩 키 입력

P=.venv/bin/python
$P scripts/probe.py --help                              # 오퍼레이션 10종
$P scripts/probe.py arrivals --station 228001059 --dry-run   # URL만, 쿼터 0
$P scripts/probe.py arrivals --station 228001059 --routes 22,59
```

옵션: `--dry-run` `--no-save` `--raw` `--path` `--routes` `--limit` `--all`

서브커맨드는 `operations.py` 에서 자동 생성된다. 오퍼레이션을 추가하려면 거기만 고치면 된다.

응답은 `tests/fixtures/<op>_<인자>_<시각>.json`, 호출 건수는 `var/quota.json` 에 누적.

## 8. 검증 상태

**확인함**

- 테스트 18개 통과 (네트워크 0)
- 실호출 16건 — 3개 API 전부 정상 응답, JSON 형식
- 정류장·노선 ID 확보, `config.yaml` 작성, 실 fixture 로 필터 동작 확인
- 오퍼레이션 10종 URL 조립 (`--dry-run`)
- `resultCode=4` 통과, 시스템 에러는 예외, 서비스키 거부는 exit 2
- 표 렌더링 — 한글 폭 정렬, `stateCd=0` 같은 falsy 값 보존

**아직 못 함**

- XML 응답 경로는 **실제로 만나지 못했다.** 세 API 모두 `format=json` 을 존중했다.
  변환 코드는 합성 샘플로만 테스트된 상태다
- `flag=WAIT` 실물을 아직 못 봤다. 판정 로직 튜닝 전에 회차 시간대 로그가 필요하다
- 운행 종료 시간대(`flag=STOP`, `resultCode=4`) 응답 미수집

## 9. 열린 이슈

- [ ] 운영계정 트래픽 증가 신청 → `far` 를 60초로 낮출 수 있다
- [ ] `flag=WAIT` 실물 수집 (회차 시간대에 25번 관찰이 가장 유력)
- [ ] 회차대기 추정 임계값 `stall_count` 튜닝 (현재 3, 근거 없음)
- [ ] 서비스키 거부로 실패한 호출도 쿼터 카운터가 1 올린다.
      실제로는 GBIS 에 도달하지 못해 한도를 안 먹지만 과다 계상이 안전한 방향이라 뒀다
- [ ] `GbisAuthError` 는 포털 층 에러인데 `Gbis` 접두사가 붙어 있다 → `PortalAuthError` 가 정확

## 10. 다음 단계 (2차)

1. `models.py` + `gbis/parser.py` — 5장의 실응답 특성(빈 문자열·키 부재·타입 혼재) 반영
2. `judge.py` — architecture.md 3장 흐름도, 순수 함수
3. `poller.py` + `state.py` — `far=150s` 적응형, FastAPI lifespan 의 asyncio 태스크
4. `api.py` + `web/` — `GET /api/board`, 키오스크 화면
5. `deploy/trueeta.service` — `StateDirectory=trueeta`

`stall_count` 튜닝은 SQLite 로그가 쌓인 뒤.
