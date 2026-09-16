# GBIS API 엔드포인트 레퍼런스

경기도 버스정보시스템(GBIS) Open API v2. 공공데이터포털 Swagger 스펙 정리.
설계 배경은 [architecture.md](architecture.md), 프로브 사용법은 [phase1-probe.md](phase1-probe.md).

공통 쿼리 파라미터: `serviceKey`(디코딩 키) · `format`(json/xml)
공통 `resultCode`: `0` 정상 · `1` 시스템 에러 · `2` 필수 파라미터 없음 · `4` 결과 없음(에러 아님)

---

## 1. 정류소 조회 (`busstationservice/v2`)

| API | 경로 | 파라미터 | 용도 |
|---|---|---|---|
| 정류소명/번호 목록조회 | `getBusStationListv2` | `keyword` | 정류소명·번호로 `stationId` 검색 |
| 주변정류소 목록조회 | `getBusStationAroundListv2` | `x`, `y` (WGS84) | 좌표 반경 500m 내 정류소 목록 |
| 정류소경유노선 목록조회 | `getBusStationViaRouteListv2` | `stationId` | 해당 정류소를 지나는 모든 노선 |
| 정류소정보항목조회 | `busStationInfov2` | `stationId` | 정류소 상세 정보(단건) |

응답 루트: `msgBody.busStationList` / `busStationAroundList` / `busRouteList` / `busStationInfo`

주요 필드: `stationId`, `stationName`, `mobileNo`(5자리 정류소번호), `regionName`, `centerYn`(중앙차로 여부), `x`/`y`

> `getBusStationAroundListv2`만 응답에 `distance` 추가.
> `getBusStationViaRouteListv2` 응답은 노선 목록(`routeId`, `routeName`, `staOrder` 등) — 정류소 자체 정보가 아님.

---

## 2. 버스노선 조회 (`busrouteservice/v2`)

| API | 경로 | 파라미터 | 용도 |
|---|---|---|---|
| 노선번호목록조회 | `getBusRouteListv2` | `keyword` | 노선번호로 `routeId` 검색 |
| 노선정보항목조회 | `getBusRouteInfoItemv2` | `routeId` | 노선 기본정보 + 배차간격 |
| 경유정류소목록조회 | `getBusRouteStationListv2` | `routeId` | 노선이 지나는 정류소 순서 목록 |
| 노선형상정보목록조회 | `getBusRouteLineListv2` | `routeId` | 노선 좌표(폴리라인) |

응답 루트: `msgBody.busRouteList` / `busRouteInfoItem` / `busRouteStationList` / `busRouteLineList`

주요 필드:
- `getBusRouteInfoItemv2`: `routeName`, `routeTypeName`, `startStationName`/`endStationName`, `turnStID`/`turnStNm`(회차 정류소), 요일별 첫차/막차·배차간격(`upFirstTime`, `peekAlloc`, `satPeekAlloc`, `sunPeekAlloc` 등)
- `getBusRouteStationListv2`: `stationId`, `stationName`, `stationSeq`(정류소 순번), **`turnSeq`(회차 순번)**, `turnYn`(회차 여부)

> `staOrder`(도착정보 쪽 필드명)와 `stationSeq`(경유정류소 쪽 필드명)는 같은 개념, 이름만 다르다.

---

## 3. 버스도착정보 조회 (`busarrivalservice/v2`)

| API | 경로 | 파라미터 | 용도 |
|---|---|---|---|
| 버스도착정보목록조회 | `getBusArrivalListv2` | `stationId` | 정류소에 정차하는 **모든 노선**의 도착정보 |
| 버스도착정보항목조회 | `getBusArrivalItemv2` | `stationId`, `routeId`, `staOrder` | **특정 노선 1개**의 도착정보 |

응답 루트: `msgBody.busArrivalList` / `busArrivalItem`

주요 필드 (1번째/2번째 도착 예정 차량 각각 `1`/`2` 접미사):
- `routeId`, `routeName`, `routeDestName`(종점) — 노선 식별
- `staOrder`, **`turnSeq`**, **`flag`**(예: `RUN`/`WAIT` — 회차대기 판정의 핵심 필드)
- `predictTime1`(분 단위 ETA), `predictTimeSec1`(초 단위 ETA)
- `locationNo1`(현재 위치 정류소 순번), `vehId1`, `plateNo1`(차량번호)
- `remainSeatCnt1`, `crowded1`(혼잡도), `lowPlate1`(저상버스 여부), `taglessCd1`, `stateCd1`

> `getBusArrivalItemv2`는 `getBusArrivalListv2`의 결과를 특정 노선으로 좁힌 것 — 필드는 거의 동일하되 `staOrder`를 요청 파라미터로 직접 넘겨야 한다(노선의 정류소 순번을 미리 알아야 호출 가능).

---

## 4. 이 프로젝트에서 쓰는 조합

목표: 정류장 2곳 × 22·25·59번 도착정보 + 회차대기(`flag`/`turnSeq`) 판정.

1. `getBusStationListv2` (keyword) → 두 정류장의 `stationId` 확보
2. `getBusArrivalListv2` (stationId) → 노선별 `routeId`, `staOrder`, `turnSeq`, `flag` 한 번에 확보
3. `getBusRouteInfoItemv2` (routeId) → 회차 정류소(`turnStID`/`turnStNm`), 배차간격 참고용
4. `getBusRouteStationListv2` (routeId) → 전체 경유 정류소의 `turnSeq` 분포 확인(회차 판정 임계값 튜닝 근거)

`getBusStationAroundListv2` · `getBusStationViaRouteListv2` · `getBusRouteListv2` · `getBusRouteLineListv2` · `getBusArrivalItemv2`는 현재 스코프에서 미사용 (좌표 기반 검색, 지도 표시, 단건 조회는 이 프로젝트(고정 정류장 2곳)엔 불필요).
