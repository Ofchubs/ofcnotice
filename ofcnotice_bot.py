import os
import re
import html
import logging
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SENT_NOTICES_FILE = "sent_notices.txt"
URLS_FILE = "ofcnoticeurls.txt"

# দপ্তর অনুযায়ী নাম নির্ধারণ
DEPT_NAME_MAP = {
    "mopa.gov.bd": "জনপ্রশাসন মন্ত্রণালয়",
    "lgd.gov.bd": "স্থানীয় সরকার বিভাগ",
    "mof.gov.bd": "অর্থ বিভাগ",
    "cabinet.gov.bd": "মন্ত্রিপরিষদ বিভাগ",
    "barisaldiv.gov.bd": "বিভাগীয় কমিশনারের কার্যালয়, বরিশাল",
    "dpp.gov.bd": "বাংলাদেশ সরকারি মুদ্রণালয় (বিজি প্রেস)",
}

EN_TO_BN_NUM = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")

MONTH_MAP = {
    "Jan": "জানুয়ারি", "Feb": "ফেব্রুয়ারি", "Mar": "মার্চ", "Apr": "এপ্রিল",
    "May": "মে", "Jun": "জুন", "Jul": "জুলাই", "Aug": "আগস্ট",
    "Sep": "সেপ্টেম্বর", "Oct": "অক্টোবর", "Nov": "নভেম্বর", "Dec": "ডিসেম্বর",
    "January": "জানুয়ারি", "February": "ফেব্রুয়ারি", "March": "মার্চ",
    "April": "এপ্রিল", "June": "জুন", "July": "জুলাই", "August": "আগস্ট",
    "September": "সেপ্টেম্বর", "October": "অক্টোবর", "November": "নভেম্বর", "December": "ডিসেম্বর"
}

def format_to_bangla_date(date_str):
    if not date_str:
        return None
    clean = date_str.translate(EN_TO_BN_NUM)
    for en_m, bn_m in MONTH_MAP.items():
        clean = re.sub(rf'\b{en_m}\b', bn_m, clean, flags=re.IGNORECASE)
    return clean.strip()

def get_current_bd_datetime():
    bd_dt = datetime.now(timezone.utc) + timedelta(hours=6)
    day = bd_dt.strftime("%d").translate(EN_TO_BN_NUM)
    month_en = bd_dt.strftime("%B")
    month = MONTH_MAP.get(month_en, month_en)
    year = bd_dt.strftime("%Y").translate(EN_TO_BN_NUM)
    time_str = bd_dt.strftime("%I.%M").translate(EN_TO_BN_NUM)
    ampm = "সকাল" if bd_dt.hour < 12 else "বিকাল" if bd_dt.hour < 17 else "সন্ধ্যা" if bd_dt.hour < 20 else "রাত"
    return f"{day} {month} {year}; {ampm} {time_str} টা"

def load_sent_notices():
    if os.path.exists(SENT_NOTICES_FILE):
        with open(SENT_NOTICES_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_sent_notice(notice_id):
    with open(SENT_NOTICES_FILE, "a", encoding="utf-8") as f:
        f.write(f"{notice_id}\n")

def get_site_name(url):
    domain = urlparse(url).netloc.replace("www.", "").lower()
    return DEPT_NAME_MAP.get(domain, domain.upper())

def send_telegram_msg(title, pdf_url, site_name, display_time):
    clean_title = html.escape(title.strip())
    clean_site_name = html.escape(site_name.strip())
    clean_time = html.escape(display_time.strip())
    
    message = (
        f"🏛 <b>{clean_site_name}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<blockquote expandable>"
        f"📌 <b>শিরোনাম/গেজেট:</b>\n{clean_title}"
        f"</blockquote>\n\n"
        f"🕒 <b>প্রকাশের তারিখ ও সময়:</b>\n<code>{clean_time}</code>\n\n"
        f"🔗 <a href='{pdf_url}'><b>📄 পিডিএফ ডাউনলোড/বিস্তারিত দেখুন</b></a>"
    )
    
    telegram_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    
    try:
        res = requests.post(telegram_url, json=payload, timeout=15)
        res.raise_for_status()
        logging.info(f"Successfully sent to Telegram: {title}")
        return True
    except Exception as e:
        logging.error(f"Telegram error: {e}")
        return False

def scrape_site(url, sent_notices):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    
    max_retries = 3
    response = None
    
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=15, verify=False)
            if response.status_code == 200:
                break
        except Exception as e:
            logging.warning(f"Attempt {attempt + 1} failed for {url}: {e}")
            if attempt == max_retries - 1:
                logging.error(f"Failed to fetch {url}")
                return

    if not response or response.status_code != 200:
        return

    soup = BeautifulSoup(response.text, 'html.parser')
    site_name = get_site_name(url)
    found_count = 0

    # ১. বিজি প্রেস (dpp.gov.bd)
    if "dpp.gov.bd" in url:
        links = soup.find_all('a', href=True)
        for a in links:
            href = a['href'].strip()
            if any(x in href.lower() for x in ['.pdf', 'upload_file', 'gazette']):
                text = a.get_text(strip=True)
                parent_tr = a.find_parent('tr') or a.find_parent('div')
                title = text
                found_date = ""
                
                if parent_tr:
                    txt_block = parent_tr.get_text(" ", strip=True)
                    date_match = re.search(r'(\d{1,4}[-/\.\s]\d{1,2}[-/\.\s]\d{2,4})|(\d{1,2}\s+[A-Za-z\u0980-\u09FF]+\s+\d{4})', txt_block)
                    if date_match:
                        found_date = date_match.group(0)
                    if len(title) < 5 or title in ["ডাউনলোড", "Download", "View", "দেখুন"]:
                        title = txt_block

                if title and len(title) > 3:
                    full_pdf_url = urljoin(url, href)
                    notice_id = re.sub(r'[^a-zA-Z0-9]', '', full_pdf_url)

                    if notice_id not in sent_notices:
                        display_time = format_to_bangla_date(found_date) or get_current_bd_datetime()
                        if send_telegram_msg(title, full_pdf_url, site_name, display_time):
                            sent_notices.add(notice_id)
                            save_sent_notice(notice_id)
                            found_count += 1

    # ২. জাতীয় তথ্য বাতায়ন পোর্টালে (barisaldiv, mopa, lgd, mof, cabinet ইত্যাদির জন্য উন্নত স্ক্র্যাপিং)
    else:
        # পেজের সব টেবিল রো চেক করা
        rows = soup.find_all('tr')
        for row in rows:
            anchors = row.find_all('a', href=True)
            if not anchors:
                continue

            tds = row.find_all(['td', 'th'])
            title = ""
            file_link = ""
            found_date = ""

            # কলামগুলো স্ক্যান করা
            for td in tds:
                txt = td.get_text(strip=True)
                # তারিখ ম্যাচিং
                if not found_date:
                    date_match = re.search(r'(\d{1,4}[-/\.\s]\d{1,2}[-/\.\s]\d{2,4})|(\d{1,2}\s+[A-Za-z\u0980-\u09FF]+\s+\d{4})', txt)
                    if date_match:
                        found_date = date_match.group(0)

            for a in anchors:
                href = a['href'].strip()
                text = a.get_text(strip=True)

                # লিঙ্ক ফিল্টারিং
                if any(x in href.lower() for x in ['.pdf', 'download', 'site/view', 'site/notices', 'node/', 'pages/', 'files/']):
                    file_link = href
                    if len(text) > 3 and text not in ["দেখুন", "ডাউনলোড", "Download", "View", "বিস্তারিত"]:
                        title = text

            # শিরোনাম অ্যাঙ্কর ট্যাগে সরাসরি না থাকলে কলাম থেকে টেক্সট নেওয়া
            if not title:
                for td in tds:
                    txt = td.get_text(strip=True)
                    if len(txt) > 5 and txt not in ["দেখুন", "ডাউনলোড", "Download", "View", "বিস্তারিত"] and txt != found_date:
                        title = txt
                        break

            if title and file_link:
                full_pdf_url = urljoin(url, file_link)
                notice_id = re.sub(r'[^a-zA-Z0-9]', '', full_pdf_url)

                if notice_id not in sent_notices:
                    display_time = format_to_bangla_date(found_date) or get_current_bd_datetime()
                    if send_telegram_msg(title, full_pdf_url, site_name, display_time):
                        sent_notices.add(notice_id)
                        save_sent_notice(notice_id)
                        found_count += 1

    logging.info(f"Processed {url} — Sent {found_count} new notices.")

def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.error("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID!")
        return

    sent_notices = load_sent_notices()
    
    if os.path.exists(URLS_FILE):
        with open(URLS_FILE, "r", encoding="utf-8") as f:
            urls = [line.strip() for line in f if line.strip()]
        
        for url in urls:
            logging.info(f"Scraping: {url}")
            scrape_site(url, sent_notices)
    else:
        logging.error(f"{URLS_FILE} file missing!")

if __name__ == "__main__":
    requests.packages.urllib3.disable_warnings()
    main()
