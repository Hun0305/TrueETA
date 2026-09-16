"""GBIS Open API v2 오퍼레이션 정의.

경로·파라미터·응답 항목 키·표 컬럼을 한 곳에 모은다.
client 와 probe 가 같은 표를 보게 해서, 오퍼레이션을 추가할 때
고칠 곳이 여기 하나가 되도록 했다.

출처: docs/api-endpoints.md (공공데이터포털 Swagger 스펙 정리)
"""

from __future__ import annotations

from dataclasses import dataclass

#: API 파라미터 이름 -> CLI 플래그 이름 (argparse dest 는 - 를 _ 로 바꾼 것)
PARAM_FLAGS = {
    "keyword": "keyword",
    "stationId": "station",
    "routeId": "route",
    "staOrder": "sta-order",
    "x": "x",
    "y": "y",
}


@dataclass(frozen=True)
class Operation:
    name: str  # CLI 서브커맨드
    path: str  # busstationservice/v2/... 형태
    params: tuple[str, ...]  # 필수 API 파라미터
    item_key: str  # msgBody 아래 항목 키
    columns: tuple[str, ...]  # 요약표 컬럼
    help: str

    @property
    def service(self) -> str:
        return self.path.split("/")[0]


_STATION_COLS = ("stationId", "stationName", "mobileNo", "regionName", "centerYn", "x", "y")
_ARRIVAL_COLS = (
    "routeName", "routeId", "staOrder", "turnSeq", "flag",
    "locationNo1", "predictTime1", "stateCd1", "vehId1", "plateNo1",
    "locationNo2", "predictTime2",
)

_OPERATIONS = (
    # --- 정류소 조회 -------------------------------------------------
    Operation(
        "station-list", "busstationservice/v2/getBusStationListv2",
        ("keyword",), "busStationList", _STATION_COLS,
        "정류소명·번호로 검색 -> stationId",
    ),
    Operation(
        "station-around", "busstationservice/v2/getBusStationAroundListv2",
        ("x", "y"), "busStationAroundList",
        ("stationId", "stationName", "mobileNo", "distance", "x", "y"),
        "좌표 반경 500m 정류소 (이름을 모를 때)",
    ),
    Operation(
        "station-routes", "busstationservice/v2/getBusStationViaRouteListv2",
        ("stationId",), "busRouteList",
        ("routeId", "routeName", "routeTypeName", "staOrder", "routeDestName"),
        "정류소를 지나는 모든 노선",
    ),
    Operation(
        "station-info", "busstationservice/v2/busStationInfov2",
        ("stationId",), "busStationInfo", _STATION_COLS,
        "정류소 상세 (단건)",
    ),
    # --- 버스노선 조회 -----------------------------------------------
    Operation(
        "route-list", "busrouteservice/v2/getBusRouteListv2",
        ("keyword",), "busRouteList",
        ("routeId", "routeName", "routeTypeName", "regionName",
         "startStationName", "endStationName"),
        "노선번호로 검색 -> routeId",
    ),
    Operation(
        "route-info", "busrouteservice/v2/getBusRouteInfoItemv2",
        ("routeId",), "busRouteInfoItem",
        ("routeName", "routeTypeName", "startStationName", "endStationName",
         "turnStID", "turnStNm", "upFirstTime", "upLastTime",
         "peekAlloc", "nPeekAlloc"),
        "노선 기본정보 + 첫차/막차 + 배차간격 + 회차정류소",
    ),
    Operation(
        "route-stations", "busrouteservice/v2/getBusRouteStationListv2",
        ("routeId",), "busRouteStationList",
        ("stationSeq", "stationId", "stationName", "turnYn", "turnSeq"),
        "노선 경유 정류소 순서 목록",
    ),
    Operation(
        "route-line", "busrouteservice/v2/getBusRouteLineListv2",
        ("routeId",), "busRouteLineList", ("x", "y"),
        "노선 폴리라인 좌표 (현재 미사용)",
    ),
    # --- 버스도착정보 조회 -------------------------------------------
    Operation(
        "arrivals", "busarrivalservice/v2/getBusArrivalListv2",
        ("stationId",), "busArrivalList", _ARRIVAL_COLS,
        "정류소의 모든 노선 도착정보 (주력)",
    ),
    Operation(
        "arrival-item", "busarrivalservice/v2/getBusArrivalItemv2",
        ("stationId", "routeId", "staOrder"), "busArrivalItem", _ARRIVAL_COLS,
        "특정 노선 1개 도착정보 (staOrder 를 미리 알아야 함)",
    ),
)

OPERATIONS: dict[str, Operation] = {op.name: op for op in _OPERATIONS}
