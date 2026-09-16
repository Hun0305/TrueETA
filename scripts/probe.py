#!/usr/bin/env python3
"""경기도 버스 API 프로브.

실제 응답을 최소 호출로 받아 tests/fixtures/ 에 박제하고,
config.yaml 을 채우는 데 필요한 ID 를 표로 뽑는다.

커맨드 1번 = API 호출 1번. 루프나 자동 반복은 일부러 넣지 않았다.
--dry-run 은 호출 없이 URL 만 보여준다 (쿼터 0건).

오퍼레이션 목록은 `probe.py --help`, 상세는 docs/api-endpoints.md 참고.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trueeta.config import load_settings  # noqa: E402
from trueeta.gbis import GbisAuthError, GbisClient, GbisError  # noqa: E402
from trueeta.gbis.envelope import as_list, find_key  # noqa: E402
from trueeta.gbis.operations import OPERATIONS, PARAM_FLAGS  # noqa: E402


# --- 출력 유틸 -----------------------------------------------------------


def _width(text: str) -> int:
    """한글은 터미널에서 두 칸을 먹는다."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _pad(text: str, target: int) -> str:
    return text + " " * max(0, target - _width(text))


def print_table(rows: list[dict[str, Any]], columns: tuple[str, ...] | list[str]) -> None:
    if not rows:
        print("  (항목 없음)")
        return

    def cell(row: dict[str, Any], key: str) -> str:
        # 0 을 "-" 로 지우지 않도록 None/빈문자열만 걸러낸다 (stateCd=0 은 유효값)
        value = row.get(key)
        return "-" if value is None or value == "" else str(value)

    cells = [[cell(row, c) for c in columns] for row in rows]
    widths = [
        max(_width(columns[i]), *(_width(cell[i]) for cell in cells))
        for i in range(len(columns))
    ]

    print("  " + "  ".join(_pad(c, w) for c, w in zip(columns, widths)))
    print("  " + "  ".join("-" * w for w in widths))
    for cell in cells:
        print("  " + "  ".join(_pad(v, w) for v, w in zip(cell, widths)))


def extract_items(body: dict[str, Any], op_name: str) -> list[dict[str, Any]]:
    """항목 리스트를 꺼낸다. 키 이름이 안 맞으면 msgBody 아래 첫 리스트로 대체."""
    op = OPERATIONS.get(op_name)
    if op is not None:
        found = find_key(body, op.item_key)
        if found is not None:
            return [i for i in as_list(found) if isinstance(i, dict)]

    msg_body = find_key(body, "msgBody")
    if isinstance(msg_body, dict):
        for value in msg_body.values():
            items = [i for i in as_list(value) if isinstance(i, dict)]
            if items:
                return items
    return []


# --- fixture 저장 --------------------------------------------------------


def _slug(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣_.-]+", "-", text)[:40] or "none"


def save_fixture(response, arg: str, fixture_dir: Path) -> Path:
    fixture_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    path = fixture_dir / f"{response.op}_{_slug(arg)}_{stamp}.json"
    path.write_text(
        json.dumps(response.body, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if response.was_xml:
        # 변환 로직을 나중에 검증할 수 있게 원본도 남긴다 (.gitignore 대상)
        path.with_suffix(".raw.xml").write_text(response.raw_text, encoding="utf-8")
    return path


# --- 실행 ---------------------------------------------------------------


def build_params(args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    """API 파라미터와 파일명에 쓸 인자 문자열을 만든다."""
    op = OPERATIONS[args.op]
    params = {p: getattr(args, PARAM_FLAGS[p].replace("-", "_")) for p in op.params}
    label = "_".join(str(v) for v in params.values())
    return params, label


def run(args: argparse.Namespace) -> int:
    op = OPERATIONS[args.op]
    params, label = build_params(args)

    settings = load_settings(require_key=not args.dry_run)
    client = GbisClient(
        settings.service_key,
        timeout=settings.timeout,
        quota_path=settings.quota_path,
    )

    if args.dry_run:
        print(client.display_url(op.name, params, args.path))
        print("(--dry-run: 호출하지 않았습니다)")
        return 0

    try:
        response = client.call(op.name, params, path=args.path)
    except GbisAuthError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        print(f"원문: {exc.raw}", file=sys.stderr)
        return 2
    except GbisError as exc:
        print(f"\n{exc}", file=sys.stderr)
        print(
            f"  경로를 의심한다면 --path '{op.path}' 를 다른 값으로 덮어써 보세요.",
            file=sys.stderr,
        )
        print(f"원문: {exc.raw}", file=sys.stderr)
        return 1

    print(f"\n요청: {response.url}")
    print(f"형식: {'XML -> dict 변환' if response.was_xml else 'JSON'}")

    if not args.no_save:
        saved = save_fixture(response, label, settings.fixture_dir)
        cwd = Path.cwd()
        print(f"저장: {saved.relative_to(cwd) if saved.is_relative_to(cwd) else saved}")

    if response.empty:
        print("\nresultCode=4 (결과 없음). 운행시간 밖이거나 ID가 틀렸을 수 있습니다.")
        return 0

    items = extract_items(response.body, op.name)
    if args.routes:
        wanted = {r.strip() for r in args.routes.split(",")}
        items = [i for i in items if str(i.get("routeName", "")).strip() in wanted]

    print(f"\n항목 {len(items)}개")
    if items:
        print(f"필드: {', '.join(items[0].keys())}\n")

    shown = items if args.all or len(items) <= args.limit else items[: args.limit]
    print_table(shown, op.columns)
    if len(shown) < len(items):
        print(f"  ... {len(items) - len(shown)}개 더 (전체는 --all)")

    if op.name == "route-stations":
        turns = [i for i in items if str(i.get("turnYn", "")).upper() == "Y"]
        if turns:
            print("\n회차점 (turnYn=Y):")
            print_table(turns, op.columns)

    if args.raw:
        print("\n--- 원문 ---")
        print(json.dumps(response.body, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subs = parser.add_subparsers(dest="op", required=True, metavar="오퍼레이션")

    for op in OPERATIONS.values():
        sub = subs.add_parser(op.name, help=op.help)
        for param in op.params:
            sub.add_argument(f"--{PARAM_FLAGS[param]}", required=True, help=param)
        sub.add_argument("--dry-run", action="store_true", help="호출 없이 URL만 출력")
        sub.add_argument("--no-save", action="store_true", help="fixture 저장 생략")
        sub.add_argument("--raw", action="store_true", help="응답 원문도 출력")
        sub.add_argument("--path", help="엔드포인트 경로 덮어쓰기")
        sub.add_argument("--routes", help="routeName 필터 (예: 22,25,59)")
        sub.add_argument("--limit", type=int, default=30, help="표에 출력할 최대 행 수")
        sub.add_argument("--all", action="store_true", help="모든 행 출력")

    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
