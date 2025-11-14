import re
import time
import sys
import logging
import json
from datetime import datetime
from typing import Optional, Tuple, Dict, List
from urllib.parse import urljoin

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException, WebDriverException, StaleElementReferenceException, ElementClickInterceptedException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup

BASE_URL = "https://dobro.mail.ru/volunteers/"
OUT_JSON = "events.json"
LOG_FILE = "parser.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, "w", "utf-8")
    ]
)
log = logging.getLogger("dobro.rf")

RU_MONTHS = {
    "января": "01", "февраля": "02", "марта": "03", "апреля": "04",
    "мая": "05", "июня": "06", "июля": "07", "августа": "08",
    "сентября": "09", "октября": "10", "ноября": "11", "декабря": "12"
}
DATE_RX = re.compile(r"(?P<d>\d{1,2})\s+(?P<m>[А-Яа-я]+)\s*(?P<y>\d{4})?", re.IGNORECASE)
TIME_RX = re.compile(r"(?P<h>\d{1,2})[:.](?P<m>\d{2})")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def ru_date_to_iso(text: str) -> Optional[str]:
    m = DATE_RX.search(text or "")
    if not m:
        return None
    d = int(m.group("d"))
    m_ru = (m.group("m") or "").lower()
    y = m.group("y") or str(datetime.now().year)
    if m_ru not in RU_MONTHS:
        return None
    return f"{int(y):04d}-{int(RU_MONTHS[m_ru]):02d}-{d:02d}"


def extract_times(text: str) -> Tuple[Optional[str], Optional[str]]:
    ts = TIME_RX.findall(text or "")
    fmt = lambda t: f"{int(t[0]):02d}:{int(t[1]):02d}"
    if not ts:
        return None, None
    if len(ts) == 1:
        return fmt(ts[0]), None
    return fmt(ts[0]), fmt(ts[1])

def driver_build():
    opts = uc.ChromeOptions()
    opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--window-size=1440,1000")
    opts.add_argument("--lang=ru-RU")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"
    )
    d = uc.Chrome(options=opts)
    d.set_page_load_timeout(60)
    d.set_script_timeout(60)
    return d


def wait_ready(drv, t=45):
    WebDriverWait(drv, t).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )


def click_show_more_until_end(drv):
    clicks = 0
    while True:
        try:
            btns = drv.find_elements(
                By.XPATH,
                "//button[.//span[contains(.,'Показать ещё')] or contains(.,'Показать ещё')]"
            )
            if not btns:
                log.info("Кнопка «Показать ещё» не найдена — конец ленты.")
                break
            btn = btns[0]
            drv.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", btn
            )
            time.sleep(2.2)
            if not btn.is_enabled() or not btn.is_displayed():
                log.info("Кнопка «Показать ещё» недоступна — конец ленты.")
                break
            btn.click()
            clicks += 1
            log.info("Клик «Показать ещё» #%d… жду подгрузку", clicks)
            time.sleep(3.2)
        except Exception as e:
            log.warning("Не удалось кликнуть «Показать ещё»: %s", e)
            break
    log.info("Кликов всего: %d", clicks)


def collect_detail_links_from_feed(html: str) -> List[str]:
    soup = BeautifulSoup(html, "lxml")
    links = []
    for a in soup.find_all("a"):
        if "Подробнее" in norm(a.get_text()):
            href = a.get("href")
            if href:
                if href.startswith("/"):
                    href = urljoin("https://добро.рф", href)
                links.append(href)
    out, seen = [], set()
    for u in links:
        u = u.split("#")[0]
        if "login" in u or "mailto:" in u:
            continue
        if u not in seen:
            seen.add(u)
            out.append(u)
    log.info("Ссылок «Подробнее» собрано: %d", len(out))
    return out

def try_next_data(html: str):
    soup = BeautifulSoup(html, "lxml")
    sc = soup.find("script", id="__NEXT_DATA__", type="application/json")
    if sc and sc.string:
        try:
            return json.loads(sc.string)
        except json.JSONDecodeError:
            pass
    for s in soup.find_all("script", type="application/json"):
        txt = s.string or ""
        if '"pageProps"' in txt:
            try:
                return json.loads(txt)
            except json.JSONDecodeError:
                pass
    return None


def read_city_from_yamaps_html(html: str) -> Optional[str]:
    """
    Разбирает HTML Яндекс.Карт и достаёт город из
    <h1 class="home-panel-content-view__header-text">Санкт-Петербург</h1>
    """
    soup = BeautifulSoup(html, "lxml")
    h = soup.select_one(
        "h1.home-panel-content-view__header-text, "
        "h1[class*='home-panel-content-view__header-text']"
    )
    if h:
        city = norm(h.get_text())
        return city or None
    return None


def click_open_on_yandex_maps(drv) -> bool:
    """
    На странице события:
      1) кликаем «Показать на карте»
      2) кликаем «Открыть в Яндекс.Картах»
    Возвращает True, если второй клик удалось сделать.
    """
    try:
        btn = WebDriverWait(drv, 10).until(
            EC.presence_of_element_located(
                (By.XPATH, "//*[contains(text(),'Показать на карте')]")
            )
        )
        log.info("Нашёл «Показать на карте»: tag=%s, class=%s",
                 btn.tag_name, btn.get_attribute("class"))

        try:
            drv.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", btn
            )
            time.sleep(1.3)
        except Exception:
            pass

        try:
            btn.click()
        except Exception:
            drv.execute_script("arguments[0].click();", btn)

        log.info("Кликнул «Показать на карте». Жду появление оверлея…")
        time.sleep(2.0)
    except TimeoutException:
        log.error("Не нашёл элемент с текстом «Показать на карте».")
        return False
    except Exception as e:
        log.warning("Ошибка при поиске/клике «Показать на карте»: %s", e)
        return False
    try:
        ymaps_el = WebDriverWait(drv, 10).until(
            EC.presence_of_element_located(
                (By.XPATH, "//*[contains(text(),'Открыть в Яндекс-Картах') or contains(text(),'Открыть в Яндекс.Картах')]")
            )
        )
        log.info("Нашёл «Открыть в Яндекс-Картах»: tag=%s, class=%s",
                 ymaps_el.tag_name, ymaps_el.get_attribute("class"))

        try:
            drv.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", ymaps_el
            )
            time.sleep(1.3)
        except Exception:
            pass

        try:
            ymaps_el.click()
        except Exception:
            drv.execute_script("arguments[0].click();", ymaps_el)

        log.info("Кликнул «Открыть в Яндекс-Картах».")
        return True
    except TimeoutException:
        log.error("Не нашёл «Открыть в Яндекс-Картах» после клика по карте.")
        return False
    except Exception as e:
        log.error("Ошибка при клике «Открыть в Яндекс-Картах»: %s", e)
        return False


def try_get_city_from_yandex(drv) -> Optional[str]:
    """
    На странице события:
      - кликает по карте
      - открывает Яндекс.Карты
      - достаёт город из заголовка
      - закрывает вкладку Яндекс.Карт (если она открылась) и возвращается назад
    """
    original = drv.current_window_handle
    handles_before = set(drv.window_handles)

    if not click_open_on_yandex_maps(drv):
        return None
    for _ in range(20):
        time.sleep(1.4)
        if len(drv.window_handles) > len(handles_before):
            break

    new_tab = None
    handles_after = set(drv.window_handles)
    extra = handles_after - handles_before
    if extra:
        new_tab = extra.pop()

    if new_tab:
        log.info("Открылась новая вкладка Яндекс.Карт.")
        drv.switch_to.window(new_tab)
    else:
        log.info("Новая вкладка не появилась — возможно, Яндекс.Карты открылись в этом же окне.")
        drv.switch_to.window(original)

    city = None
    try:
        try:
            wait_ready(drv, 30)
        except TimeoutException:
            pass
        time.sleep(1.0)
        html = drv.page_source or ""
        city = read_city_from_yamaps_html(html)
        if city:
            log.info("Город из Яндекс-Карт: %s", city)
        else:
            log.warning("Не удалось извлечь город со страницы Яндекс-Карт.")
    except WebDriverException as e:
        log.error("WebDriverException при чтении Яндекс-Карт: %s", e)
    finally:
        if new_tab:
            try:
                drv.close()
            except Exception:
                pass
            try:
                drv.switch_to.window(original)
            except Exception:
                pass
        else:
            try:
                drv.switch_to.window(original)
            except Exception:
                pass

    return city


def extract_from_detail(html: str, url: str) -> Dict:
    soup = BeautifulSoup(html, "lxml")
    data = try_next_data(html)

    rec = {k: "" for k in [
        "title", "date_iso", "time_start", "time_end", "datetime_raw",
        "address_full", "city", "region",
        "organizer_name", "organizer_url",
        "contact_name", "contact_position", "contact_phone", "contact_vk",
        "description", "url"
    ]}
    rec["url"] = url
    if data:
        candidates = [
            data.get("props", {}).get("pageProps", {}).get("event", {}),
            data.get("props", {}).get("pageProps", {}).get("initialState", {}).get("event", {}),
            data.get("props", {}).get("pageProps", {}).get("deed", {}),
        ]
        event = next((c for c in candidates if isinstance(c, dict) and c), {})
        if event:
            rec["title"] = (event.get("title") or event.get("name") or "").strip()

            start_iso = event.get("startDateTime") or event.get("dateStart") or ""
            end_iso = event.get("endDateTime") or event.get("dateEnd") or ""

            def split_iso(dt):
                if not dt:
                    return "", ""
                return dt[:10], (dt[11:16] if "T" in dt else "")

            d1, t1 = split_iso(start_iso)
            d2, t2 = split_iso(end_iso)
            rec["date_iso"] = d1 or d2
            rec["time_start"] = t1
            rec["time_end"] = t2
            rec["datetime_raw"] = (start_iso + " — " + end_iso).strip(" —")

            place = event.get("place") or event.get("location") or {}
            addr = place.get("address") or {}
            parts = []
            for k in ["region", "city", "street", "house", "addressLine"]:
                v = addr.get(k) or place.get(k)
                if v:
                    parts.append(str(v))
            rec["address_full"] = ", ".join(parts)
            rec["city"] = addr.get("city") or ""
            rec["region"] = addr.get("region") or ""

            org = event.get("organization") or event.get("organizer") or {}
            rec["organizer_name"] = org.get("name") or ""
            rec["organizer_url"] = org.get("url") or ""

            contact = event.get("contact") or {}
            rec["contact_name"] = contact.get("name") or ""
            rec["contact_position"] = contact.get("position") or ""
            rec["contact_phone"] = contact.get("phone") or ""
            rec["contact_vk"] = contact.get("vk") or ""
            rec["description"] = (event.get("description") or "").strip()
    if not rec["title"]:
        el = soup.select_one("h1, h2.EventInfo_event-title__3DHyd, h2[class*='EventInfo_event-title']")
        if el:
            rec["title"] = norm(el.get_text())

    if not rec["address_full"]:
        el = soup.select_one(
            "span.CardTypes_card-location__title__aCIPk, span[class*='card-location__title']"
        )
        if el:
            rec["address_full"] = norm(el.get_text())

    if not rec["city"]:
        m = re.search(
            r"(?:г\.?\s*|город\s+)([^,]+)",
            rec["address_full"],
            re.IGNORECASE
        )
        if m:
            rec["city"] = norm(m.group(1))

    if not any([rec["date_iso"], rec["time_start"], rec["time_end"]]):
        el = soup.select_one(
            "span.CardTypes_card-time__title__QoS6L, span[class*='card-time__title']"
        )
        text = norm(el.get_text()) if el else norm(soup.get_text())[:3000]
        rec["datetime_raw"] = text
        rec["date_iso"] = ru_date_to_iso(text) or ""
        t1, t2 = extract_times(text)
        rec["time_start"], rec["time_end"] = t1 or "", t2 or ""

    if not rec["organizer_name"]:
        org = soup.select_one(
            ".EventInfo_event__organization__EdRYe, .EventInfo_event-info__organizer-title__owGDk"
        )
        if org:
            rec["organizer_name"] = norm(org.get_text())
    if not rec["organizer_url"]:
        a = soup.select_one("a[href*='/organizations/']")
        if a:
            rec["organizer_url"] = urljoin("https://dobro.ru", a.get("href"))

    if not rec["contact_name"]:
        el = soup.select_one(
            ".EventContacts_event-contacts__contact-name__DtYJx, [class*='event-contacts__contact-name']"
        )
        if el:
            rec["contact_name"] = norm(el.get_text())
    if not rec["contact_position"]:
        el = soup.select_one(
            ".EventContacts_event-contacts__contact-position__7w0Zr, [class*='event-contacts__contact-position']"
        )
        if el:
            rec["contact_position"] = norm(el.get_text())
    if not rec["contact_phone"]:
        el = soup.select_one(
            ".EventContacts_event-contacts__phone-text__NuFca, [class*='event-contacts__phone-text']"
        )
        if el:
            rec["contact_phone"] = norm(el.get_text())
    if not rec["contact_vk"]:
        a = soup.select_one(
            ".SocialMediaBlock_socials__GFSLa a[href^='https://vk.com/']"
        )
        if a:
            rec["contact_vk"] = a.get("href")

    if not rec["description"]:
        el = soup.select_one(
            ".EventInfo_event-description__text__XCVRW, [class*='event-description__text']"
        )
        if el:
            rec["description"] = norm(el.get_text())

    return rec


def expand_description(drv, timeout=6) -> bool:
    """
    Кликает «Показать полностью» на странице мероприятия.
    """
    xpaths = [
        "//span[contains(@class,'TextWithShowMore_show-more') and contains(.,'Показать полностью')]",
        "//button[.//span[contains(.,'Показать полностью')] or contains(.,'Показать полностью')]",
        "//a[contains(.,'Показать полностью')]",
    ]

    btn = None
    for xp in xpaths:
        els = drv.find_elements(By.XPATH, xp)
        if els:
            btn = els[0]
            break
    if not btn:
        log.info("Кнопка «Показать полностью» не найдена — возможно, текст уже раскрыт.")
        return False

    try:
        drv.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        time.sleep(0.25)
        try:
            btn.click()
        except (ElementClickInterceptedException, StaleElementReferenceException):
            drv.execute_script("arguments[0].click();", btn)
        log.info("Нажал «Показать полностью». Жду раскрытие…")
    except Exception as e:
        log.warning("Не удалось нажать «Показать полностью»: %s", e)
        return False

    try:
        WebDriverWait(drv, timeout).until(
            lambda d: len(d.find_elements(
                By.CSS_SELECTOR,
                ".EventInfo_event-description__text--hidden___lkKa"
            )) == 0
        )
        time.sleep(1.2)
        return True
    except Exception:
        return True


def parse_detail(drv, url: str) -> Dict:
    log.info("Открываю карточку: %s", url)
    try:
        drv.get(url)
        wait_ready(drv, 45)
        time.sleep(1.8)

        if expand_description(drv):
            log.info("Описание раскрыто.")

        html = drv.page_source or ""
        rec = extract_from_detail(html, url)

        if not rec.get("city"):
            log.info("   → город пуст в карточке, пробую Яндекс.Карты…")
            city_from_ymaps = try_get_city_from_yandex(drv)
            if city_from_ymaps:
                rec["city"] = city_from_ymaps
                if not rec.get("address_full"):
                    rec["address_full"] = city_from_ymaps

        log.info("   └ title: %s", rec["title"] or "—")
        log.info("   └ addr : %s", rec["address_full"] or "—")
        log.info("   └ city : %s", rec["city"] or "—")
        log.info("   └ date : %s  time: %s–%s", rec["date_iso"] or "—",
                 rec["time_start"] or "—", rec["time_end"] or "—")
        log.info("   └ org  : %s | %s", rec["organizer_name"] or "—",
                 rec["organizer_url"] or "—")
        log.info("   └ cnt  : %s (%s) %s %s",
                 rec["contact_name"] or "—", rec["contact_position"] or "—",
                 rec["contact_phone"] or "—", rec["contact_vk"] or "—")
        log.info("   └ desc : %d символов", len(rec.get("description", "")))

        if not rec["title"]:
            log.warning("   ! Заголовок не извлечён (%s)", url)
        return rec
    except TimeoutException:
        log.error("Timeout при открытии %s", url)
    except WebDriverException as e:
        log.error("WebDriverException для %s: %s", url, e)

    return {
        "title": "", "date_iso": "", "time_start": "", "time_end": "",
        "datetime_raw": "", "address_full": "", "city": "", "region": "",
        "organizer_name": "", "organizer_url": "",
        "contact_name": "", "contact_position": "",
        "contact_phone": "", "contact_vk": "",
        "description": "", "url": url
    }

def empty_to_none(x):
    if x is None:
        return None
    x = str(x).strip()
    return x if x else None


def rec_to_object(rec: Dict) -> Dict:
    return {
        "title": empty_to_none(rec.get("title")),
        "url": empty_to_none(rec.get("url")),
        "schedule": {
            "date": empty_to_none(rec.get("date_iso")),
            "time_start": empty_to_none(rec.get("time_start")),
            "time_end": empty_to_none(rec.get("time_end")),
            "datetime_raw": empty_to_none(rec.get("datetime_raw")),
        },
        "location": {
            "address_full": empty_to_none(rec.get("address_full")),
            "city": empty_to_none(rec.get("city")),
            "region": empty_to_none(rec.get("region")),
        },
        "organizer": {
            "name": empty_to_none(rec.get("organizer_name")),
            "url": empty_to_none(rec.get("organizer_url")),
        },
        "contact": {
            "name": empty_to_none(rec.get("contact_name")),
            "position": empty_to_none(rec.get("contact_position")),
            "phone": empty_to_none(rec.get("contact_phone")),
            "vk": empty_to_none(rec.get("contact_vk")),
        },
        "description": empty_to_none(rec.get("description")),
    }


def main():
    try:
        drv = driver_build()
    except Exception as e:
        log.critical("Браузер не стартовал: %s", e)
        return

    log.info("Открываю ленту: %s", BASE_URL)
    drv.get(BASE_URL)
    wait_ready(drv, 60)
    time.sleep(1.0)

    click_show_more_until_end(drv)
    links = collect_detail_links_from_feed(drv.page_source or "")
    if not links:
        log.error("Ссылок «Подробнее» не найдено. Проверьте верстку/тексты.")
        drv.quit()
        return

    events: List[Dict] = []
    ok = fail = 0

    for i, url in enumerate(links, 1):
        rec = parse_detail(drv, url)
        if rec.get("title"):
            ok += 1
        else:
            fail += 1
        events.append(rec)
        log.info("ОБРАБОТАНО [%d/%d] ok=%d fail=%d", i, len(links), ok, fail)

    drv.quit()
    log.info("Парсинг завершён. Всего записей: %d", len(events))
    events_sorted = sorted(
        events,
        key=lambda r: (
            r.get("date_iso") or "9999-12-31",
            r.get("time_start") or "99:99",
            r.get("title") or ""
        )
    )

    data = [rec_to_object(r) for r in events_sorted]

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=False)

    print(f"Готово: {OUT_JSON} ({len(data)} записей)")
    log.info("ГОТОВО: %s (%d записей)", OUT_JSON, len(data))


if __name__ == "__main__":
    main()
