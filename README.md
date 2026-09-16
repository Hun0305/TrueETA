# TrueETA

용인 죽전 지역 정류장 2곳의 22·25·59번 버스 도착정보를 8인치 화면에 띄우고,
**회차대기** 차량을 실제 도착시간과 구분해 보여주는 라즈베리파이 전광판.

- 설계: [docs/architecture.md](docs/architecture.md)
- API 스펙: [docs/api-endpoints.md](docs/api-endpoints.md)
- 1차 결과: [docs/phase1-probe.md](docs/phase1-probe.md)

## 현재 단계

**1차 완료** — API 3종 승인, 실호출 검증, 정류장·노선 ID 확보, `config.yaml` 작성.
화면과 판정 로직은 2차.

| 정류장 | stationId | 노선 |
|---|---|---|
| 도담마을아이파크.죽전휴먼빌 | `228001059` | 22(미금역 방향) · 59(수지구청 방향) |
| 대일초교후문 | `228003549` | 25(미금역 방향) |

## 설치

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env     # SERVICE_KEY 에 공공데이터포털 '디코딩' 키
.venv/bin/pytest         # 네트워크·쿼터 0
```

## 프로브

커맨드 1번 = API 호출 1번. `--dry-run` 은 호출 없이 URL 만 보여준다.

```bash
P=.venv/bin/python
$P scripts/probe.py --help                                   # 오퍼레이션 10종
$P scripts/probe.py arrivals --station 228001059 --routes 22,59
$P scripts/probe.py route-info --route 241423001
$P scripts/probe.py station-list --keyword "도담마을아이파크"
```

응답은 `tests/fixtures/` 에 저장되고, 오늘 쓴 호출 건수는 `var/quota.json` 에 누적된다.
개발계정 한도는 **1,000건/일**이다.

## 구조

```
config.yaml              정류장 2곳 · 노선 3개 · 폴링 주기
src/trueeta/
  config.py              .env + config.yaml -> Settings / BoardConfig
  quota.py               일일 호출 카운터
  gbis/
    operations.py        오퍼레이션 10종 정의 (CLI 가 여기서 생성된다)
    client.py            HTTP 호출 (재시도 없음 - 실패를 그대로 드러낸다)
    envelope.py          JSON/XML 판별 · resultCode 해석 (순수 함수)
    errors.py            GbisError / GbisAuthError
scripts/probe.py         프로브 CLI
tests/                   18개 · fixtures 에 실응답 보관
```

## 다음 단계

`models.py` · `gbis/parser.py` · `judge.py`(순수 함수) · `poller.py` ·
`api.py` · `web/` · `deploy/trueeta.service`.

파서를 쓰기 전에 [phase1-probe.md 5장](docs/phase1-probe.md#5-실응답에서-알아낸-것)의
실응답 특성(빈 문자열·키 부재·타입 혼재)을 먼저 볼 것.
