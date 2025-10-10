#!/usr/bin/env python3
"""
AGGRESSIVE QUARTERLY REPORT DOWNLOADER (v3 - no explicit years)
================================================================
•⁠  ⁠Headless Selenium + Requests
•⁠  ⁠IR-only scope (same registrable domain + trusted IR CDNs)
•⁠  ⁠Robust cookie consent handling (OneTrust/TrustArc/Quantcast/Generic)
•⁠  ⁠Scrolls to load lazy content
•⁠  ⁠*No hardcoded years* — always tries to fetch the latest by:
    * Page order (top-most first)
    * Strong "quarterly" cues (Q1/Q2/Q3/Q4, "quarter", "earnings", "10‑Q")
    * Artifact type preference: 10-Q > press release > presentation > supplement
•⁠  ⁠Follows "Learn more / Read more" detail pages (e.g., Caterpillar, 3M)
•⁠  ⁠SEC Filings route when needed (grabs the most recent 10‑Q row)
•⁠  ⁠Saves only PDF / PPT / PPTX (no HTML)

Inputs
------
•⁠  ⁠dow30_with_ir_pages.csv (ticker, company, ir_page[, found])

Outputs
-------
quarterly_reports/TICKER/*.pdf|.pptx|.ppt
quarterly_reports/_logs/debug.jsonl
"""

import os, re, time, json, warnings
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
import pandas as pd
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

warnings.filterwarnings("ignore")

ALLOWED_IR_ASSET_DOMAINS = (
    "q4cdn.com","gcs-web.com","kscope.io","corporate-ir.net","equisolve.net",
    "investis.com","tools.investis.com","otp.tools.investis.com",
    "d18rn0p25nwr6d.cloudfront.net"
)
DOC_EXTS = (".pdf", ".ppt", ".pptx")

QUARTER_CUES = [
    "q1","q2","q3","q4",
    "first quarter","second quarter","third quarter","fourth quarter",
    "quarterly","quarter","quarter ended",
    "earnings","financial results","results"
]
SEC_10Q_TERMS = ["10-q","form 10-q","10q"]
EARNINGS_RELEASE_TERMS = ["press release","news release","earnings release","release"]
PRESENTATION_TERMS = ["presentation","slides","deck","earnings call presentation","investor presentation","earnings presentation"]
SUPPLEMENT_TERMS = ["supplement","supplemental","tables","financial supplement","statistical supplement"]

PREF_KIND_SCORE = {"10q": 1000, "release": 800, "presentation": 700, "supplement": 600, "other": 100}

def safe_dir(p): os.makedirs(p, exist_ok=True); return p

def base_domain(h):
    p = (h or "").lower().split(".")
    return ".".join(p[-2:]) if len(p) >= 2 else (h or "").lower()

def is_allowed_ir_link(page_url, target_url):
    a = base_domain(urlparse(page_url).hostname)
    b = base_domain(urlparse(target_url).hostname)
    if a == b: return True
    host = (urlparse(target_url).hostname or "").lower()
    return any(host.endswith(d) for d in ALLOWED_IR_ASSET_DOMAINS)

def is_pdfish_url(href: str) -> bool:
    h = (href or "").lower()
    return (h.endswith(DOC_EXTS) or "/static-files/" in h or "/-/media/" in h or "document" in h or "download" in h)

def classify_kind(text_href: str) -> str:
    t = (text_href or "").lower()
    if any(k in t for k in SEC_10Q_TERMS): return "10q"
    if any(k in t for k in EARNINGS_RELEASE_TERMS): return "release"
    if any(k in t for k in PRESENTATION_TERMS): return "presentation"
    if any(k in t for k in SUPPLEMENT_TERMS): return "supplement"
    return "other"

def text_is_quarterly(s: str) -> bool:
    t = (s or "").lower()
    return any(k in t for k in QUARTER_CUES) or any(k in t for k in SEC_10Q_TERMS)

class AggressiveQuarterlyDownloaderV3:
    def __init__(self, outdir="data/raw", per_company=2):
        self.outdir = safe_dir(outdir)
        self.logdir = safe_dir(os.path.join(self.outdir, "_logs"))
        self.per_company = per_company
        # Create unique session identifier to avoid conflicts
        import uuid
        self.session_id = str(uuid.uuid4())[:8]
        self.session = requests.Session()
        self.session.headers.update({"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        self.driver = self._setup_selenium()
        self.stats = {"processed":0,"successful":0,"failed":[], "total_files":0, "session_id":self.session_id}

        # Minimal IR overrides (expand if your CSV lacks IR URLs)
        self.ir_overrides = {
            "PG":"https://www.pginvestor.com/",
            "DIS":"https://thewaltdisneycompany.com/investor-relations/",
            "JPM":"https://www.jpmorganchase.com/ir",
        }

        # Per-company nudges only for navigation shape (no year assumptions)
        self.special = {"AMGN":self._strategy_amgn, "GS":self._strategy_gs, "MMM":self._strategy_mmm,
                        "MSFT":self._strategy_msft, "V":self._strategy_visa}

    def _log(self, obj):
        with open(os.path.join(self.logdir, "debug.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, default=str) + "\n")

    def _setup_selenium(self):
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

        # Create unique user data directory for this session to avoid conflicts
        import tempfile
        user_data_dir = tempfile.mkdtemp(prefix=f"chrome_session_{self.session_id}_")
        options.add_argument(f"--user-data-dir={user_data_dir}")

        # Additional isolation arguments
        options.add_argument("--disable-web-security")
        options.add_argument("--disable-features=VizDisplayCompositor")
        options.add_argument(f"--remote-debugging-port=0")  # Let Chrome pick a random port

        prefs={"profile.default_content_setting_values.images":2,"download.prompt_for_download":False}
        options.add_experimental_option("prefs", prefs)

        # Retry logic for driver creation
        max_retries = 3
        for attempt in range(max_retries):
            try:
                driver = webdriver.Chrome(options=options)
                driver.set_page_load_timeout(40)
                print(f"Chrome driver initialized successfully for session {self.session_id} (attempt {attempt + 1})")
                return driver
            except Exception as e:
                print(f"Failed to initialize Chrome driver (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt == max_retries - 1:
                    raise
                time.sleep(2)  # Wait before retry

    def _accept_cookies(self):
        XPATHS = [
            "//button[contains(@id,'onetrust-accept-btn')]",
            "//button[contains(@class,'onetrust-accept-btn-handler')]",
            "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'accept all')]",
            "//button[contains(@class,'css') and contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'accept')]",
            "//a[contains(@class,'trustarc') and contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'accept')]",
            "//button[contains(@class,'truste-button') or contains(@class,'trustarc')]",
            "//button[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'accept')]",
            "//button[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'agree')]",
            "//button[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'allow')]",
            "//a[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'accept')]",
            "//a[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'agree')]"
        ]
        for xp in XPATHS:
            try:
                elems = self.driver.find_elements(By.XPATH, xp)
                for el in elems:
                    if el.is_displayed():
                        self.driver.execute_script("arguments[0].click();", el)
                        time.sleep(0.7)
                        return True
            except Exception:
                continue
        return False

    def _scroll(self, cycles=6, pause=0.8):
        last = 0
        for _ in range(cycles):
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(pause)
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
            time.sleep(0.4)
            cur = self.driver.execute_script("return document.body.scrollHeight")
            if cur == last: break
            last = cur
        self.driver.execute_script("window.scrollTo(0,0);")
        time.sleep(0.5)

    def _download_if_doc(self, ticker, url, title):
        try:
            r = self.session.get(url, timeout=30, allow_redirects=True, verify=False)
            if r.status_code != 200: return False
            ct = r.headers.get("content-type","").lower()
            data = r.content or b""
            ok_ct = any(x in ct for x in ["pdf","presentation","powerpoint","ms-powerpoint","officedocument.presentationml.presentation"])
            ok_ext = any(url.lower().endswith(ext) for ext in DOC_EXTS)
            ok_sniff = data.startswith(b"%PDF") or data[:4]==b"PK\x03\x04"
            if not (ok_ct or ok_ext or ok_sniff): return False
            base = safe_dir(os.path.join(self.outdir, ticker))
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_title = re.sub(r"[^A-Za-z0-9]+","", (title or "Quarterly_Report")).strip("")[:60]
            ext = ".pdf" if data.startswith(b"%PDF") or url.lower().endswith(".pdf") else (".pptx" if "presentationml" in ct or url.lower().endswith(".pptx") else ".ppt")
            path = os.path.join(base, f"{ticker}{ts}{safe_title}{ext}")
            with open(path,"wb") as f: f.write(data)
            print(f"      ✅ Saved: {os.path.basename(path)}")
            self.stats["total_files"] += 1
            return True
        except Exception as e:
            self._log({"stage":"download","ticker":ticker,"url":url,"err":str(e)})
            return False

    def _collect_ranked_candidates_from_soup(self, soup):
        """
        Collect anchors that look quarterly or point to detail pages;
        rank by kind preference and position (earlier is better).
        """
        candidates = []
        idx = 0
        for a in soup.find_all("a", href=True):
            idx += 1
            href = a["href"]
            text = a.get_text(" ", strip=True) or ""
            full = urljoin(self.driver.current_url, href)
            if not is_allowed_ir_link(self.driver.current_url, full): 
                continue
            low = (text + " " + href).lower()
            # direct artifact
            if is_pdfish_url(href) and text_is_quarterly(low):
                kind = classify_kind(low)
                score = PREF_KIND_SCORE.get(kind, 0) - idx*0.01
                candidates.append(("direct", full, text, kind, score))
                continue
            # detail page to follow
            if (("learn more" in low) or ("read more" in low) or text_is_quarterly(low)) and not is_pdfish_url(href):
                kind = classify_kind(low)
                score = PREF_KIND_SCORE.get(kind, 0) - idx*0.01 - 5  # slight penalty for follow
                candidates.append(("follow", full, text, kind, score))
        # sort by score desc
        candidates.sort(key=lambda x: x[4], reverse=True)
        return candidates

    def _harvest_from_detail_page(self, ticker, limit):
        got = 0
        inner = BeautifulSoup(self.driver.page_source, "html.parser")
        for b in inner.find_all("a", href=True):
            if got >= limit: break
            href2 = b["href"]; full2 = urljoin(self.driver.current_url, href2)
            if not is_allowed_ir_link(self.driver.current_url, full2): 
                continue
            low = (b.get_text(" ", strip=True) + " " + href2).lower()
            if is_pdfish_url(href2) and text_is_quarterly(low):
                t2 = b.get_text(" ", strip=True) or "Quarterly Artifact"
                if self._download_if_doc(ticker, full2, t2): got += 1
        # If still nothing, allow any doc-ish artifact on detail page
        if got == 0:
            for b in inner.find_all("a", href=True):
                if got >= limit: break
                href2 = b["href"]; full2 = urljoin(self.driver.current_url, href2)
                if not is_allowed_ir_link(self.driver.current_url, full2): 
                    continue
                if is_pdfish_url(href2):
                    t2 = b.get_text(" ", strip=True) or "Report"
                    if self._download_if_doc(ticker, full2, t2): got += 1
        return got

    def _sec_filings_route(self, ticker, limit=2):
        """Navigate to SEC filings page (if present) and download top-most 10‑Q artifacts."""
        got = 0
        soup = BeautifulSoup(self.driver.page_source, "html.parser")
        sec_link = None
        for a in soup.find_all("a", href=True):
            t = a.get_text(" ", strip=True).lower()
            if "sec filing" in t or "sec filings" in t or t == "sec":
                sec_link = urljoin(self.driver.current_url, a["href"]); break
        if not sec_link: 
            return 0
        try:
            self.driver.get(sec_link); time.sleep(2); self._accept_cookies(); self._scroll(2)
            inner = BeautifulSoup(self.driver.page_source, "html.parser")
            # assume newest rows are first; take first rows that mention 10-Q
            for row in inner.find_all(["tr","article","li","div"]):
                if got >= limit: break
                tx = row.get_text(" ", strip=True).lower()
                if any(t in tx for t in SEC_10Q_TERMS):
                    for a in row.find_all("a", href=True):
                        if got >= limit: break
                        href = a["href"]; full = urljoin(self.driver.current_url, href)
                        if is_allowed_ir_link(self.driver.current_url, full) and is_pdfish_url(href):
                            if self._download_if_doc(ticker, full, a.get_text(" ", strip=True)): got += 1
            self.driver.back(); time.sleep(1.2)
        except Exception as e:
            self._log({"stage":"sec_filings","ticker":ticker,"err":str(e)})
        return got

    # --------- Per-company nudges (no year logic) ----------
    def _strategy_amgn(self, ticker):
        # Try "EARNINGS"/Presentations sections
        soup = BeautifulSoup(self.driver.page_source, "html.parser")
        for a in soup.find_all("a", href=True):
            t = a.get_text(" ", strip=True).lower()
            if any(k in t for k in ["earnings","presentations","events"]):
                url = urljoin(self.driver.current_url, a["href"])
                try:
                    self.driver.get(url); time.sleep(2); self._accept_cookies(); self._scroll(3)
                    got = self._generic_grab(ticker, limit=self.per_company)
                    if got >= 1: return got
                    self.driver.back(); time.sleep(1)
                except Exception: continue
        return 0

    def _strategy_gs(self, ticker):
        # Home often exposes "Form 10‑Q" tile; click then pick doc
        soup = BeautifulSoup(self.driver.page_source, "html.parser")
        for a in soup.find_all("a", href=True):
            txt = a.get_text(" ", strip=True).lower()
            if ("form 10-q" in txt or "10-q" in txt):
                url = urljoin(self.driver.current_url, a["href"])
                try:
                    self.driver.get(url); time.sleep(2); self._accept_cookies(); self._scroll(2)
                    got = self._harvest_from_detail_page(ticker, limit=self.per_company)
                    if got >= 1: return got
                    self.driver.back(); time.sleep(1)
                except Exception: continue
        return self._generic_grab(ticker, limit=self.per_company)

    def _strategy_mmm(self, ticker):
        return self._generic_grab(ticker, limit=self.per_company)

    def _strategy_msft(self, ticker):
        got = self._sec_filings_route(ticker, limit=self.per_company)
        if got >= 1: return got
        return self._generic_grab(ticker, limit=self.per_company)

    def _strategy_visa(self, ticker):
        self._accept_cookies()
        return self._generic_grab(ticker, limit=self.per_company)

    # ----------------------- Generic page harvesting ---------------------
    def _generic_grab(self, ticker, limit=2):
        got = 0
        soup = BeautifulSoup(self.driver.page_source, "html.parser")
        candidates = self._collect_ranked_candidates_from_soup(soup)
        # Iterate candidates by score/position; prioritize direct artifacts first
        for kind, url, text, _k, _score in candidates:
            if got >= limit: break
            if kind == "direct":
                if self._download_if_doc(ticker, url, text): got += 1
        # Then follow best "follow" candidates
        if got < limit:
            for kind, url, text, _k, _score in candidates:
                if got >= limit: break
                if kind != "follow": continue
                try:
                    self.driver.get(url); time.sleep(2); self._accept_cookies(); self._scroll(2)
                    got += self._harvest_from_detail_page(ticker, limit=limit-got)
                    self.driver.back(); time.sleep(1.2)
                except Exception as e:
                    self._log({"stage":"follow_click","ticker":ticker,"url":url,"err":str(e)})
        # As last resort, SEC filings
        if got < limit:
            got += self._sec_filings_route(ticker, limit=limit-got)
        return got

    # ------------------------------ Main flow ---------------------------
    def process_company(self, ticker, company, ir_url):
        print("\n" + "="*80)
        print(f"{ticker} - {company}")
        print("="*80)
        self.stats["processed"] += 1

        if ticker in self.ir_overrides and (not ir_url or "pg" in ticker.lower() or base_domain(urlparse(ir_url).hostname) != base_domain(urlparse(self.ir_overrides[ticker]).hostname)):
            ir_url = self.ir_overrides[ticker]
            print(f"  🔧 Using IR override: {ir_url}")

        try:
            self.driver.get(ir_url); time.sleep(2.5)
        except Exception as e:
            print("  ❌ Load error")
            self.stats["failed"].append(ticker)
            self._log({"stage":"load","ticker":ticker,"err":str(e)})
            return 0

        self._accept_cookies()
        self._scroll(6)

        got = 0
        if ticker in self.special:
            try:
                got = self.special[ticker](ticker)
            except Exception as e:
                self._log({"stage":"special","ticker":ticker,"err":str(e)})
        if got < self.per_company:
            got += self._generic_grab(ticker, limit=self.per_company-got)

        if got >= 1:
            self.stats["successful"] += 1
        else:
            self.stats["failed"].append(ticker)
        return got

    def close(self):
        try:
            if self.driver:
                self.driver.quit()
                print(f"Chrome driver closed for session {self.session_id}")
        except Exception as e:
            print(f"Error closing driver for session {self.session_id}: {e}")

        # Clean up temporary Chrome user data directory
        try:
            import shutil
            import glob
            temp_dirs = glob.glob(f"/tmp/chrome_session_{self.session_id}_*")
            for temp_dir in temp_dirs:
                shutil.rmtree(temp_dir, ignore_errors=True)
                print(f"Cleaned up temp directory: {temp_dir}")
        except Exception as e:
            print(f"Error cleaning up temp directories for session {self.session_id}: {e}")

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Download quarterly reports from IR pages")
    parser.add_argument("--json", default="output/ir_finder_results.json",
                       help="Path to JSON file with IR page data")
    parser.add_argument("--output", default="data/raw",
                       help="Output directory for downloads")
    parser.add_argument("--companies", nargs="+",
                       help="Specific company tickers to process (default: all)")
    parser.add_argument("--per-company", type=int, default=2,
                       help="Max files to download per company")

    args = parser.parse_args()

    print("="*80)
    print("AGGRESSIVE QUARTERLY REPORT DOWNLOADER (v3 - JSON input)")
    print("="*80)

    # Load JSON data
    if not os.path.exists(args.json):
        print(f"❌ JSON file not found: {args.json}")
        return

    with open(args.json, 'r') as f:
        data = json.load(f)

    companies = data.get("companies_processed", [])
    if args.companies:
        # Filter to specific companies
        companies = [c for c in companies if c["ticker"] in args.companies]

    if not companies:
        print("❌ No companies found to process")
        return

    print(f"🚀 Processing {len(companies)} companies...")

    dl = AggressiveQuarterlyDownloaderV3(outdir=args.output, per_company=args.per_company)
    start = time.time()

    try:
        for company in companies:
            ticker = company.get("ticker", "").strip()
            company_name = company.get("company_name", "").strip() or ticker
            ir_url = company.get("ir_url", "").strip() if company.get("ir_page_found") else None

            # Apply IR overrides if needed
            if not ir_url and ticker in dl.ir_overrides:
                ir_url = dl.ir_overrides[ticker]

            if not ir_url:
                print(f"\n{ticker} - {company_name}\n  ⚠️ Missing IR URL; skipping")
                dl.stats["failed"].append(ticker)
                continue

            got = dl.process_company(ticker, company_name, ir_url)
            print(f"  → Downloaded {got} files")
            time.sleep(1.0)

        elapsed = time.time() - start
        print("\n" + "="*80)
        print("FINAL SUMMARY")
        print("="*80)
        print(f"⏱️  Total time: {elapsed/60:.1f} minutes")
        print(f"✅ Successful: {dl.stats['successful']}/{dl.stats['processed']}")
        print(f"📊 Total files: {dl.stats['total_files']}")
        if dl.stats["failed"]:
            print("\nFAILED Tickers: " + ", ".join([t for t in dl.stats["failed"] if t]))
        print(f"\nArtifacts saved under: {dl.outdir}/TICKER/*.pdf|.pptx|.ppt")
        print(f"Debug log: {dl.logdir}/debug.jsonl")

        # Save summary to JSON
        summary = {
            "timestamp": datetime.now().isoformat(),
            "total_companies": len(companies),
            "successful_downloads": dl.stats['successful'],
            "total_files": dl.stats['total_files'],
            "failed_tickers": dl.stats['failed'],
            "processing_time_minutes": round(elapsed/60, 2)
        }

        summary_file = os.path.join(args.output, "download_summary.json")
        with open(summary_file, 'w') as f:
            json.dump(summary, f, indent=2)
        print(f"📊 Summary saved to {summary_file}")

    finally:
        dl.close()

if __name__ == "__main__":
    main()