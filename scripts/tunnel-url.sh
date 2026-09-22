#!/bin/bash
# trueeta-tunnel.service의 최근 로그에서 현재 발급된 trycloudflare.com 주소를 찾아 출력한다.
# quick tunnel은 서비스가 재시작될 때마다 주소가 바뀌므로, 확인이 필요할 때마다 이 스크립트를 실행한다.
journalctl -u trueeta-tunnel -n 200 --no-pager | grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' | tail -1
