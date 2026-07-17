from __future__ import annotations

import json
import math
import re
import shutil
import statistics
import time
from datetime import datetime
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
    "time", "timestamp", "datetime", "dateTime", "date", "createdAt",
    "updatedAt", "x", "label", "category", "name",
)
VALUE_KEYS = (
    "price", "value", "close", "last", "current", "currentPrice",
    "futurePrice", "futuresPrice", "index", "y",
)
TIME_ARRAY_KEYS = ("labels", "categories", "times", "timestamps", "dates", "x")
VALUE_ARRAY_KEYS = ("data", "values", "prices", "close", "closes", "y")


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def number(value: Any) -> float | None:
    text = clean(value).replace(",", "").replace("%", "")
    text = text.replace("−", "-").replace("＋", "+")
    text = re.sub(r"[^0-9.+-]", "", text)
    if text in {"", "+", "-", ".", "+.", "-."}:
        return None
    try:
        parsed = float(text)
        return parsed if math.isfinite(parsed) else None
    except ValueError:
        return None


def parse_time(value: Any, reference: datetime) -> datetime | None:
    if isinstance(value, (int, float)):
        epoch = float(value)
        if epoch > 10_000_000_000:
            epoch /= 1000
        if 1_000_000_000 <= epoch <= 5_000_000_000:
            return datetime.fromtimestamp(epoch, ZoneInfo("UTC")).astimezone(KST)
        return None

    text = clean(value)
    if not text:
        return None
    if re.fullmatch(r"\d{10,13}", text):
        return parse_time(float(text), reference)

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=KST) if parsed.tzinfo is None else parsed.astimezone(KST)
    except ValueError:
        pass

    patterns = (
        r"(?P<year>\d{4})[./-](?P<month>\d{1,2})[./-](?P<day>\d{1,2})\D+"
        r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?",
        r"(?P<month>\d{1,2})[./-](?P<day>\d{1,2})\D+"
        r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        group = match.groupdict()
        try:
            return datetime(
                int(group.get("year") or reference.year), int(group["month"]),
                int(group["day"]), int(group["hour"]), int(group["minute"]),
                int(group.get("second") or 0), tzinfo=KST,
            )
        except ValueError:
            continue
    return None


def iso_minute(value: datetime) -> str:
    return value.astimezone(KST).replace(second=0, microsecond=0).isoformat()


def build_driver() -> webdriver.Chrome:
    options = Options()
    for item in (
        "--headless=new", "--no-sandbox", "--disable-dev-shm-usage",
        "--disable-gpu", "--window-size=1600,1200", "--lang=ko-KR",
        "--disable-blink-features=AutomationControlled",
    ):
        options.add_argument(item)
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
    )
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    path = shutil.which("chromedriver")
    driver = webdriver.Chrome(service=Service(path), options=options) if path else webdriver.Chrome(options=options)
    driver.execute_cdp_cmd("Network.enable", {})
    return driver


def wait_for_page(driver: webdriver.Chrome) -> None:
    def loaded(browser: webdriver.Chrome) -> bool:
        try:
            text = clean(browser.find_element(By.TAG_NAME, "body").text)
        except Exception:
            return False
        # 등락폭은 원본에서 부호가 생략되고, 등락률에만 부호가 붙을 수 있습니다.
        return "코스피 야간선물" in text and re.search(r"\([+-]?\s*\d+(?:\.\d+)?%\)", text) is not None

    WebDriverWait(driver, 60).until(loaded)
    time.sleep(7)


def extract_quote(driver: webdriver.Chrome) -> dict[str, float]:
    text = clean(driver.find_element(By.TAG_NAME, "body").text)
    rate_matches = list(re.finditer(r"\(([+-]?\s*\d+(?:\.\d+)?)%\)", text))
    if not rate_matches:
        raise RuntimeError("등락률을 찾지 못했습니다.")

    # 제목 뒤에 처음 나오는 등락률을 야간선물 카드의 값으로 사용합니다.
    title_at = text.find("코스피 야간선물")
    rate_match = next((m for m in rate_matches if m.start() > title_at), rate_matches[0])
    prefix = text[max(title_at, rate_match.start() - 120):rate_match.start()]
    tokens = re.findall(r"(?<![\d.])[+-]?\d{1,3}(?:,\d{3})*(?:\.\d+)(?![\d.])", prefix)
    if len(tokens) < 2:
        raise RuntimeError("현재 지수와 등락폭을 찾지 못했습니다.")

    price = number(tokens[-2])
    raw_change = number(tokens[-1])
    rate = number(rate_match.group(1))
    if price is None or raw_change is None or rate is None:
        raise RuntimeError("현재 숫자를 변환하지 못했습니다.")

    # 원본은 하락폭 앞의 '-'를 화살표/색으로 대체하므로 등락률의 부호를 적용합니다.
    change = abs(raw_change) * (-1 if rate < 0 else 1 if rate > 0 else 0)
    return {"price": price, "change": change, "changeRate": rate}


def iter_nodes(value: Any, path: str = "root") -> Iterable[tuple[str, Any]]:
    yield path, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from iter_nodes(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from iter_nodes(item, f"{path}[{index}]")


def dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for row in rows:
        timestamp = clean(row.get("time"))
        price = number(row.get("price"))
        if timestamp and price is not None and price > 0:
            mapping[timestamp] = {"time": timestamp, "price": round(price, 4)}
    return [mapping[key] for key in sorted(mapping)]


def rows_from_list(values: list[Any], reference: datetime) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if values and all(isinstance(item, dict) for item in values):
        for item in values:
            t_key = next((key for key in TIME_KEYS if key in item), None)
            v_key = next((key for key in VALUE_KEYS if key in item), None)
            if not t_key or not v_key:
                continue
            timestamp = parse_time(item[t_key], reference)
            price = number(item[v_key])
            if timestamp and price and price > 0:
                rows.append({"time": iso_minute(timestamp), "price": price})
    elif values and all(isinstance(item, (list, tuple)) and len(item) >= 2 for item in values):
        for item in values:
            timestamp = parse_time(item[0], reference)
            price = number(item[1])
            if timestamp and price and price > 0:
                rows.append({"time": iso_minute(timestamp), "price": price})
    return dedupe(rows)


def rows_from_parallel(value: dict[str, Any], reference: datetime) -> list[dict[str, Any]]:
    time_values = next((value[key] for key in TIME_ARRAY_KEYS if isinstance(value.get(key), list)), None)
    price_values = next((value[key] for key in VALUE_ARRAY_KEYS if isinstance(value.get(key), list)), None)
    if not time_values or not price_values or time_values is price_values:
        return []
    rows = []
    for raw_time, raw_price in zip(time_values, price_values):
        # series.data가 {x,y} 목록이면 일반 목록 처리기가 담당합니다.
        if isinstance(raw_price, (dict, list)):
            continue
        timestamp = parse_time(raw_time, reference)
        price = number(raw_price)
        if timestamp and price and price > 0:
            rows.append({"time": iso_minute(timestamp), "price": price})
    return dedupe(rows)


def score(rows: list[dict[str, Any]], current: float, context: str) -> float:
    if len(rows) < 4:
        return -1
    prices = [float(row["price"]) for row in rows]
    median = statistics.median(prices)
    if not current * .45 <= median <= current * 2.2:
        return -1
    times = [datetime.fromisoformat(row["time"]) for row in rows]
    span_hours = max((times[-1] - times[0]).total_seconds() / 3600, 0)
    keywords = sum(18 for word in ("kospi", "night", "future", "chart", "series", "price") if word in context.lower())
    closeness = max(0, 40 - abs(prices[-1] - current) / max(current, 1) * 400)
    return len(rows) * 4 + min(span_hours, 72) * 2 + keywords + closeness


def best_series(value: Any, current: float, context: str, reference: datetime) -> list[dict[str, Any]]:
    candidates: list[tuple[float, list[dict[str, Any]]]] = []
    for path, node in iter_nodes(value):
        rows: list[dict[str, Any]] = []
        if isinstance(node, list):
            rows = rows_from_list(node, reference)
        elif isinstance(node, dict):
            rows = rows_from_parallel(node, reference)
        candidate_score = score(rows, current, f"{context} {path}")
        if candidate_score >= 0:
            candidates.append((candidate_score, rows))
    return max(candidates, key=lambda item: item[0])[1] if candidates else []


def series_from_network(driver: webdriver.Chrome, current: float, reference: datetime) -> list[dict[str, Any]]:
    candidates: list[tuple[float, list[dict[str, Any]]]] = []
    seen: set[str] = set()
    for entry in driver.get_log("performance"):
        try:
            message = json.loads(entry["message"])["message"]
            if message.get("method") != "Network.responseReceived":
                continue
            params = message.get("params", {})
            response = params.get("response", {})
            request_id = params.get("requestId")
            url = clean(response.get("url"))
            if not request_id or request_id in seen or url.startswith("data:"):
                continue
            seen.add(request_id)
            resource_type = params.get("type", "")
            mime = clean(response.get("mimeType")).lower()
            if resource_type not in {"XHR", "Fetch", "Document", "Script"} and "json" not in mime:
                continue
            body = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": request_id}).get("body", "")
            if not body or len(body) > 15_000_000:
                continue
            parsed_values: list[Any] = []
            try:
                parsed_values.append(json.loads(body))
            except json.JSONDecodeError:
                pass
            # Next.js 페이지의 script 안 JSON도 검사합니다.
            if "__next_f.push" in body.lower() or "__NEXT_DATA__" in body:
                for snippet in re.findall(r"(\[\s*\{.{100,}?\}\s*\])", body, flags=re.DOTALL):
                    if len(snippet) <= 5_000_000:
                        try:
                            parsed_values.append(json.loads(snippet))
                        except json.JSONDecodeError:
                            pass
            for parsed in parsed_values:
                rows = best_series(parsed, current, url, reference)
                candidate_score = score(rows, current, url)
                if candidate_score >= 0:
                    candidates.append((candidate_score, rows))
        except Exception:
            continue
    if not candidates:
        return []
    rows = max(candidates, key=lambda item: item[0])[1]
    print(f"네트워크에서 원본 시계열 {len(rows)}개 발견")
    return rows


def choose_chart(driver: webdriver.Chrome) -> WebElement | None:
    candidates: list[WebElement] = []
    seen: set[str] = set()
    for selector in ("canvas", "svg", "[class*='chart']", "[class*='Chart']", "[class*='recharts']", "[class*='apexcharts']"):
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            try:
                rect = element.rect
                if element.is_displayed() and rect.get("width", 0) >= 500 and rect.get("height", 0) >= 180 and element.id not in seen:
                    seen.add(element.id)
                    candidates.append(element)
            except Exception:
                pass
    return max(candidates, key=lambda item: item.rect.get("width", 0) * item.rect.get("height", 0)) if candidates else None


def tooltip_text(driver: webdriver.Chrome) -> str:
    parts: list[str] = []
    for selector in ("[role='tooltip']", "[class*='tooltip']", "[class*='Tooltip']", ".recharts-tooltip-wrapper", ".apexcharts-tooltip"):
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            try:
                value = clean(element.text)
                if element.is_displayed() and value and value not in parts:
                    parts.append(value)
            except Exception:
                pass
    return " ".join(parts)


def point_from_tooltip(text: str, current: float, reference: datetime) -> dict[str, Any] | None:
    timestamp = parse_time(text, reference)
    if timestamp is None:
        return None
    without_rate = re.sub(r"[+-]?\d+(?:\.\d+)?\s*%", " ", text)
    values = [number(token) for token in re.findall(r"(?<![\d.])[+-]?\d{1,3}(?:,\d{3})*(?:\.\d+)(?![\d.])", without_rate)]
    candidates = [value for value in values if value and current * .45 <= value <= current * 2.2]
    if not candidates:
        return None
    price = min(candidates, key=lambda value: abs(value - current))
    return {"time": iso_minute(timestamp), "price": round(price, 4)}


def series_from_hover(driver: webdriver.Chrome, current: float, reference: datetime) -> list[dict[str, Any]]:
    chart = choose_chart(driver)
    if chart is None:
        return []
    width = int(chart.rect.get("width", 0))
    height = int(chart.rect.get("height", 0))
    rows: list[dict[str, Any]] = []
    # 차트의 왼쪽 끝부터 오른쪽 끝까지 훑어 원본과 동일한 시작·종료 시각을 유지합니다.
    for index in range(160):
        x = int(5 + (width - 10) * index / 159)
        try:
            ActionChains(driver).move_to_element_with_offset(chart, x, max(10, height // 2)).perform()
            time.sleep(.055)
            point = point_from_tooltip(tooltip_text(driver), current, reference)
            if point:
                rows.append(point)
        except Exception:
            pass
    rows = dedupe(rows)
    print(f"툴팁에서 원본 전체 구간 {len(rows)}개 발견")
    return rows if score(rows, current, "hover chart") >= 0 else []


def is_trading_time(reference: datetime) -> bool:
    return reference.hour >= 18 or reference.hour < 6


def collect() -> dict[str, Any]:
    reference = datetime.now(KST)
    driver = build_driver()
    try:
        driver.get(SOURCE_URL)
        wait_for_page(driver)
        quote = extract_quote(driver)
        rows = series_from_network(driver, quote["price"], reference)
        extraction = "network"
        if not rows:
            rows = series_from_hover(driver, quote["price"], reference)
            extraction = "hover"
        if len(rows) < 4:
            raise RuntimeError("원본 전체 차트 시계열을 찾지 못해 기존 배포 데이터를 유지합니다.")

        # 이전 실행 데이터와 합치거나 임의로 자르지 않습니다. 원본 응답의 전체 기간만 배포합니다.
        rows = dedupe(rows)
        return {
            "title": "코스피 야간선물",
            "statusText": "야간 거래 시간 중 실시간 제공" if is_trading_time(reference) else "야간 거래 종료 후 최종값",
            **quote,
            "updatedAt": reference.strftime("%Y-%m-%d %H:%M:%S KST"),
            "collectionMethod": "원본 차트 전체 기간",
            "extractionMethod": extraction,
            "sourceName": "Hang on!",
            "sourceUrl": SOURCE_URL,
            "periodStart": rows[0]["time"],
            "periodEnd": rows[-1]["time"],
            "pointCount": len(rows),
            "rows": rows,
        }
    except TimeoutException as exc:
        raise RuntimeError("Hang on 페이지 로딩 시간이 초과되었습니다.") from exc
    finally:
        driver.quit()


def main() -> None:
    payload = collect()
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"갱신 완료: {payload['price']:,.2f} {payload['change']:+,.2f} ({payload['changeRate']:+.2f}%)")
    print(f"원본 표시 기간: {payload['periodStart']} ~ {payload['periodEnd']} ({payload['pointCount']}개)")


if __name__ == "__main__":
    main()

