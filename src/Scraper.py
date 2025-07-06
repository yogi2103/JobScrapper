import os
import requests
from bs4 import BeautifulSoup
import re
import time
import json
from datetime import datetime

# --------------------------
# ✅ Configurations
# --------------------------

company_names = [
    "Uber", "Expedia", "Razorpay", "Rubrik", "Atlassian", "Amazon", "Google", "Intuit", "Meta", "Microsoft",
    "Coinbase", "ThoughtSpot", "Cred", "Oracle", "Goldman Sachs", "Paypal", "LinkedIn", "Airbnb", "DoorDash",
    "Salesforce", "Confluent", "Couchbase", "Stripe", "Docusign", "Agoda", "Visa", "Twilio", "Okta", "Cohesity",
    "Swiggy", "Rippling", "Intervue.io", "Deliveroo", "Remitly"
]

tech_keywords = [
    "java", "node", "react", "reactjs", "aws", "cloud", "microservices",
    "distributed systems", "api", "rest", "docker",
    "jenkins", "event-driven", "lambda", "ec2", "spring",
    "javascript", "typescript", "html", "css",
    "graphql", "restful", "jest", "mocha", "cypress", "puppeteer"
]

title_keywords = [kw.lower() for kw in [
    "engineer", "sde", "swe", "developer", "full stack",
    "mts", "ui", "web", "member of technical staff", "software"
]]

# Telegram bot configuration (injected via GitHub Actions secrets)
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

FC_CACHE_FILE  = "company_fc_cache.json"
SEEN_JOBS_FILE = "seen_jobs_dict.json"

# --------------------------
# ✅ Experience Regex
# --------------------------

EXPERIENCE_RE = re.compile(r"""\b
    (?:(?:minimum|min\.?|at\ least)\s*)?
    (?P<min>\d+(?:\.\d+)?)
    (?:\s*(?:-|to|–)\s*(?P<max>\d+(?:\.\d+)?))?
    (?:\s*(?:\+|plus))?
    \s*years?\b
""", re.IGNORECASE | re.VERBOSE)

# --------------------------
# ✅ Utility Functions
# --------------------------

def ensure_file(filename):
    """
    Ensure that a JSON file exists. If not, create it with empty dict.
    """
    if not os.path.exists(filename):
        with open(filename, "w") as f:
            json.dump({}, f, indent=2)

def load_json(filename):
    if os.path.exists(filename):
        with open(filename, "r") as f:
            return json.load(f)
    return {}

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

def get_company_id_from_page(company_name, fc_cache):
    if company_name in fc_cache:
        return fc_cache[company_name]
    url = f"https://www.linkedin.com/company/{company_name.lower()}"
    headers = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.raise_for_status()
        snippet = "\n".join(res.text.splitlines()[:500])
        m = re.search(r'data-semaphore-content-urn="urn:li:organization:(\d+)"', snippet)
        if m:
            fc = m.group(1)
            fc_cache[company_name] = fc
            save_json(FC_CACHE_FILE, fc_cache)
            return fc
    except Exception:
        return None

def is_within_12_hours(text):
    t = text.lower()
    if "just now" in t or "minute" in t:
        return True
    m = re.search(r"(\d+)\s*(hour|day)", t)
    if m:
        v, u = int(m.group(1)), m.group(2)
        return (u == "hour" and v <= 12) or (u == "day" and v == 0)
    return False

def send_telegram_message(message: str):
    """Send a formatted message via Telegram Bot API."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        r = requests.post(url, data=payload, timeout=5)
        if r.status_code != 200:
            print(f"❌ Telegram error: {r.text}")
    except Exception as e:
        print(f"❌ Telegram exception: {e}")

# --------------------------
# ✅ Job Fetching Logic
# --------------------------

def fetch_jobs(company_name, company_id, seen_jobs):
    headers = {"User-Agent": "Mozilla/5.0"}
    matches, rejects = [], []
    stop = False

    for start in range(0, 250, 25):
        if stop:
            break
        url = (
            f"https://www.linkedin.com/jobs/search/"
            f"?f_C={company_id}&f_TPR=r10800&location=India&start={start}"
        )
        print(f"\n🔍 Scanning: {url}")
        try:
            res = requests.get(url, headers=headers, timeout=10)
            soup = BeautifulSoup(res.text, "html.parser")
            cards = soup.select(".base-card")
            if not cards:
                break

            for card in cards:
                title = card.select_one(".base-search-card__title").get_text(strip=True)
                comp  = card.select_one(".base-search-card__subtitle").get_text(strip=True)
                link  = card.select_one("a")["href"].split("?")[0]
                posted = card.select_one("time").get_text(strip=True)

                if not is_within_12_hours(posted):
                    rejects.append((title, comp, link, "⏱ Not within 12h"))
                    stop = True
                    break

                if link in seen_jobs.get(company_name, {}):
                    continue

                job_page = requests.get(link, headers=headers, timeout=10)
                job_soup = BeautifulSoup(job_page.text, "html.parser")
                desc_div = job_soup.find("div", class_=re.compile("description__text"))
                description = desc_div.get_text("\n").lower() if desc_div else ""

                lis = job_soup.select("li")
                qual_text = "\n".join(
                    li.get_text() for li in lis if "year" in li.get_text().lower()
                ).lower()

                if not any(kw in title.lower() for kw in title_keywords) or not description:
                    rejects.append((title, comp, link, "🚫 Filter fail"))
                    continue

                nums = []
                for m in EXPERIENCE_RE.finditer(qual_text):
                    lo = float(m.group("min"))
                    hi = float(m.group("max")) if m.group("max") else lo
                    nums += [lo, hi]

                ok_exp  = any(2 <= n < 4 for n in nums) and all(n < 4 for n in nums)
                ok_tech = any(tk in description for tk in tech_keywords)

                if nums and ok_exp and ok_tech:
                    seen_jobs.setdefault(company_name, {})[link] = datetime.now().isoformat()
                    msg = (
                        f"🧑‍💻 *{title}* at *{comp}*\n"
                        f"📅 *Posted:* {posted}\n"
                        f"🔗 [View Job]({link})"
                    )
                    send_telegram_message(msg)
                    matches.append({
                        "title": title,
                        "company": comp,
                        "posted": posted,
                        "link": link
                    })
                else:
                    rejects.append((title, comp, link, "❌ Missing skills/exp"))

                time.sleep(1)

        except Exception as e:
            print(f"❌ Error scanning {url}: {e}")
            continue

    return matches, rejects, seen_jobs

# --------------------------
# ✅ Main
# --------------------------

def main():
    # ensure JSON cache files exist
    ensure_file(FC_CACHE_FILE)
    ensure_file(SEEN_JOBS_FILE)

    fc_cache  = load_json(FC_CACHE_FILE)
    seen_jobs = load_json(SEEN_JOBS_FILE)

    for company in company_names:
        cid = get_company_id_from_page(company, fc_cache)
        if not cid:
            print(f"❌ Skipping {company}: no FC")
            continue

        jobs, rejects, seen_jobs = fetch_jobs(company, cid, seen_jobs)

        print(f"\n✅ Matches for {company} ({len(jobs)}):")
        for j in jobs:
            print(f"🧑‍💻 {j['title']} ({j['posted']}) {j['link']}")

        print(f"\n❌ Rejects for {company} ({len(rejects)}):")
        for t, c, l, r in rejects:
            print(f"{r}: {t} at {c} — {l}")

    save_json(FC_CACHE_FILE, fc_cache)
    save_json(SEEN_JOBS_FILE, seen_jobs)

if __name__ == "__main__":
    main()
