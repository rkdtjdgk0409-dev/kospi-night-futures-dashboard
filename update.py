from __future__ import annotations

import json
import math
import re
import shutil
import statistics
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver import ActionChains
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support.ui import WebDriverWait


SOURCE_URL = "https://www.hangon.co.kr/kospi-night-futures"
OUTPUT_PATH = Path("data.json")
KST = ZoneInfo("Asia/Seoul")

TIME_KEYS = (
    "time",
    "timestamp",
    "datetime",
    "dateTime",
    "date",
    "createdAt",
    "updatedAt",
    "x",
    "label",
)

VALUE_KEYS = (
    "price",
    "value",
    "close",
    "last",
    "current",
    "currentPrice",
    "futurePrice",
    "futuresPrice",
    "index",
    "y",
)


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value).replace("\xa0", " ").strip()
    return re.sub(r"\s+", " ", text)


def parse_number(value: Any) -> float | None:
    text = clean_text(value)
    text = text.replace(",", "").replace("%", "")
    text = text.replace("−", "-").replace("＋", "+")
    text = re.sub(r"[^0-9.+-]", "", text)

    if text in {"", "+", "-", ".", "+.", "-."}:
        return None

    try:
        number = float(text)
    except ValueError:
        return None

    return number if math.isfinite(number) else None


def now_kst() -> datetime:
    return datetime.now(KST)


def session_date_for_hour(
    hour: int,
    reference: datetime,
) -> datetime:
    """
    야간장은 한국시간 18:00~익일 05:00입니다.
    새벽 시간만 있고 날짜가 없으면 전날 시작한 세션으로 해석합니다.
    """
    if reference.hour < 12 and hour >= 18:
        return reference - timedelta(days=1)

    if reference.hour >= 18 and hour < 12:
        return reference + timedelta(days=1)

    return reference


def parse_timestamp(
    value: Any,
    reference: datetime,
) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        number = float(value)

        if number > 10_000_000_000:
            number /= 1000

        if 1_000_000_000 <= number <= 5_000_000_000:
            return datetime.fromtimestamp(
                number,
                tz=ZoneInfo("UTC"),
            ).astimezone(KST)

        return None

    text = clean_text(value)

    if not text:
        return None

    numeric = parse_number(text)

    if (
        numeric is not None
        and re.fullmatch(r"\d{10,13}", text)
    ):
        return parse_timestamp(numeric, reference)

    iso_text = text.replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(iso_text)

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=KST)
        else:
            parsed = parsed.astimezone(KST)

        return parsed
    except ValueError:
        pass

    full_patterns = (
        r"(?P<year>\d{4})[./-](?P<month>\d{1,2})[./-](?P<day>\d{1,2})"
        r"[ T]?(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?",
        r"(?P<month>\d{1,2})[./-](?P<day>\d{1,2})"
        r"[ T]?(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?",
    )

    for pattern in full_patterns:
        match = re.search(pattern, text)

        if not match:
            continue

        groups = match.groupdict()
        year = int(groups.get("year") or reference.year)
        month = int(groups["month"])
        day = int(groups["day"])
        hour = int(groups["hour"])
        minute = int(groups["minute"])
        second = int(groups.get("second") or 0)

        try:
            return datetime(
                year,
                month,
                day,
                hour,
                minute,
                second,
                tzinfo=KST,
            )
        except ValueError:
            continue

    time_match = re.search(
        r"(?<!\d)(?P<hour>\d{1,2}):(?P<minute>\d{2})"
        r"(?::(?P<second>\d{2}))?(?!\d)",
        text,
    )

    if time_match:
        hour = int(time_match.group("hour"))
        minute = int(time_match.group("minute"))
        second = int(time_match.group("second") or 0)
        base = session_date_for_hour(hour, reference)

        try:
            return datetime(
                base.year,
                base.month,
                base.day,
                hour,
                minute,
                second,
                tzinfo=KST,
            )
        except ValueError:
            return None

    return None


def iso_minute(value: datetime) -> str:
    return value.astimezone(KST).replace(
        second=0,
        microsecond=0,
    ).isoformat()


def build_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1600,1800")
    options.add_argument("--lang=ko-KR")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    )
    options.set_capability(
        "goog:loggingPrefs",
        {"performance": "ALL"},
    )

    driver_path = shutil.which("chromedriver")

    if driver_path:
        driver = webdriver.Chrome(
            service=Service(driver_path),
            options=options,
        )
    else:
        driver = webdriver.Chrome(options=options)

    driver.execute_cdp_cmd("Network.enable", {})
    return driver


def wait_for_page(driver: webdriver.Chrome) -> None:
    def loaded(browser: webdriver.Chrome) -> bool:
        try:
            text = browser.find_element(By.TAG_NAME, "body").text
        except Exception:
            return False

        return (
            "코스피 야간선물" in text
            and re.search(
                r"[+-]\s*\d[\d,]*(?:\.\d+)?\s*"
                r"\([+-]?\s*\d+(?:\.\d+)?%\)",
                text,
            )
            is not None
        )

    WebDriverWait(driver, 60).until(loaded)
    time.sleep(7)


def extract_quote(driver: webdriver.Chrome) -> dict[str, float]:
    text = clean_text(
        driver.find_element(By.TAG_NAME, "body").text
    )

    change_match = re.search(
        r"([+-]\s*\d[\d,]*(?:\.\d+)?)\s*"
        r"\(([+-]?\s*\d+(?:\.\d+)?)%\)",
        text,
    )

    if not change_match:
        raise RuntimeError(
            "현재 등락폭과 등락률을 찾지 못했습니다."
        )

    before_change = text[: change_match.start()]
    price_candidates = re.findall(
        r"(?<![\d.])\d{1,3}(?:,\d{3})*(?:\.\d+)(?![\d.])",
        before_change,
    )

    if not price_candidates:
        raise RuntimeError(
            "현재 코스피 야간선물 값을 찾지 못했습니다."
        )

    price = parse_number(price_candidates[-1])
    change = parse_number(change_match.group(1))
    change_rate = parse_number(change_match.group(2))

    if (
        price is None
        or change is None
        or change_rate is None
    ):
        raise RuntimeError(
            "현재 야간선물 숫자를 변환하지 못했습니다."
        )

    return {
        "price": price,
        "change": change,
        "changeRate": change_rate,
    }


def iter_arrays(
    value: Any,
    path: str = "root",
) -> Iterable[tuple[str, list[Any]]]:
    if isinstance(value, list):
        yield path, value

        for index, item in enumerate(value):
            yield from iter_arrays(
                item,
                f"{path}[{index}]",
            )

    elif isinstance(value, dict):
        for key, item in value.items():
            yield from iter_arrays(
                item,
                f"{path}.{key}",
            )


def row_from_dict(
    item: dict[str, Any],
    reference: datetime,
) -> tuple[datetime, float] | None:
    time_key = next(
        (
            key
            for key in TIME_KEYS
            if key in item
        ),
        None,
    )
    value_key = next(
        (
            key
            for key in VALUE_KEYS
            if key in item
        ),
        None,
    )

    if not time_key or not value_key:
        return None

    timestamp = parse_timestamp(
        item.get(time_key),
        reference,
    )
    price = parse_number(item.get(value_key))

    if timestamp is None or price is None or price <= 0:
        return None

    return timestamp, price


def rows_from_array(
    values: list[Any],
    reference: datetime,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    if not values:
        return rows

    if all(isinstance(item, dict) for item in values):
        for item in values:
            parsed = row_from_dict(item, reference)

            if parsed:
                timestamp, price = parsed
                rows.append(
                    {
                        "time": iso_minute(timestamp),
                        "price": round(price, 4),
                    }
                )

    elif all(
        isinstance(item, (list, tuple))
        and len(item) >= 2
        for item in values
    ):
        for item in values:
            timestamp = parse_timestamp(
                item[0],
                reference,
            )
            price = parse_number(item[1])

            if timestamp and price and price > 0:
                rows.append(
                    {
                        "time": iso_minute(timestamp),
                        "price": round(price, 4),
                    }
                )

    return dedupe_rows(rows)


def dedupe_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}

    for row in rows:
        time_value = clean_text(row.get("time"))
        price = parse_number(row.get("price"))

        if not time_value or price is None:
            continue

        mapping[time_value] = {
            "time": time_value,
            "price": round(price, 4),
        }

    return [
        mapping[key]
        for key in sorted(mapping)
    ]


def series_score(
    rows: list[dict[str, Any]],
    current_price: float,
    context: str,
) -> float:
    if len(rows) < 4:
        return -1

    prices = [
        float(row["price"])
        for row in rows
        if row.get("price") is not None
    ]

    if len(prices) < 4:
        return -1

    median = statistics.median(prices)

    if not (
        current_price * 0.45
        <= median
        <= current_price * 2.2
    ):
        return -1

    valid_times = 0

    for row in rows:
        try:
            datetime.fromisoformat(row["time"])
            valid_times += 1
        except ValueError:
            pass

    if valid_times / len(rows) < 0.8:
        return -1

    keyword_bonus = sum(
        15
        for word in (
            "kospi",
            "night",
            "future",
            "chart",
            "series",
            "price",
        )
        if word in context.lower()
    )

    distinct_prices = len(
        {
            round(price, 3)
            for price in prices
        }
    )

    return (
        len(rows) * 3
        + min(distinct_prices, 50)
        + keyword_bonus
    )


def extract_json_series(
    value: Any,
    current_price: float,
    context: str,
    reference: datetime,
) -> list[dict[str, Any]]:
    candidates: list[tuple[float, list[dict[str, Any]]]] = []

    for path, array in iter_arrays(value):
        rows = rows_from_array(array, reference)
        score = series_score(
            rows,
            current_price,
            f"{context} {path}",
        )

        if score >= 0:
            candidates.append((score, rows))

    if not candidates:
        return []

    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )
    return candidates[0][1]


def series_from_network(
    driver: webdriver.Chrome,
    current_price: float,
    reference: datetime,
) -> list[dict[str, Any]]:
    candidates: list[tuple[float, list[dict[str, Any]]]] = []
    seen_request_ids: set[str] = set()

    try:
        logs = driver.get_log("performance")
    except Exception:
        return []

    for entry in logs:
        try:
            outer = json.loads(entry["message"])
            message = outer["message"]

            if message.get("method") != "Network.responseReceived":
                continue

            params = message.get("params", {})
            response = params.get("response", {})
            request_id = params.get("requestId")
            url = clean_text(response.get("url"))

            if (
                not request_id
                or request_id in seen_request_ids
                or url.startswith("data:")
            ):
                continue

            seen_request_ids.add(request_id)
            resource_type = params.get("type", "")
            mime_type = clean_text(
                response.get("mimeType")
            ).lower()

            if (
                resource_type not in {
                    "XHR",
                    "Fetch",
                    "Document",
                    "Script",
                }
                and "json" not in mime_type
            ):
                continue

            try:
                body_result = driver.execute_cdp_cmd(
                    "Network.getResponseBody",
                    {"requestId": request_id},
                )
                body = body_result.get("body", "")
            except Exception:
                continue

            if not body or len(body) > 15_000_000:
                continue

            parsed_values: list[Any] = []

            try:
                parsed_values.append(json.loads(body))
            except json.JSONDecodeError:
                pass

            # JavaScript 응답 안에 들어 있는 큰 JSON 배열도 확인합니다.
            for match in re.finditer(
                r"(\[\s*\{.{100,}?\}\s*\])",
                body,
                flags=re.DOTALL,
            ):
                snippet = match.group(1)

                if len(snippet) > 5_000_000:
                    continue

                try:
                    parsed_values.append(
                        json.loads(snippet)
                    )
                except json.JSONDecodeError:
                    continue

            for parsed in parsed_values:
                rows = extract_json_series(
                    parsed,
                    current_price,
                    url,
                    reference,
                )
                score = series_score(
                    rows,
                    current_price,
                    url,
                )

                if score >= 0:
                    candidates.append((score, rows))

        except Exception:
            continue

    if not candidates:
        return []

    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    best = candidates[0][1]
    print(
        f"네트워크 시계열 발견: {len(best)}개 포인트"
    )
    return best


def chart_candidates(
    driver: webdriver.Chrome,
) -> list[WebElement]:
    output: list[WebElement] = []
    seen: set[str] = set()

    for selector in (
        "canvas",
        "svg",
        "[class*='chart']",
        "[class*='Chart']",
        "[class*='recharts']",
        "[class*='apexcharts']",
        "[class*='highcharts']",
    ):
        for element in driver.find_elements(
            By.CSS_SELECTOR,
            selector,
        ):
            try:
                if not element.is_displayed():
                    continue

                rect = element.rect
                width = float(rect.get("width", 0))
                height = float(rect.get("height", 0))

                if width < 500 or height < 180:
                    continue

                if element.id in seen:
                    continue

                seen.add(element.id)
                output.append(element)

            except Exception:
                continue

    return output


def choose_chart(
    driver: webdriver.Chrome,
) -> WebElement | None:
    candidates = chart_candidates(driver)

    if not candidates:
        return None

    return max(
        candidates,
        key=lambda element: (
            float(element.rect.get("width", 0))
            * float(element.rect.get("height", 0))
        ),
    )


def visible_tooltip_texts(
    driver: webdriver.Chrome,
) -> list[str]:
    selectors = (
        "[role='tooltip']",
        "[class*='tooltip']",
        "[class*='Tooltip']",
        ".recharts-tooltip-wrapper",
        ".apexcharts-tooltip",
        ".highcharts-tooltip",
    )

    texts: list[str] = []

    for selector in selectors:
        for element in driver.find_elements(
            By.CSS_SELECTOR,
            selector,
        ):
            try:
                if not element.is_displayed():
                    continue

                text = clean_text(element.text)

                if text and text not in texts:
                    texts.append(text)

            except Exception:
                continue

    return texts


def tooltip_point(
    texts: list[str],
    current_price: float,
    reference: datetime,
) -> dict[str, Any] | None:
    for text in texts:
        timestamp = parse_timestamp(text, reference)

        if timestamp is None:
            continue

        without_percent = re.sub(
            r"[+-]?\d+(?:\.\d+)?\s*%",
            " ",
            text,
        )
        number_tokens = re.findall(
            r"(?<![\d.])[+-]?\d{1,3}(?:,\d{3})*(?:\.\d+)(?![\d.])",
            without_percent,
        )
        candidates = [
            number
            for token in number_tokens
            if (number := parse_number(token)) is not None
            and number > 0
            and current_price * 0.45 <= number <= current_price * 2.2
        ]

        if not candidates:
            continue

        price = min(
            candidates,
            key=lambda value: abs(value - current_price),
        )

        return {
            "time": iso_minute(timestamp),
            "price": round(price, 4),
        }

    return None


def series_from_hover(
    driver: webdriver.Chrome,
    current_price: float,
    reference: datetime,
) -> list[dict[str, Any]]:
    chart = choose_chart(driver)

    if chart is None:
        return []

    width = int(float(chart.rect.get("width", 0)))
    height = int(float(chart.rect.get("height", 0)))

    if width < 100 or height < 100:
        return []

    rows: list[dict[str, Any]] = []
    sample_count = min(100, max(45, width // 12))

    for index in range(sample_count):
        x = int(
            6
            + (width - 12)
            * index
            / max(1, sample_count - 1)
        )
        y = max(10, height // 2)

        try:
            ActionChains(driver).move_to_element_with_offset(
                chart,
                x,
                y,
            ).perform()
            time.sleep(0.08)

            point = tooltip_point(
                visible_tooltip_texts(driver),
                current_price,
                reference,
            )

            if point:
                rows.append(point)

        except Exception:
            continue

    rows = dedupe_rows(rows)

    if series_score(
        rows,
        current_price,
        "hover chart",
    ) < 0:
        return []

    print(
        f"차트 툴팁 시계열 발견: {len(rows)}개 포인트"
    )
    return rows


def load_previous() -> dict[str, Any]:
    if not OUTPUT_PATH.exists():
        return {}

    try:
        value = json.loads(
            OUTPUT_PATH.read_text(encoding="utf-8")
        )
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def merge_rows(
    previous_rows: list[dict[str, Any]],
    source_rows: list[dict[str, Any]],
    current_quote: dict[str, float],
    reference: datetime,
) -> tuple[list[dict[str, Any]], str]:
    mapping: dict[str, dict[str, Any]] = {}

    for row in previous_rows:
        time_value = clean_text(row.get("time"))
        price = parse_number(row.get("price"))

        if time_value and price is not None:
            mapping[time_value] = {
                "time": time_value,
                "price": round(price, 4),
            }

    method = "5분 간격 현재가 누적"

    if len(source_rows) >= 4:
        method = "원본 차트 시계열 추출"

        for row in source_rows:
            mapping[row["time"]] = row

    current_time = iso_minute(reference)
    mapping[current_time] = {
        "time": current_time,
        "price": round(current_quote["price"], 4),
    }

    cutoff = reference - timedelta(days=4)
    filtered: list[dict[str, Any]] = []

    for key in sorted(mapping):
        try:
            timestamp = datetime.fromisoformat(key)

            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=KST)

            if timestamp.astimezone(KST) < cutoff:
                continue
        except ValueError:
            continue

        filtered.append(mapping[key])

    # 파일 크기와 화면 밀도를 제한하되 최근 데이터는 충분히 유지합니다.
    if len(filtered) > 2500:
        filtered = filtered[-2500:]

    return filtered, method


def is_trading_time(reference: datetime) -> bool:
    return reference.hour >= 18 or reference.hour < 6


def build_payload() -> dict[str, Any]:
    reference = now_kst()
    previous = load_previous()
    driver = build_driver()

    try:
        driver.get(SOURCE_URL)
        wait_for_page(driver)
        quote = extract_quote(driver)

        network_rows = series_from_network(
            driver,
            quote["price"],
            reference,
        )

        if network_rows:
            source_rows = network_rows
            extraction = "network"
        else:
            hover_rows = series_from_hover(
                driver,
                quote["price"],
                reference,
            )
            source_rows = hover_rows
            extraction = "hover" if hover_rows else "observation"

        rows, method = merge_rows(
            previous.get("rows", []),
            source_rows,
            quote,
            reference,
        )

        return {
            "title": "코스피 야간선물",
            "statusText": (
                "야간 거래 시간 중 실시간 제공"
                if is_trading_time(reference)
                else "야간 거래 종료 후 최종값"
            ),
            "price": quote["price"],
            "change": quote["change"],
            "changeRate": quote["changeRate"],
            "updatedAt": reference.strftime(
                "%Y-%m-%d %H:%M:%S KST"
            ),
            "collectionMethod": method,
            "extractionMethod": extraction,
            "sourceName": "Hang on!",
            "sourceUrl": SOURCE_URL,
            "rows": rows,
        }

    except TimeoutException as exc:
        raise RuntimeError(
            "Hang on 페이지 로딩 시간이 초과되었습니다."
        ) from exc

    finally:
        driver.quit()


def main() -> None:
    payload = build_payload()

    OUTPUT_PATH.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        "갱신 완료:",
        f"{payload['price']:,.2f}",
        f"{payload['change']:+,.2f}",
        f"({payload['changeRate']:+.2f}%)",
    )
    print(
        f"시계열 포인트: {len(payload['rows'])}개"
    )
    print(
        f"수집 방식: {payload['collectionMethod']}"
    )


if __name__ == "__main__":
    main()
