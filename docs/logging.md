# 로그 — 어디에 남고 왜 파일로도 남기나

## 요약

| 어디 | 내용 | 재부팅하면 |
|---|---|---|
| **`var/logs/trueeta.log`** | 앱 로그 + uvicorn 오류. 날짜 포함 | **남는다** (30일 보관) |
| **`var/logs/access.log`** | 화면·폰이 부른 HTTP 접속 | **남는다** (7일 보관) |
| journald (`journalctl -u trueeta`) | 위 둘 + systemd 기동/중지 | **사라진다** |
| `var/observations.db` | 관측·판정 기록 (로그가 아니라 데이터) | 남는다 |
| `var/quota.json` | 날짜별·오퍼레이션별 호출 건수 | 남는다 |

```bash
tail -f var/logs/trueeta.log                        # 실시간
grep "사이클" var/logs/trueeta.log | tail           # 폴러가 도는지
grep -E "WARNING|ERROR" var/logs/trueeta.log*       # 문제만, 교체된 파일까지
ls var/logs/                                        # trueeta.log.2026-09-28 처럼 날짜별
```

---

## 원인 — journald 가 메모리에만 있다

9/24 사고 조사 때 `journalctl --list-boots` 가 부팅 1개만 보여줘서
"어제 라즈베리파이가 안 켜져 있었다" 고 잘못 결론 낸 적이 있다
([incident-2026-09-24.md](incident-2026-09-24.md)). 그 뒤로도 재부팅마다
이전 기록이 사라졌다.

원인은 라즈베리파이 OS 의 기본 설정이었다.

```
/usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf
    Storage=volatile

실제 저장 위치   /run/log/journal   (메모리, 36MB)
/var/log/journal                    (비어 있음, 4KB)
```

**SD 카드 수명을 아끼려고 저널을 메모리에만 둔다.** 재부팅하면 통째로 사라진다.

> 예전에 `/var/log/journal` 디렉터리가 **있는 것만** 보고 "영구 저장이다" 라고
> 말했는데 틀렸다. 디렉터리는 비어 있었고, 실제로는 드롭인 설정이 volatile 로
> 덮고 있었다. 디렉터리 존재가 아니라 `journalctl --header` 의 `File path` 를
> 봐야 한다.

---

## 행동 — 앱이 직접 파일로 남긴다

시스템 설정(journald)을 바꾸는 대신 **우리 서비스 로그만** 파일로 남기기로 했다.

- sudo 가 필요 없다
- SD 쓰기를 전역으로 늘리지 않는다 (다른 서비스 로그까지 영구 저장하지 않음)
- 저장소에 코드로 남아 다른 기기로 옮겨도 그대로 동작한다

구현은 [src/trueeta/logfile.py](../src/trueeta/logfile.py).
서비스 기동 시(`api.lifespan`) 붙는다.

### 설계에서 정한 것

**날짜별 교체, 보관 제한.** 자정마다 새 파일로 넘기고 앱 로그는 30일,
접속 로그는 7일만 둔다. SD 카드를 채우면 안 된다. 앱 로그는 하루 수백 줄
(사이클 로그가 2~10분에 한 줄)이라 30일이어도 수 MB 다.

**접속 로그 분리.** 화면이 15초마다 `/api/board` 를 불러 하루 5,700줄이 넘는다.
한 파일에 섞으면 폴러 로그가 묻힌다. 볼 일이 드물어서 보관도 짧다.

**서비스키 가리기.** 9/29 에 journald 에서 키가 290줄 발견됐다 (httpx 가 INFO
에서 요청 URL 을 남겼다). httpx 는 WARNING 으로 막아뒀지만, 파일은 오래 남으므로
모든 핸들러에 `serviceKey=...` → `serviceKey=***` 필터를 한 겹 더 걸었다.
journald 로 가는 출력에도 같은 필터가 걸린다.

**실패해도 서비스는 돈다.** 로그 디렉터리를 못 만들면 경고만 남기고 파일 없이
계속한다. 로그 때문에 전광판이 죽으면 안 된다 (관측 로그와 같은 원칙).

**테스트 샌드박스를 따른다.** 위치는 `var_dir` 아래라서 `TRUEETA_VAR_DIR` 을
따른다. 테스트는 실제 `var/logs/` 에 쓰지 않는다.

---

## 결과

샌드박스로 서버를 띄워 확인했다.

```
var/logs/trueeta.log
  2026-09-29 00:55:01 INFO    trueeta | 로그 파일: .../trueeta.log (journald 는 재부팅하면 사라진다)
  2026-09-29 00:55:01 INFO    trueeta | 프리셋 2개 (기본 '집앞'), 정류장 2곳, ...
  2026-09-29 00:55:01 INFO    trueeta.poller | 운행시간 밖 (05:50~00:10) — 조회 중지
  2026-09-29 00:55:01 INFO    uvicorn.error | Uvicorn running on http://0.0.0.0:8097

var/logs/access.log
  2026-09-29 00:55:02 INFO    uvicorn.access | 127.0.0.1:33956 - "GET /api/board HTTP/1.1" 200
```

journald 출력도 그대로 나온다 (`journalctl -u trueeta -f` 는 계속 쓸 수 있다).

테스트 7개: 파일에 날짜가 찍히는지, 서비스키가 가려지는지, 접속 로그가 분리되는지,
두 번 붙여도 줄이 중복되지 않는지, 교체·보관 설정, 못 쓰는 위치에서 안 죽는지.

### 한계

- uvicorn 이 lifespan **전에** 찍는 두 줄(`Started server process`,
  `Waiting for application startup`)은 파일에 안 남는다. 파일 핸들러가 lifespan
  에서 붙기 때문이다. 기동 여부는 바로 뒤의 `로그 파일:` 줄로 알 수 있다
- systemd 자체의 기동·중지 기록(`Started trueeta.service`)은 journald 에만 있다.
  재부팅 이력은 파일 로그의 `로그 파일:` 줄(= 기동 시각)로 대신 본다

---

## 선택 — journald 자체를 영구로 돌리려면

파일 로그로 충분하지만, 시스템 전체 로그(부팅·커널·다른 서비스)까지 남기고
싶으면 드롭인으로 덮을 수 있다. **SD 쓰기가 늘어난다는 점**을 감안할 것.

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=persistent\nSystemMaxUse=100M\n' \
  | sudo tee /etc/systemd/journald.conf.d/50-persistent.conf
sudo systemctl restart systemd-journald
journalctl --header | grep "File path"     # /var/log/journal/... 이면 성공
```

`/etc` 쪽 드롭인이 `/usr/lib` 쪽(`40-rpi-volatile-storage.conf`)보다 우선한다.
