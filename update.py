from __future__ import annotations

import json
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support.ui import WebDriverWait

SOURCE_URL = "https://www.hangon.co.kr/kospi-night-futures"
OUTPUT_IMAGE = Path("panel.png")
OUTPUT_JSON = Path("data.json")
KST = ZoneInfo("Asia/Seoul")


def build_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1440,1600")
    options.add_argument("--lang=ko-KR")
    options.add_argument("--hide-scrollbars")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    )

    chromedriver = shutil.which("chromedriver")
    if chromedriver:
        return webdriver.Chrome(
            service=Service(chromedriver),
            options=options,
        )

    return webdriver.Chrome(options=options)


def wait_for_dashboard(driver: webdriver.Chrome) -> None:
    def ready(browser: webdriver.Chrome) -> bool:
        try:
            text = browser.find_element(By.TAG_NAME, "body").text
        except Exception:
            return False

        has_quote = re.search(
            r"[+-]\s*\d[\d,]*(?:\.\d+)?\s*"
            r"\([+-]?\s*\d+(?:\.\d+)?%\)",
            text,
        )

        return "코스피 야간선물" in text and has_quote is not None

    WebDriverWait(driver, 60).until(ready)
    time.sleep(6)


def rect(element: WebElement) -> dict[str, float]:
    value = element.rect
    return {
        "x": float(value.get("x", 0)),
        "y": float(value.get("y", 0)),
        "width": float(value.get("width", 0)),
        "height": float(value.get("height", 0)),
    }


def visible_elements(driver: webdriver.Chrome, selector: str) -> list[WebElement]:
    output = []
    for element in driver.find_elements(By.CSS_SELECTOR, selector):
        try:
            if element.is_displayed():
                output.append(element)
        except Exception:
            continue
    return output


def find_heading(driver: webdriver.Chrome) -> WebElement:
    candidates = driver.find_elements(
        By.XPATH,
        "//*[normalize-space(text())='코스피 야간선물']",
    )

    visible = []
    for element in candidates:
        try:
            if element.is_displayed():
                visible.append(element)
        except Exception:
            continue

    if not visible:
        raise RuntimeError("코스피 야간선물 제목을 찾지 못했습니다.")

    # 본문 제목은 보통 글자가 크므로 높이가 큰 요소를 우선합니다.
    return max(visible, key=lambda element: rect(element)["height"])


def find_chart(driver: webdriver.Chrome, heading: WebElement) -> WebElement:
    heading_box = rect(heading)
    candidates = []

    for selector in (
        "canvas",
        "svg",
        "[class*='chart']",
        "[class*='Chart']",
        "[class*='recharts']",
        "[class*='apexcharts']",
        "[class*='highcharts']",
    ):
        for element in visible_elements(driver, selector):
            box = rect(element)
            if box["width"] < 500 or box["height"] < 180:
                continue
            if box["y"] < heading_box["y"] - 50:
                continue
            distance = abs(box["y"] - (heading_box["y"] + heading_box["height"]))
            if distance > 1000:
                continue
            score = box["width"] * box["height"] / (1 + distance)
            candidates.append((score, element))

    if not candidates:
        raise RuntimeError("야간선물 차트 영역을 찾지 못했습니다.")

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def find_panel(driver: webdriver.Chrome, heading: WebElement, chart: WebElement) -> WebElement | None:
    script = r"""
    const heading = arguments[0];
    const chart = arguments[1];
    let node = heading;
    const chartRect = chart.getBoundingClientRect();

    while (node && node !== document.body) {
      const r = node.getBoundingClientRect();
      const text = (node.innerText || '').replace(/\s+/g, ' ').trim();
      const containsChart = node.contains(chart);
      const hasTitle = text.includes('코스피 야간선물');
      const hasPercent = /[+-]?\d+(?:\.\d+)?%/.test(text);

      if (
        containsChart && hasTitle && hasPercent &&
        r.width >= Math.max(600, chartRect.width * 0.9) &&
        r.height >= chartRect.height &&
        r.height <= 950
      ) {
        return node;
      }

      node = node.parentElement;
    }

    return null;
    """

    return driver.execute_script(script, heading, chart)


def validate_image(path: Path) -> None:
    with Image.open(path) as image:
        if image.width < 600 or image.height < 280:
            raise RuntimeError(
                f"캡처 이미지 크기가 너무 작습니다: {image.width}x{image.height}"
            )


def capture_union(driver: webdriver.Chrome, heading: WebElement, chart: WebElement) -> None:
    full_path = Path("_full.png")

    # 페이지 전체 높이에 맞춰 브라우저를 확장합니다.
    width = int(driver.execute_script("return document.documentElement.scrollWidth"))
    height = int(driver.execute_script("return document.documentElement.scrollHeight"))
    width = max(1200, min(width, 1800))
    height = max(900, min(height, 5000))
    driver.set_window_size(width, height)
    time.sleep(1)
    driver.save_screenshot(str(full_path))

    heading_box = rect(heading)
    chart_box = rect(chart)

    left = max(0, int(min(heading_box["x"], chart_box["x"])) - 35)
    top = max(0, int(heading_box["y"]) - 45)
    right = min(
        width,
        int(max(chart_box["x"] + chart_box["width"], width - 40)),
    )
    bottom = min(height, int(chart_box["y"] + chart_box["height"]) + 25)

    with Image.open(full_path) as image:
        cropped = image.crop((left, top, right, bottom))
        cropped.save(OUTPUT_IMAGE)

    full_path.unlink(missing_ok=True)
    validate_image(OUTPUT_IMAGE)


def capture_panel(driver: webdriver.Chrome, heading: WebElement, chart: WebElement) -> None:
    panel = find_panel(driver, heading, chart)

    if panel is not None:
        try:
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});",
                panel,
            )
            time.sleep(1)
            panel.screenshot(str(OUTPUT_IMAGE))
            validate_image(OUTPUT_IMAGE)
            return
        except Exception as exc:
            print("패널 요소 캡처 실패, 좌표 캡처로 전환:", exc)

    capture_union(driver, heading, chart)


def main() -> None:
    driver = build_driver()

    try:
        driver.get(SOURCE_URL)
        wait_for_dashboard(driver)
        heading = find_heading(driver)
        chart = find_chart(driver, heading)
        capture_panel(driver, heading, chart)

        payload = {
            "title": "코스피 야간선물",
            "updatedAt": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
            "sourceName": "Hang on!",
            "sourceUrl": SOURCE_URL,
        }

        OUTPUT_JSON.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print("코스피 야간선물 패널 갱신 완료")
        print("이미지:", OUTPUT_IMAGE)

    except TimeoutException as exc:
        raise RuntimeError("Hang on 페이지 로딩 시간이 초과되었습니다.") from exc

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
