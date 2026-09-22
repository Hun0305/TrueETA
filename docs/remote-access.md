# 외부 접속 (Cloudflare Tunnel + systemd)

집 밖 다른 기기의 브라우저에서도 전광판 웹서버(`localhost:8099`)에 접속할 수 있게, Cloudflare Tunnel로 외부 노출하고 systemd로 재부팅 후에도 자동 기동되게 만든 기록. (2026-09-22)

- 배경: `python -m trueeta`를 터미널에서 직접 실행해왔는데, 재부팅하면 서버가 안 뜨고 외부에서는 애초에 접속 불가
- Jenkins·GitHub Actions·Vercel은 CI/CD·서버리스 호스팅 도구라 이 문제(상시 실행 프로세스를 LAN 밖에 노출)와는 맞지 않아서 제외 ([architecture.md 2장](architecture.md#2-sw-구성) 참고)
- 도메인이 없어서 지금은 **Quick Tunnel**(무료, 계정 불필요, `*.trycloudflare.com`)로 진행. 재시작마다 URL이 바뀌는 게 유일한 단점

---

## 1. 구성

| systemd 서비스 | 하는 일 | 유닛 파일 |
|---|---|---|
| `trueeta.service` | `.venv/bin/python -m trueeta` 실행 (포트 8099) | [scripts/trueeta.service](trueeta.service) |
| `trueeta-tunnel.service` | cloudflared Quick Tunnel로 8099를 `trycloudflare.com` 주소에 연결. `trueeta.service` 다음에 시작하도록 `Requires=`/`After=` 설정 | [scripts/trueeta-tunnel.service](trueeta-tunnel.service) |

실제 설치본은 `/etc/systemd/system/`에 있고, 저장소의 `scripts/*.service`는 그 사본. 수정 시 둘 다 갱신하고 `sudo systemctl daemon-reload` 필요.

`cloudflared`는 GitHub 릴리스에서 arm64 deb(`cloudflared-linux-arm64.deb`)로 설치 (`/usr/bin/cloudflared`, apt 저장소 아님 → 패키지 매니저 자동 업데이트 안 됨, 버전 올리려면 새 deb 재설치).

---

## 2. 바뀐 URL 확인하기

Quick Tunnel은 계정에 묶여있지 않아서 서비스가 재시작될 때마다(=재부팅마다) 새 URL을 발급받는다. cloudflared가 시작 로그에 찍는 URL을 journalctl에서 읽어오면 된다.

```bash
bash /home/hun/TrueETA/scripts/tunnel-url.sh
# 또는 tools/cloudflare.sh 가 위 명령어를 감싼 단축 스크립트
```

내부적으로는:
```bash
journalctl -u trueeta-tunnel -n 200 --no-pager | grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' | tail -1
```

---

## 3. 운영 명령어

```bash
# 상태 확인
systemctl status trueeta.service trueeta-tunnel.service

# 재시작 (트래픽 초과·응답 이상 시)
sudo systemctl restart trueeta.service trueeta-tunnel.service

# 로그 실시간
journalctl -u trueeta.service -f
journalctl -u trueeta-tunnel.service -f
```

두 서비스 다 `enable` 되어 있어 재부팅하면 자동으로 순서대로(`trueeta` → `trueeta-tunnel`) 올라온다.

---

## 4. 향후: 도메인 생기면 고정 주소로 전환

지금은 URL이 계속 바뀌는 게 불편하면, 도메인을 구매해 Cloudflare 네임서버로 옮긴 뒤 아래 절차로 전환 가능.

1. `cloudflared tunnel login` — 브라우저에서 Cloudflare 계정 인증
2. `cloudflared tunnel create trueeta` — named tunnel 생성 (자격증명 파일 발급)
3. `cloudflared tunnel route dns trueeta eta.<도메인>` — DNS 레코드 연결
4. `trueeta-tunnel.service`의 `ExecStart`를 `cloudflared tunnel --url ...` 대신 `cloudflared tunnel run trueeta`로 교체 (config.yml에 ingress 규칙 지정)

이후로는 재부팅해도 `eta.<도메인>` 주소 고정, [scripts/tunnel-url.sh](tunnel-url.sh)는 더 이상 필요 없어짐.
