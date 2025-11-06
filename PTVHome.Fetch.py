from datetime import datetime
import pytz
import time
import json
import os
import requests
import argparse
import sys
from datetime import timedelta

def _compute_weekday_date(base_dt, target_day_name):
    """Return datetime in same week as base_dt for the target weekday name (Mon-Sun)."""
    day_map = {
        'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3,
        'Friday': 4, 'Saturday': 5, 'Sunday': 6
    }
    target_idx = day_map.get(target_day_name)
    if target_idx is None:
        raise ValueError(f"Unknown day name: {target_day_name}")
    current_idx = base_dt.weekday()  # Monday=0
    delta_days = target_idx - current_idx
    target_dt = base_dt + timedelta(days=delta_days)
    # Normalize time to midday to avoid edge cases; keep tz info
    target_dt = target_dt.replace(hour=12, minute=0, second=0, microsecond=0)
    return target_dt

def build_dynamic_url(base_dt, override_day=None):
    target_day = override_day or base_dt.strftime('%A')
    try:
        target_dt = _compute_weekday_date(base_dt, target_day)
    except Exception:
        target_dt = base_dt
    day_of_week = target_day
    day_abbr = target_dt.strftime('%a')
    month_abbr = target_dt.strftime('%b')
    day = target_dt.strftime('%d')
    year = target_dt.strftime('%Y')
    hour = target_dt.strftime('%H')
    minute = target_dt.strftime('%M')
    second = target_dt.strftime('%S')

    print(f"Formatted components: DayOfWeek={day_of_week}, DayAbbr={day_abbr}, MonthAbbr={month_abbr}, Day={day}, Year={year}, Hour={hour}, Minute={minute}, Second={second}")

    url = (
        f"https://ptv.com.pk/tvguidemaster?channelid=3&dayofweek={day_of_week}"
        f"&date={day_abbr}%20{month_abbr}%20{day}%20{year}%20{hour}%3A{minute}%3A{second}%20GMT%2B0500%20(Pakistan%20Standard%20Time)"
    )
    print(f"Generated URL: {url}")
    return url

def save_cookies(driver, path="ptv_cookies.json"):
    try:
        cookies = driver.get_cookies()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        print(f"Saved {len(cookies)} cookies to {path}")
    except Exception as e:
        print(f"Failed to save cookies: {e}")

def load_cookies(path="ptv_cookies.json"):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            print(f"Loaded {len(cookies)} cookies from {path}")
            return cookies
        except Exception as e:
            print(f"Failed to load cookies: {e}")
    return []

def fetch_with_requests(url, cookies_file="ptv_cookies.json"):
    print("Attempting fetch via requests with persisted cookies...")
    session = requests.Session()
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://ptv.com.pk/',
        'Connection': 'keep-alive',
    }
    cookies = load_cookies(cookies_file)
    for c in cookies:
        # requests expects cookie fields: name, value; domain optional
        if 'name' in c and 'value' in c:
            session.cookies.set(c['name'], c['value'], domain=c.get('domain', 'ptv.com.pk'))

    resp = session.get(url, headers=headers, timeout=60)
    print(f"Requests status: {resp.status_code}")
    return resp.text

def fetch_ptv_home_schedule(no_browser=False):
    """
    Fetches the PTV Home schedule by opening the tvguide page with Selenium,
    then iterating all weekday tabs (Monday–Sunday) and saving each day's HTML.
    """
    try:
        # Set the timezone to Pakistan Standard Time
        tz = pytz.timezone('Asia/Karachi')
        now = datetime.now(tz)
        print(f"Current time in Asia/Karachi: {now}")

        if not no_browser:
            try:
                # Lazy import to allow server mode when uc isn't installed
                import undetected_chromedriver as uc
                from selenium.webdriver.common.by import By
                from selenium.webdriver.support.ui import WebDriverWait
                from selenium.webdriver.support import expected_conditions as EC

                # Use undetected-chromedriver to reduce Cloudflare detection
                options = uc.ChromeOptions()
                options.add_argument('--disable-blink-features=AutomationControlled')
                options.add_argument('--no-sandbox')
                options.add_argument('--disable-dev-shm-usage')
                # Headless toggle for CI environments (GitHub Actions, etc.)
                headless = os.environ.get('HEADLESS', '0').lower() in ('1','true','yes')
                if headless:
                    options.add_argument('--headless=new')
                    options.add_argument('--disable-gpu')
                    options.add_argument('--window-size=1280,1024')
                options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36')

                print("Launching undetected Chrome...")
                driver = uc.Chrome(options=options)
                
                # Open actual tvguide page that triggers internal calls and sets cookies
                tvguide_url = 'https://ptv.com.pk/ptvhome/tvguide'
                print("Opening tvguide page...")
                driver.get(tvguide_url)

                # Wait for schedule container to render or Cloudflare to clear
                try:
                    WebDriverWait(driver, 30).until(
                        EC.any_of(
                            EC.presence_of_element_located((By.CSS_SELECTOR, 'div.tab-pane.tab-item')),
                            EC.presence_of_element_located((By.CSS_SELECTOR, 'div.rt-post'))
                        )
                    )
                except Exception:
                    print("Initial wait timed out; checking for challenge and waiting more...")
                    time.sleep(20)

                print(f"Page title after load: {driver.title}")

                # Save cookies for future non-browser fetches
                save_cookies(driver)

                # Prepare day names and outputs
                day_names = [
                    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
                ]
                combined_contents = []

                # Helper to find the active tab content element
                def get_active_tab():
                    tabs = driver.find_elements(By.CSS_SELECTOR, 'div.tab-pane.tab-item.show.active')
                    if tabs:
                        return tabs[0]
                    # fallback: pick first pane
                    all_tabs = driver.find_elements(By.CSS_SELECTOR, 'div.tab-pane.tab-item')
                    return all_tabs[0] if all_tabs else None

                # Ensure we have an initial active tab before iteration
                current_active = get_active_tab()

                for day in day_names:
                    print(f"Switching to day tab: {day}")
                    clicked = False
                    # Try clicking by link text or XPath containing the day name
                    try:
                        # Prefer exact link text if available
                        elements = driver.find_elements(By.LINK_TEXT, day)
                        target = None
                        if elements:
                            target = elements[0]
                        else:
                            # More generic: any anchor or button with the day name
                            xpath = (
                                f"//a[contains(@class,'nav-link') and contains(translate(normalize-space(.), 'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'), '{day.upper()}')]"
                                f" | //button[contains(@class,'nav-link') and contains(translate(normalize-space(.), 'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'), '{day.upper()}')]"
                            )
                            found = driver.find_elements(By.XPATH, xpath)
                            if found:
                                target = found[0]

                        if target:
                            WebDriverWait(driver, 15).until(EC.element_to_be_clickable(target))
                            driver.execute_script("arguments[0].click();", target)
                            clicked = True
                            print(f"Clicked day tab: {day}")
                        else:
                            print(f"Day tab not found via selectors for: {day}")
                    except Exception as e:
                        print(f"Click failed for {day}: {e}")

                    # Wait for content update: either staleness of previous active or presence of active pane
                    try:
                        if current_active:
                            WebDriverWait(driver, 20).until(EC.staleness_of(current_active))
                        WebDriverWait(driver, 20).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, 'div.tab-pane.tab-item.show.active'))
                        )
                    except Exception:
                        print(f"Content update wait timed out for {day}; proceeding with available content")

                    # Capture the active tab's HTML
                    current_active = get_active_tab()
                    if current_active:
                        schedule_html = current_active.get_attribute('innerHTML')
                    else:
                        schedule_html = driver.page_source

                    # Save per-day file
                    out_name = f"PTVHome.Schedule.{day}.txt"
                    with open(out_name, "w", encoding="utf-8") as f:
                        f.write(schedule_html)
                    print(f"Saved {day} HTML to {out_name}")

                    combined_contents.append(f"<!-- {day} -->\n" + schedule_html)

                    # If content looks like challenge, attempt requests fallback for that day
                    if 'Just a moment' in schedule_html or 'challenge-platform' in schedule_html:
                        print(f"Detected challenge for {day}; attempting requests fallback...")
                        url = build_dynamic_url(now, override_day=day)
                        text = fetch_with_requests(url)
                        with open(out_name, "w", encoding="utf-8") as f:
                            f.write(text)
                        print(f"Fallback content saved for {day} to {out_name}")

                # Also write a combined file with all days
                with open("PTVHome.Schedule.All.txt", "w", encoding="utf-8") as f:
                    f.write("\n\n".join(combined_contents))
                print("Saved combined schedule to PTVHome.Schedule.All.txt")

                driver.quit()
                return
            except Exception as e:
                print(f"Browser path unavailable or failed, switching to server mode: {e}")

        # --- Server-friendly mode using cloudscraper ---
        import cloudscraper
        print("Starting server-friendly fetch with Cloudscraper (no browser)...")
        scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'linux',
                'mobile': False
            }
        )
        headers = {
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://ptv.com.pk/',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
        }

        tvguide_url = 'https://ptv.com.pk/ptvhome/tvguide'
        try:
            _ = scraper.get(tvguide_url, headers=headers, timeout=60)
            print("Primed session by visiting tvguide page.")
        except Exception as e:
            print(f"Warning: could not prime tvguide page: {e}")

        # Retry helper to mitigate Cloudflare challenge pages
        def fetch_with_retries(url, headers, max_retries=3):
            last_err = None
            for i in range(1, max_retries + 1):
                try:
                    resp = scraper.get(url, headers=headers, timeout=60)
                    txt = resp.text or ""
                    print(f"[retry {i}] GET {url} -> {resp.status_code}, len={len(txt)}")
                    if resp.status_code == 200 and ('Just a moment' not in txt) and ('challenge-platform' not in txt):
                        return resp
                    time.sleep(3)
                except Exception as e:
                    print(f"[retry {i}] error: {e}")
                    last_err = e
                    time.sleep(3)
            if last_err:
                raise last_err
            raise RuntimeError("Failed to bypass challenge after retries")

        # Attempt to parse all day tabs directly from tvguide page
        print("Fetching full tvguide page markup for parsing...")
        try:
            page_resp = fetch_with_retries(tvguide_url, headers={**headers, 'Referer': tvguide_url})
            page_status = page_resp.status_code
            page_html = page_resp.text or ""
            print(f"[tvguide] HTTP {page_status}, length={len(page_html)}")
            if page_status != 200 or len(page_html.strip()) < 500:
                print("tvguide markup too short or non-200; aborting.")
                sys.exit(1)
        except Exception as e:
            print(f"Error fetching tvguide page: {e}")
            sys.exit(1)

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(page_html, 'html.parser')
        nav_links = soup.select('a.nav-link')
        print(f"Found {len(nav_links)} nav links.")
        id_map = {}
        for a in nav_links:
            text = (a.get_text(strip=True) or "").title()
            href = a.get('href') or ''
            if text and href.startswith('#'):
                id_map[text] = href[1:]
        print(f"Day to tab-id map: {id_map}")

        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        combined_contents = []
        for day in day_names:
            tab_id = id_map.get(day)
            if not tab_id:
                print(f"No tab id mapped for {day}; aborting.")
                sys.exit(1)
            pane = soup.select_one(f"div.tab-pane.tab-item#{tab_id}")
            if not pane:
                print(f"Pane not found for id {tab_id} ({day}); aborting.")
                sys.exit(1)
            html = pane.decode_contents() or ""
            html = html.strip()
            print(f"[{day}] extracted length from tvguide={len(html)}")
            if len(html) < 100:
                print(f"[{day}] tvguide content too short; attempting tvguidemaster endpoint...")
                ajax_url = build_dynamic_url(now, override_day=day)
                ajax_headers = {
                    **headers,
                    'Referer': tvguide_url,
                    'Origin': 'https://ptv.com.pk',
                    'X-Requested-With': 'XMLHttpRequest',
                    'Accept': 'text/html, */*; q=0.01',
                }
                try:
                    ajax_resp = fetch_with_retries(ajax_url, headers=ajax_headers)
                    ajax_html = ajax_resp.text or ""
                    print(f"[{day}] tvguidemaster HTTP {ajax_resp.status_code}, len={len(ajax_html)}")
                    if ajax_resp.status_code != 200 or len(ajax_html.strip()) < 100:
                        print(f"[{day}] tvguidemaster content invalid; aborting.")
                        sys.exit(1)
                    html = ajax_html
                except Exception as e:
                    print(f"[{day}] tvguidemaster error: {e}")
                    sys.exit(1)
            out_name = f"PTVHome.Schedule.{day}.txt"
            try:
                with open(out_name, "w", encoding="utf-8") as f:
                    f.write(html)
                print(f"Saved {day} HTML to {out_name}")
                try:
                    size = os.path.getsize(out_name)
                    print(f"[{day}] file size={size}")
                    if size < 100:
                        print(f"[{day}] output file too small; aborting to avoid empty artifacts.")
                        sys.exit(1)
                except Exception as e:
                    print(f"[{day}] could not stat output file: {e}")
                    sys.exit(1)
            except Exception as e:
                print(f"Error writing file {out_name}: {e}")
                sys.exit(1)
            combined_contents.append(f"<!-- {day} -->\n" + html)

        try:
            with open("PTVHome.Schedule.All.txt", "w", encoding="utf-8") as f:
                f.write("\n\n".join(combined_contents))
            print("Saved combined schedule to PTVHome.Schedule.All.txt (server mode)")
        except Exception as e:
            print(f"Error writing combined file: {e}")
            sys.exit(1)

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch PTV Home schedule")
    parser.add_argument('--no-browser', action='store_true', help='Force server-friendly mode using cloudscraper')
    args = parser.parse_args()
    fetch_ptv_home_schedule(no_browser=args.no_browser)