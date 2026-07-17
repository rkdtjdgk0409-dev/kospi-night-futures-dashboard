from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


SOURCE_URL = "https://www.hangon.co.kr/kospi-night-futures"
PUBLISHED_DATA_URL = (
    "https://rkdtjdgk0409-dev.github.io/"
    "kospi-night-futures-dashboard/data.json"
)
OUTPUT_PATH = Path("data.json")
KST = ZoneInfo("Asia/Seoul")

# Next.js가 서버에서 내려주는 Flight 데이터의 문자열 부분을 읽습니다.
PUSH_PATTERN = re.compile(
    r'self\.__next_f\.push\(\[1,'
    r'("(?:\\.|[^"\\])*")'
    r'\]\)</script>'
)

LABEL_PATTERN = re.compile(
    r"(?P<month>\d{1,2})\.\s*"
    r"(?P<day>\d{1,2})\.\s*"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})"
)


def clean_number(value: Any) -> float:
    text = str(value or "")
    text = text.replace(",", "").replace("%", "")
    text = text.replace("−", "-").replace("＋", "+")
    text = re.sub(r"[^0-9.+-]", "", text)

    if text in {"", "+", "-", ".", "+.", "-."}:
        raise ValueError(f"숫자로 바꿀 수 없습니다: {value!r}")

    return float(text)


def fetch_text(url: str, timeout: int = 45) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/145.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/json;"
                "q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
            "Cache-Control": "no-cache",
        },
    )

    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def walk(value: Any) -> Iterable[Any]:
    yield value

    if isinstance(value, dict):
        for child in value.values():
            yield from walk(child)

    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def iter_flight_payloads(page_html: str) -> Iterable[Any]:
    """Next.js script 문자열을 정상 JSON으로 두 번 해제합니다."""
    for match in PUSH_PATTERN.finditer(page_html):
        try:
            decoded = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue

        separator = decoded.find(":")

        if separator < 0:
            continue

        try:
            yield json.loads(decoded[separator + 1 :])
        except json.JSONDecodeError:
            continue


def find_night_futures_data(page_html: str) -> dict[str, Any]:
    for payload in iter_flight_payloads(page_html):
        for value in walk(payload):
            if not isinstance(value, dict):
                continue

            if (
                value.get("name") == "코스피 야간선물"
                and isinstance(value.get("history"), list)
            ):
                return value

    raise RuntimeError(
        "원본 페이지의 Next.js 야간선물 데이터를 찾지 못했습니다."
    )


def infer_year(
    month: int,
    day: int,
    reference: datetime,
) -> int:
    year = reference.year
    candidate = datetime(year, month, day, tzinfo=KST)

    # 12월 말~1월 초에 연도가 바뀌는 경우를 보정합니다.
    if candidate - reference > timedelta(days=180):
        year -= 1
    elif reference - candidate > timedelta(days=180):
        year += 1

    return year


def parse_history(
    history: list[Any],
    reference: datetime,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for item in history:
        if not isinstance(item, dict):
            continue

        label = str(item.get("label") or "").strip()

        if not label:
            label = (
                f"{item.get('fullDate', '')} "
                f"{item.get('time', '')}"
            ).strip()

        match = LABEL_PATTERN.search(label)

        if not match:
            continue

        try:
            month = int(match.group("month"))
            day = int(match.group("day"))
            hour = int(match.group("hour"))
            minute = int(match.group("minute"))
            price = clean_number(item.get("value"))
            timestamp = datetime(
                infer_year(month, day, reference),
                month,
                day,
                hour,
                minute,
                tzinfo=KST,
            )
        except (TypeError, ValueError):
            continue

        rows.append(
            {
                "time": timestamp.isoformat(),
                "price": round(price, 4),
            }
        )

    return rows


def is_trading_time(reference: datetime) -> bool:
    return reference.hour >= 18 or reference.hour < 6


def build_payload() -> dict[str, Any]:
    reference = datetime.now(KST)
    cache_buster = int(reference.timestamp())
    page_html = fetch_text(f"{SOURCE_URL}?t={cache_buster}")
    source = find_night_futures_data(page_html)
    rows = parse_history(source["history"], reference)

    if len(rows) < 10:
        raise RuntimeError(
            "원본 시계열이 너무 짧아 정상 데이터로 판단하지 않았습니다."
        )

    price = clean_number(source.get("value"))
    change_rate = clean_number(source.get("changePercent"))
    raw_change = abs(clean_number(source.get("change")))
    change = raw_change * (
        -1 if change_rate < 0 else 1 if change_rate > 0 else 0
    )

    return {
        "title": "코스피 야간선물",
        "statusText": (
            "야간 거래 시간 중 실시간 제공"
            if is_trading_time(reference)
            else "야간 거래 종료 후 최종값"
        ),
        "price": price,
        "change": change,
        "changeRate": change_rate,
        "updatedAt": reference.strftime("%Y-%m-%d %H:%M:%S KST"),
        "collectionMethod": "원본 서버 차트 전체 기간",
        "extractionMethod": "next-flight",
        "sourceName": "Hang on!",
        "sourceUrl": SOURCE_URL,
        "periodStart": rows[0]["time"],
        "periodEnd": rows[-1]["time"],
        "pointCount": len(rows),
        "rows": rows,
    }


def read_local_data() -> dict[str, Any]:
    try:
        value = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def read_published_data() -> dict[str, Any]:
    try:
        value = json.loads(
            fetch_text(
                f"{PUBLISHED_DATA_URL}?t="
                f"{int(datetime.now(KST).timestamp())}",
                timeout=20,
            )
        )
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def fallback_payload(reason: Exception) -> dict[str, Any]:
    """일시 오류가 나도 워크플로를 실패시키지 않고 직전 차트를 유지합니다."""
    candidates = (read_published_data(), read_local_data())

    for previous in candidates:
        rows = previous.get("rows")

        if isinstance(rows, list) and rows:
            previous["statusText"] = "원본 일시 응답 오류 · 직전 정상값"
            previous["collectionMethod"] = "직전 정상 차트 유지"
            previous["extractionMethod"] = "fallback"
            previous["lastAttemptAt"] = datetime.now(KST).strftime(
                "%Y-%m-%d %H:%M:%S KST"
            )
            previous["lastError"] = str(reason)
            return previous

    return {
        "title": "코스피 야간선물",
        "statusText": "원본 데이터 재시도 중",
        "price": 0,
        "change": 0,
        "changeRate": 0,
        "updatedAt": "정상 데이터 수집 대기 중",
        "collectionMethod": "자동 재시도",
        "extractionMethod": "fallback-empty",
        "sourceName": "Hang on!",
        "sourceUrl": SOURCE_URL,
        "periodStart": None,
        "periodEnd": None,
        "pointCount": 0,
        "lastAttemptAt": datetime.now(KST).strftime(
            "%Y-%m-%d %H:%M:%S KST"
        ),
        "lastError": str(reason),
        "rows": [],
    }


def write_payload(payload: dict[str, Any]) -> None:
    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    try:
        payload = build_payload()
        write_payload(payload)
        print(
            "갱신 완료:",
            f"{payload['price']:,.2f}",
            f"{payload['change']:+,.2f}",
            f"({payload['changeRate']:+.2f}%)",
        )
        print(
            "원본 차트 전체 구간:",
            payload["periodStart"],
            "~",
            payload["periodEnd"],
            f"({payload['pointCount']}개)",
        )
    except Exception as error:
        print(f"경고: {error}")
        payload = fallback_payload(error)
        write_payload(payload)
        print(
            "자동 실행은 계속 진행합니다:",
            payload["statusText"],
        )


if __name__ == "__main__":
    main()

