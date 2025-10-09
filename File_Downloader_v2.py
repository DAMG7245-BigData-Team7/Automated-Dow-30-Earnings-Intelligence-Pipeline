#!/usr/bin/env python3
"""
Enhanced Parallel Document Downloader - 2025 Documents Only
Downloads only 2025 earnings documents from Dow 30 companies
Version 2.0 - With browser-based download fallback and improved detection
"""

import json
import time
import logging
import requests
import re
import os
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple
import threading
from urllib.parse import urlparse, urljoin
import urllib3
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.common.keys import Keys

# Suppress SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(threadName)-10s] - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class Enhanced2025Downloader:
    """Enhanced parallel downloader for 2025 documents only with browser fallback"""
    
    def __init__(self, 
                 input_file: str = "output/ir_finder_results.json",
                 output_dir: str = "downloads_2025",
                 max_workers: int = 3,
                 headless: bool = True,
                 debug: bool = False):
        
        self.input_file = Path(input_file)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.max_workers = max_workers
        self.headless = headless
        self.debug = debug
        
        if debug:
            logger.setLevel(logging.DEBUG)
        
        # Thread-safe tracking
        self.lock = threading.Lock()
        self.stats = {
            'companies_processed': 0,
            'total_documents': 0,
            'failed_companies': [],
            'browser_downloads': 0,
            'direct_downloads': 0,
            'start_time': datetime.now()
        }
        
        # Results storage
        self.results = []
        
        # Document patterns
        self.DOC_PATTERNS = {
            '10-K': [r'10-?k', r'form\s*10-?k', r'annual\s*report\s*on\s*form'],
            '10-Q': [r'10-?q', r'form\s*10-?q', r'quarterly\s*report'],
            '8-K': [r'8-?k', r'form\s*8-?k', r'current\s*report'],
            'Proxy': [r'proxy', r'def\s*14a', r'definitive\s*proxy'],
            'Earnings': [r'earnings', r'earnings\s*release', r'financial\s*results', r'quarterly\s*results'],
            'Presentation': [r'presentation', r'investor\s*presentation', r'slides', r'webcast'],
            'Annual Report': [r'annual\s*report', r'ar\s*2025'],
            'Quarterly': [r'q[1-4]\s*2025', r'quarterly', r'quarter', r'three\s*months']
        }
        
        # Comprehensive 2025 patterns
        self.YEAR_PATTERNS = [
            # Standard formats
            r'2025',
            r'fy\s*2025', r'fy25', r'fy\s*25', r'fiscal\s*2025', r'fiscal\s*year\s*2025',
            r'fiscal\s*25', r'fy-25', r'fy-2025',
            
            # Quarter formats
            r'q[1-4]\s*2025', r'q[1-4]\s*25', r'q[1-4]/25', r'q[1-4]-25',
            r'1q\s*25', r'2q\s*25', r'3q\s*25', r'4q\s*25',
            r'1q25', r'2q25', r'3q25', r'4q25',
            r'first\s*quarter.*2025', r'second\s*quarter.*2025', 
            r'third\s*quarter.*2025', r'fourth\s*quarter.*2025',
            
            # Month formats
            r'january\s*2025', r'february\s*2025', r'march\s*2025',
            r'april\s*2025', r'may\s*2025', r'june\s*2025',
            r'july\s*2025', r'august\s*2025', r'september\s*2025',
            r'october\s*2025', r'november\s*2025', r'december\s*2025',
            r'jan\s*2025', r'feb\s*2025', r'mar\s*2025',
            r'apr\s*2025', r'may\s*2025', r'jun\s*2025',
            r'jul\s*2025', r'aug\s*2025', r'sep\s*2025',
            r'oct\s*2025', r'nov\s*2025', r'dec\s*2025',
            
            # Date formats
            r'10/\d{1,2}/2025', r'10/\d{1,2}/25',  # MM/DD/YYYY
            r'\d{1,2}/\d{1,2}/25', r'\d{1,2}/\d{1,2}/2025',
            r'2025-\d{2}-\d{2}', r'2025/\d{2}/\d{2}',  # YYYY-MM-DD
            
            # Period ended formats
            r'ended.*2025', r'ending.*2025',
            r'as\s*of.*2025', r'through.*2025',
            r'period.*2025', r'year.*2025'
        ]
        
        # Valid file extensions
        self.VALID_EXTENSIONS = ['.pdf', '.xlsx', '.xls', '.doc', '.docx', '.zip', '.pptx', '.html']
        
        # Navigation patterns
        self.SEC_NAV_PATTERNS = [
            "SEC Filings", "SEC Filing", "Filings & Reports", "Edgar",
            "Financial Filings", "Regulatory Filings", "SEC Documents",
            "10-K", "10-Q", "8-K", "Proxy", "All Filings", "Filings"
        ]
        
        self.FIN_NAV_PATTERNS = [
            "Financial Information", "Financials", "Financial Reports",
            "Quarterly Results", "Annual Reports", "Earnings",
            "Financial Statements", "Results", "Reports & Presentations",
            "Quarterly Earnings", "Financial Results", "Financial Releases"
        ]
    
    def create_driver(self) -> webdriver.Chrome:
        """Create Chrome driver with download capabilities"""
        chrome_options = Options()
        
        if self.headless:
            chrome_options.add_argument("--headless=new")
        
        # Set download directory
        prefs = {
            "download.default_directory": str(self.output_dir.absolute()),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
            "plugins.always_open_pdf_externally": True
        }
        chrome_options.add_experimental_option("prefs", prefs)
        
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.page_load_strategy = 'normal'
        chrome_options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(30)
        driver.implicitly_wait(5)
        
        return driver
    
    def _wait_for_content(self, driver, timeout=10):
        """Wait for dynamic content to load"""
        try:
            # Wait for common loading indicators to disappear
            WebDriverWait(driver, timeout).until_not(
                EC.presence_of_element_located((By.CLASS_NAME, "loading"))
            )
        except:
            pass
        
        try:
            # Execute JavaScript to scroll and trigger lazy loading
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
            time.sleep(1)
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)
            driver.execute_script("window.scrollTo(0, 0);")
        except:
            pass
    
    def _handle_microsoft(self, driver, company_dir: Path, ticker: str) -> Dict:
        """Special handler for Microsoft's investor site"""
        documents_found = []
        
        try:
            # Navigate to earnings page
            earnings_url = "https://www.microsoft.com/en-us/Investor/earnings/earnings-quarterly-results"
            driver.get(earnings_url)
            time.sleep(3)
            self._wait_for_content(driver)
            
            # Look for FY2025 earnings links
            fy25_links = driver.find_elements(By.XPATH, 
                "//a[contains(text(), 'FY25') or contains(text(), 'FY 2025') or contains(text(), 'Q1 2025') or contains(text(), 'Q2 2025') or contains(text(), 'Q3 2025')]")
            
            for link in fy25_links[:10]:
                try:
                    href = link.get_attribute('href')
                    if href:
                        # Navigate to the specific quarter page
                        driver.get(href)
                        time.sleep(2)
                        
                        # Look for download links on the quarter page
                        download_links = driver.find_elements(By.XPATH, 
                            "//a[contains(text(), 'Download') or contains(text(), 'Excel') or contains(@href, '.xlsx') or contains(@href, '.pdf') or contains(@href, '.docx')]")
                        
                        for dl_link in download_links:
                            dl_href = dl_link.get_attribute('href')
                            if dl_href and ('.xlsx' in dl_href or '.pdf' in dl_href or '.docx' in dl_href):
                                documents_found.append({
                                    'url': dl_href,
                                    'text': dl_link.text or 'MSFT Earnings Document',
                                    'type': 'Earnings',
                                    'is_pdf': '.pdf' in dl_href
                                })
                except:
                    continue
                    
        except Exception as e:
            logger.debug(f"Microsoft handler error: {e}")
        
        return documents_found
    
    def _handle_jpmorgan(self, driver, company_dir: Path, ticker: str) -> Dict:
        """Special handler for JPMorgan Chase"""
        documents_found = []
        
        try:
            # Navigate to IR page
            ir_url = "https://www.jpmorganchase.com/ir/quarterly-earnings"
            driver.get(ir_url)
            time.sleep(3)
            self._wait_for_content(driver)
            
            # Look for 2025 earnings
            links_2025 = driver.find_elements(By.XPATH, 
                "//a[contains(text(), '2025') or contains(@href, '2025')]")
            
            for link in links_2025:
                try:
                    href = link.get_attribute('href')
                    text = link.text
                    
                    # Navigate to earnings page and look for PDFs
                    if href and 'earnings' in href.lower():
                        driver.get(href)
                        time.sleep(2)
                        
                        # Look for PDF and presentation links
                        doc_links = driver.find_elements(By.XPATH, 
                            "//a[contains(@href, '.pdf') or contains(text(), 'Presentation') or contains(text(), 'Supplement') or contains(text(), 'Release')]")
                        
                        for doc_link in doc_links[:10]:
                            doc_href = doc_link.get_attribute('href')
                            if doc_href and ('.pdf' in doc_href or '.pptx' in doc_href):
                                documents_found.append({
                                    'url': doc_href,
                                    'text': doc_link.text or 'JPM Document',
                                    'type': 'Earnings',
                                    'is_pdf': '.pdf' in doc_href
                                })
                except:
                    continue
                    
        except Exception as e:
            logger.debug(f"JPMorgan handler error: {e}")
        
        return documents_found
    
    def _handle_boeing(self, driver, company_dir: Path, ticker: str) -> Dict:
        """Special handler for Boeing"""
        documents_found = []
        
        try:
            # Boeing specific URL
            ir_url = "https://investors.boeing.com/investors/quarterly-earnings"
            driver.get(ir_url)
            time.sleep(3)
            self._wait_for_content(driver)
            
            # Look for 2025 documents
            quarters = driver.find_elements(By.XPATH, 
                "//div[contains(@class, 'quarter') or contains(@class, 'earning')]//a[contains(text(), '2025')]")
            
            for link in quarters:
                href = link.get_attribute('href')
                if href:
                    driver.get(href)
                    time.sleep(2)
                    
                    # Find PDFs on the page
                    pdf_links = driver.find_elements(By.XPATH, "//a[contains(@href, '.pdf')]")
                    for pdf_link in pdf_links:
                        pdf_href = pdf_link.get_attribute('href')
                        if pdf_href:
                            documents_found.append({
                                'url': pdf_href,
                                'text': pdf_link.text or 'Boeing Document',
                                'type': 'Earnings',
                                'is_pdf': True
                            })
        except Exception as e:
            logger.debug(f"Boeing handler error: {e}")
        
        return documents_found
    
    def _handle_intel(self, driver, company_dir: Path, ticker: str) -> Dict:
        """Special handler for Intel"""
        documents_found = []
        
        try:
            # Intel earnings page
            ir_url = "https://www.intc.com/quarterly-earnings"
            driver.get(ir_url)
            time.sleep(3)
            self._wait_for_content(driver)
            
            # Look for 2025 quarters
            quarter_links = driver.find_elements(By.XPATH, 
                "//a[contains(text(), 'Q1 2025') or contains(text(), 'Q2 2025') or contains(text(), 'Q3 2025') or contains(text(), 'Q4 2025')]")
            
            for link in quarter_links:
                href = link.get_attribute('href')
                if href:
                    # Get all PDFs and Excel files from the quarter
                    documents_found.append({
                        'url': href,
                        'text': link.text,
                        'type': 'Earnings',
                        'is_pdf': '.pdf' in href
                    })
                    
        except Exception as e:
            logger.debug(f"Intel handler error: {e}")
        
        return documents_found
    
    def _handle_walmart(self, driver, company_dir: Path, ticker: str) -> Dict:
        """Special handler for Walmart"""
        documents_found = []
        
        try:
            # Walmart financial releases
            ir_url = "https://stock.walmart.com/financials/quarterly-results/default.aspx"
            driver.get(ir_url)
            time.sleep(3)
            self._wait_for_content(driver)
            
            # Look for FY2025/FY2026 (Walmart's fiscal year)
            fiscal_links = driver.find_elements(By.XPATH, 
                "//a[contains(text(), 'FY2025') or contains(text(), 'FY2026') or contains(text(), 'Q1 2025') or contains(text(), 'Q2 2025')]")
            
            for link in fiscal_links:
                href = link.get_attribute('href')
                if href and ('.pdf' in href or '.xlsx' in href):
                    documents_found.append({
                        'url': href,
                        'text': link.text or 'Walmart Document',
                        'type': 'Earnings',
                        'is_pdf': '.pdf' in href
                    })
                    
        except Exception as e:
            logger.debug(f"Walmart handler error: {e}")
        
        return documents_found
    
    def _handle_generic_quarterly(self, driver, ticker: str) -> Dict:
        """Generic handler for companies with quarterly earnings pages"""
        documents_found = []
        
        try:
            # Common patterns for quarterly earnings
            quarterly_patterns = [
                "//a[contains(text(), 'Quarterly') and contains(text(), '2025')]",
                "//a[contains(text(), 'Q1 2025') or contains(text(), 'Q2 2025') or contains(text(), 'Q3 2025')]",
                "//a[contains(text(), 'First Quarter 2025') or contains(text(), 'Second Quarter 2025') or contains(text(), 'Third Quarter 2025')]",
                "//a[contains(@href, 'q1-2025') or contains(@href, 'q2-2025') or contains(@href, 'q3-2025')]"
            ]
            
            for pattern in quarterly_patterns:
                links = driver.find_elements(By.XPATH, pattern)
                for link in links[:5]:
                    href = link.get_attribute('href')
                    text = link.text
                    
                    if href:
                        # Check if it's a direct document
                        if '.pdf' in href or '.xlsx' in href:
                            documents_found.append({
                                'url': href,
                                'text': text or f'{ticker} Q 2025',
                                'type': 'Quarterly',
                                'is_pdf': '.pdf' in href
                            })
                        else:
                            # Navigate and look for documents
                            original_url = driver.current_url
                            driver.get(href)
                            time.sleep(2)
                            
                            # Find all document links
                            doc_links = driver.find_elements(By.XPATH, 
                                "//a[contains(@href, '.pdf') or contains(@href, '.xlsx')]")
                            
                            for doc_link in doc_links[:5]:
                                doc_href = doc_link.get_attribute('href')
                                if doc_href:
                                    documents_found.append({
                                        'url': doc_href,
                                        'text': doc_link.text or f'{ticker} Document',
                                        'type': 'Quarterly',
                                        'is_pdf': '.pdf' in doc_href
                                    })
                            
                            # Return to original page
                            driver.get(original_url)
                            
        except Exception as e:
            logger.debug(f"Generic quarterly handler error: {e}")
        
        return documents_found
    
    def process_company(self, company_data: Dict) -> Dict:
        """Process a single company for 2025 documents - prioritize PDFs"""
        ticker = company_data['ticker']
        ir_url = company_data['ir_url']
        
        result = {
            'ticker': ticker,
            'company_name': company_data['company_name'],
            'documents_found': 0,
            'documents_downloaded': 0,
            'pdf_downloads': 0,
            'html_downloads': 0,
            'browser_downloads': 0,
            'direct_downloads': 0,
            'status': 'pending',
            'strategies_used': [],
            'timestamp': datetime.now().isoformat()
        }
        
        driver = None
        
        try:
            # Create company directory
            company_dir = self.output_dir / ticker
            company_dir.mkdir(exist_ok=True)
            
            logger.info(f"Starting {ticker} - Looking for 2025 PDF documents")
            
            # Create driver for this company
            driver = self.create_driver()
            
            # Navigate to IR page
            driver.get(ir_url)
            time.sleep(3)
            self._wait_for_content(driver)
            
            all_documents = []
            
            # Log page title for debugging
            if self.debug:
                logger.debug(f"{ticker}: Page title: {driver.title}")
                logger.debug(f"{ticker}: Current URL: {driver.current_url}")
            
            # Use company-specific handlers for problematic sites
            company_handlers = {
                'MSFT': self._handle_microsoft,
                'JPM': self._handle_jpmorgan,
                'BA': self._handle_boeing,
                'INTC': self._handle_intel,
                'WMT': self._handle_walmart
            }
            
            # Companies that typically need special handling
            difficult_companies = ['IBM', 'GS', 'UNH', 'CVX', 'TRV', 'AXP']
            
            # Try company-specific handler if available
            if ticker in company_handlers:
                logger.info(f"{ticker}: Using {ticker}-specific handler")
                custom_docs = company_handlers[ticker](driver, company_dir, ticker)
                if custom_docs:
                    all_documents.extend(custom_docs)
                    result['strategies_used'].append('custom_handler')
                    logger.info(f"{ticker}: Found {len(custom_docs)} documents via custom handler")
            
            # For difficult companies without specific handlers, try generic quarterly
            elif ticker in difficult_companies:
                logger.info(f"{ticker}: Using generic quarterly handler")
                custom_docs = self._handle_generic_quarterly(driver, ticker)
                if custom_docs:
                    all_documents.extend(custom_docs)
                    result['strategies_used'].append('generic_quarterly')
                    logger.info(f"{ticker}: Found {len(custom_docs)} documents via generic handler")
            
            # Continue with general strategies for all companies
            # Strategy 1: Look for direct PDF links on main page
            direct_pdfs = self._find_direct_pdf_links(driver)
            if direct_pdfs:
                all_documents.extend(direct_pdfs)
                result['strategies_used'].append('direct_pdfs')
                logger.info(f"{ticker}: Found {len(direct_pdfs)} direct PDF links")
            
            # Strategy 2: Latest documents (most likely to have 2025)
            latest_docs = self._find_latest_documents(driver)
            if latest_docs:
                all_documents.extend(latest_docs)
                result['strategies_used'].append('latest')
                logger.info(f"{ticker}: Found {len(latest_docs)} latest 2025 documents")
            
            # Strategy 3: SEC Filings Section
            sec_docs = self._find_sec_filings(driver, ir_url)
            if sec_docs:
                all_documents.extend(sec_docs)
                result['strategies_used'].append('sec_filings')
                logger.info(f"{ticker}: Found {len(sec_docs)} in SEC filings (2025)")
            
            # Strategy 4: Financial Documents Section
            fin_docs = self._find_financial_documents(driver, ir_url)
            if fin_docs:
                all_documents.extend(fin_docs)
                result['strategies_used'].append('financial_section')
                logger.info(f"{ticker}: Found {len(fin_docs)} in financial section (2025)")
            
            # Strategy 5: Direct page scan (skip for companies with custom handlers if they found docs)
            if not (ticker in ['MSFT', 'JPM', 'BA'] and all_documents):
                page_docs = self._scan_current_page(driver)
                if page_docs:
                    all_documents.extend(page_docs)
                    result['strategies_used'].append('page_scan')
                    logger.info(f"{ticker}: Found {len(page_docs)} via page scan (2025)")
            
            # Strategy 6: Events/News section
            event_docs = self._find_events_section(driver, ir_url)
            if event_docs:
                all_documents.extend(event_docs)
                result['strategies_used'].append('events')
            
            # Deduplicate and prioritize PDFs
            unique_docs = self._deduplicate_documents(all_documents)
            result['documents_found'] = len(unique_docs)
            
            # Count PDFs vs HTML
            pdf_count = sum(1 for doc in unique_docs if doc.get('is_pdf', False))
            logger.info(f"{ticker}: Found {pdf_count} PDFs out of {len(unique_docs)} total documents")
            
            if self.debug and unique_docs:
                logger.debug(f"{ticker}: Sample URLs found:")
                for doc in unique_docs[:3]:
                    doc_type = "PDF" if doc.get('is_pdf') else "HTML"
                    logger.debug(f"  - {doc_type}: {doc['text'][:50]}: {doc['url'][:100]}")
            
            # Download all 2025 documents - PDFs first
            for doc in unique_docs:
                # Special handling for HTML pages that might contain PDFs
                if not doc.get('is_pdf', False) and 'earnings' in doc['url'].lower():
                    # This might be an earnings page with PDFs inside
                    logger.debug(f"{ticker}: Exploring earnings page for PDFs: {doc['url'][:100]}")
                    nested_pdfs = self._find_pdfs_on_page(driver, doc['url'])
                    if nested_pdfs:
                        logger.info(f"{ticker}: Found {len(nested_pdfs)} PDFs on earnings page")
                        # Add these PDFs to download queue
                        for pdf in nested_pdfs:
                            if self.download_document(pdf, company_dir, ticker):
                                result['documents_downloaded'] += 1
                                result['direct_downloads'] += 1
                                result['pdf_downloads'] += 1
                        continue  # Skip downloading the HTML page itself
                
                # Regular download logic
                if self.download_document(doc, company_dir, ticker):
                    result['documents_downloaded'] += 1
                    result['direct_downloads'] += 1
                    if doc.get('is_pdf', False):
                        result['pdf_downloads'] += 1
                    else:
                        result['html_downloads'] += 1
                # If failed and not PDF, try browser download
                elif not doc.get('is_pdf', False) and self.download_with_browser(doc, company_dir, ticker, driver):
                    result['documents_downloaded'] += 1
                    result['browser_downloads'] += 1
                    result['html_downloads'] += 1
                else:
                    logger.debug(f"{ticker}: Failed to download {doc['url'][:100]}")
            
            result['status'] = 'success'
            
            # Update stats
            with self.lock:
                self.stats['companies_processed'] += 1
                self.stats['total_documents'] += result['documents_downloaded']
                self.stats['browser_downloads'] += result['browser_downloads']
                self.stats['direct_downloads'] += result['direct_downloads']
                self.results.append(result)
            
            logger.info(f"✓ {ticker}: Downloaded {result['documents_downloaded']}/{result['documents_found']} "
                       f"(PDFs: {result['pdf_downloads']}, HTML: {result['html_downloads']})")
            
        except Exception as e:
            logger.error(f"✗ {ticker}: {str(e)[:100]}")
            result['status'] = 'failed'
            result['error'] = str(e)[:200]
            
            with self.lock:
                self.stats['failed_companies'].append(ticker)
                self.results.append(result)
        
        finally:
            if driver:
                try:
                    driver.quit()
                except:
                    pass
        
        return result
    
    def _find_direct_pdf_links(self, driver) -> List[Dict]:
        """Find direct PDF links on current page"""
        pdfs = []
        
        try:
            # Look specifically for PDF links
            pdf_selectors = [
                "//a[contains(@href, '.pdf')]",
                "//a[contains(@href, '.PDF')]", 
                "//a[contains(text(), 'PDF')]",
                "//a[contains(text(), 'Download')]",
                "//a[contains(@title, 'PDF')]",
                "//a[contains(@title, 'Download')]"
            ]
            
            pdf_links = []
            for selector in pdf_selectors:
                try:
                    links = driver.find_elements(By.XPATH, selector)
                    pdf_links.extend(links)
                except:
                    continue
            
            # Check each PDF link
            checked_urls = set()
            for link in pdf_links:
                try:
                    href = link.get_attribute('href')
                    text = link.text.strip()
                    
                    if not href or href in checked_urls:
                        continue
                    
                    checked_urls.add(href)
                    
                    # Check if it's a PDF and from 2025
                    if '.pdf' in href.lower() and self._is_2025_document(href, text):
                        title = link.get_attribute('title') or ''
                        pdfs.append({
                            'url': href,
                            'text': text or title or 'PDF Document 2025',
                            'type': self._determine_doc_type(href, text, title),
                            'is_pdf': True
                        })
                except:
                    continue
                    
        except Exception as e:
            logger.debug(f"Direct PDF search error: {e}")
        
        return pdfs
    
    def _find_latest_documents(self, driver) -> List[Dict]:
        """Find the latest (2025) documents"""
        documents = []
        
        try:
            # Look for "Latest" or "Most Recent" sections
            latest_patterns = [
                "Latest Reports", "Recent Filings", "Latest Documents",
                "Most Recent", "Current Reports", "Latest Earnings",
                "Recent Events", "Latest News",
                "Q3 2025", "Third Quarter 2025", "Q2 2025", "Second Quarter 2025",
                "Q1 2025", "First Quarter 2025"
            ]
            
            for pattern in latest_patterns:
                try:
                    elements = driver.find_elements(By.XPATH, 
                        f"//*[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{pattern.lower()}')]")
                    
                    for element in elements[:3]:
                        try:
                            # Get parent container and look for links
                            parent = element.find_element(By.XPATH, "./ancestor::*[self::div or self::section or self::article][1]")
                            links = parent.find_elements(By.TAG_NAME, "a")[:20]
                            
                            for link in links:
                                href = link.get_attribute('href')
                                text = link.text.strip()
                                
                                if href and self._is_2025_document(href, text):
                                    documents.append({
                                        'url': href,
                                        'text': text or 'Latest 2025 Document',
                                        'type': self._determine_doc_type(href, text, '')
                                    })
                        except:
                            continue
                except:
                    continue
                    
        except Exception as e:
            logger.debug(f"Latest documents search error: {e}")
        
        return documents
    
    def _find_sec_filings(self, driver, base_url: str) -> List[Dict]:
        """Find SEC filings for 2025"""
        documents = []
        original_url = driver.current_url
        
        try:
            # First try to select 2025 in any year dropdowns
            self._select_2025_in_dropdowns(driver)
            
            # Navigate to SEC section
            for pattern in self.SEC_NAV_PATTERNS:
                try:
                    links = driver.find_elements(By.XPATH, 
                        f"//a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{pattern.lower()}')]")
                    
                    if links:
                        href = links[0].get_attribute('href')
                        if href and 'javascript:' not in href.lower():
                            driver.get(href)
                            time.sleep(3)
                            self._wait_for_content(driver)
                            
                            # Try to select 2025 again on the new page
                            self._select_2025_in_dropdowns(driver)
                            
                            # Extract 2025 documents
                            docs = self._extract_2025_documents(driver)
                            if docs:
                                documents.extend(docs)
                                break
                except:
                    continue
            
            # Return to original page if we navigated away
            if driver.current_url != original_url and not documents:
                driver.get(original_url)
                time.sleep(2)
                    
        except Exception as e:
            logger.debug(f"SEC filing search error: {e}")
        
        return documents
    
    def _find_financial_documents(self, driver, base_url: str) -> List[Dict]:
        """Find financial documents for 2025"""
        documents = []
        original_url = driver.current_url
        
        try:
            for pattern in self.FIN_NAV_PATTERNS:
                try:
                    links = driver.find_elements(By.XPATH, 
                        f"//a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{pattern.lower()}')]")
                    
                    if links:
                        href = links[0].get_attribute('href')
                        if href and 'javascript:' not in href.lower():
                            driver.get(href)
                            time.sleep(3)
                            self._wait_for_content(driver)
                            
                            # Try to select 2025
                            self._select_2025_in_dropdowns(driver)
                            
                            docs = self._extract_2025_documents(driver)
                            if docs:
                                documents.extend(docs)
                                break
                except:
                    continue
            
            # Return to original page if needed
            if driver.current_url != original_url and not documents:
                driver.get(original_url)
                time.sleep(2)
                    
        except Exception as e:
            logger.debug(f"Financial documents search error: {e}")
        
        return documents
    
    def _find_events_section(self, driver, base_url: str) -> List[Dict]:
        """Find events and presentations for 2025"""
        documents = []
        
        try:
            event_patterns = [
                "Events", "Events & Presentations", "News & Events",
                "Presentations", "Webcasts", "Conference Calls", "Conferences"
            ]
            
            for pattern in event_patterns:
                try:
                    element = driver.find_element(By.PARTIAL_LINK_TEXT, pattern)
                    element.click()
                    time.sleep(2)
                    self._wait_for_content(driver)
                    
                    # Select 2025 if possible
                    self._select_2025_in_dropdowns(driver)
                    
                    docs = self._extract_2025_documents(driver)
                    if docs:
                        documents.extend(docs)
                        break
                except:
                    continue
                    
        except Exception as e:
            logger.debug(f"Events section error: {e}")
        
        return documents
    
    def _scan_current_page(self, driver) -> List[Dict]:
        """Comprehensive scan of current page for 2025 documents - PDFs first"""
        pdf_documents = []
        html_pages = []
        
        try:
            # Get all links
            all_links = driver.find_elements(By.TAG_NAME, "a")
            
            if self.debug:
                logger.debug(f"Scanning {len(all_links)} links on current page")
            
            # Also check iframes
            iframes = driver.find_elements(By.TAG_NAME, "iframe")
            for iframe in iframes:
                try:
                    driver.switch_to.frame(iframe)
                    iframe_links = driver.find_elements(By.TAG_NAME, "a")
                    all_links.extend(iframe_links)
                    driver.switch_to.default_content()
                except:
                    driver.switch_to.default_content()
            
            # Check each link
            checked = 0
            for link in all_links:
                if checked >= 500:  # Limit to prevent hanging
                    break
                    
                try:
                    href = link.get_attribute('href')
                    text = link.text.strip()
                    title = link.get_attribute('title') or ''
                    aria_label = link.get_attribute('aria-label') or ''
                    
                    # Also check parent text for context
                    try:
                        parent_text = link.find_element(By.XPATH, "..").text[:100]
                    except:
                        parent_text = ''
                    
                    if not href or href.startswith(('javascript:', 'mailto:', '#')):
                        continue
                    
                    checked += 1
                    
                    # Check if it's a 2025 document
                    if self._is_relevant_2025_document(href, text, title, aria_label, parent_text):
                        doc_info = {
                            'url': href,
                            'text': text or title or 'Document 2025',
                            'type': self._determine_doc_type(href, text, title),
                            'is_pdf': '.pdf' in href.lower()
                        }
                        
                        # Separate PDFs from HTML pages
                        if doc_info['is_pdf']:
                            pdf_documents.append(doc_info)
                        else:
                            html_pages.append(doc_info)
                        
                except:
                    continue
            
            # If we found HTML pages but few PDFs, explore HTML pages for PDFs
            if len(pdf_documents) < 5 and html_pages:
                logger.debug(f"Found {len(pdf_documents)} PDFs, exploring HTML pages for more...")
                for page in html_pages[:5]:  # Check first 5 HTML pages
                    pdfs = self._find_pdfs_on_page(driver, page['url'])
                    pdf_documents.extend(pdfs)
            
            # Return PDFs first, then add HTML pages only if we have few PDFs
            documents = pdf_documents
            if len(documents) < 3:
                # Only add HTML pages if we don't have enough PDFs
                documents.extend(html_pages[:5])
                    
        except Exception as e:
            logger.debug(f"Page scan error: {e}")
        
        return documents
    
    def _extract_2025_documents(self, driver) -> List[Dict]:
        """Extract only 2025 documents from current page - prioritize PDFs"""
        documents = []
        html_pages = []  # Store HTML pages to explore later
        
        try:
            # Method 1: Look in tables
            tables = driver.find_elements(By.TAG_NAME, "table")
            for table in tables[:5]:
                try:
                    rows = table.find_elements(By.TAG_NAME, "tr")
                    for row in rows[:100]:
                        row_text = row.text.lower()
                        
                        # Check specifically for 2025
                        if any(pattern in row_text for pattern in ['2025', 'fy25', 'fy 25', 'q1 25', 'q2 25', 'q3 25']):
                            links = row.find_elements(By.TAG_NAME, "a")
                            for link in links:
                                href = link.get_attribute('href')
                                link_text = link.text or ''
                                
                                if href and self._is_2025_document(href, link_text):
                                    if self._is_valid_document_url(href):
                                        # Prioritize PDFs
                                        if '.pdf' in href.lower():
                                            documents.append({
                                                'url': href,
                                                'text': link_text or 'Document 2025',
                                                'type': self._determine_doc_type(href, link_text, ''),
                                                'is_pdf': True
                                            })
                                        else:
                                            html_pages.append({
                                                'url': href,
                                                'text': link_text or 'Document 2025',
                                                'type': self._determine_doc_type(href, link_text, ''),
                                                'is_pdf': False
                                            })
                except:
                    continue
            
            # Method 2: Look in lists and divs
            containers = driver.find_elements(By.CSS_SELECTOR, 
                "ul, ol, div.documents, div.filings, div.content, div.table-responsive, article, section")
            
            for container in containers[:15]:
                try:
                    container_text = container.text.lower()
                    if any(pattern in container_text for pattern in ['2025', 'fy25', 'fy 25']):
                        links = container.find_elements(By.TAG_NAME, "a")
                        for link in links[:30]:
                            href = link.get_attribute('href')
                            link_text = link.text or ''
                            
                            if href and self._is_2025_document(href, link_text):
                                if self._is_valid_document_url(href):
                                    # Prioritize PDFs
                                    if '.pdf' in href.lower():
                                        documents.append({
                                            'url': href,
                                            'text': link_text or 'Document 2025',
                                            'type': self._determine_doc_type(href, link_text, ''),
                                            'is_pdf': True
                                        })
                                    else:
                                        html_pages.append({
                                            'url': href,
                                            'text': link_text or 'Document 2025',
                                            'type': self._determine_doc_type(href, link_text, ''),
                                            'is_pdf': False
                                        })
                except:
                    continue
            
            # If we found HTML pages but no PDFs, explore the HTML pages for PDF links
            if html_pages and len(documents) < 5:
                for page in html_pages[:3]:  # Check first 3 HTML pages
                    pdfs = self._find_pdfs_on_page(driver, page['url'])
                    documents.extend(pdfs)
                    
        except Exception as e:
            logger.debug(f"Document extraction error: {e}")
        
        return documents
    
    def _select_2025_in_dropdowns(self, driver):
        """Try to select 2025 in any year dropdowns"""
        try:
            # Find year dropdowns
            dropdowns = driver.find_elements(By.CSS_SELECTOR, 
                "select[name*='year'], select[id*='year'], select[class*='year'], "
                "select[name*='Year'], select[id*='Year'], select[class*='Year']")
            
            for dropdown in dropdowns[:3]:
                try:
                    select = Select(dropdown)
                    # Try different formats
                    for option_text in ['2025', 'FY 2025', 'FY25', 'Fiscal 2025']:
                        try:
                            select.select_by_visible_text(option_text)
                            time.sleep(2)
                            self._wait_for_content(driver)
                            break
                        except:
                            continue
                    
                    # If text didn't work, try by value
                    try:
                        select.select_by_value('2025')
                        time.sleep(2)
                    except:
                        pass
                except:
                    continue
        except:
            pass
    
    def _is_2025_document(self, url: str, text: str) -> bool:
        """Check if document is from 2025"""
        combined = f"{url} {text}".lower()
        
        # Must have 2025 or clear 2025 indicators
        has_2025 = any([
            '2025' in combined,
            'fy25' in combined,
            'fy 25' in combined,
            'fiscal 25' in combined,
            'q1 25' in combined,
            'q2 25' in combined,
            'q3 25' in combined,
            'q4 25' in combined,
            '1q25' in combined,
            '2q25' in combined,
            '3q25' in combined,
            '4q25' in combined
        ])
        
        # Exclude if it clearly belongs to another year (unless 2025 is also mentioned)
        if has_2025:
            return True
            
        # Check if it has other years without 2025
        other_years = ['2024', '2023', '2022', '2021', '2020', '2019']
        for year in other_years:
            if year in combined:
                return False
        
        return False
    
    def _is_relevant_2025_document(self, url: str, text: str, title: str, aria_label: str, parent_text: str = '') -> bool:
        """Check if a link is a relevant 2025 document"""
        combined = f"{url} {text} {title} {aria_label} {parent_text}".lower()
        
        # MUST have 2025 pattern
        has_2025 = any(re.search(pattern, combined) for pattern in self.YEAR_PATTERNS)
        
        if not has_2025:
            return False
        
        # Check for document type
        has_doc_type = False
        for doc_type, patterns in self.DOC_PATTERNS.items():
            if any(re.search(pattern, combined) for pattern in patterns):
                has_doc_type = True
                break
        
        # Check for file extension
        has_valid_ext = any(ext in url.lower() for ext in self.VALID_EXTENSIONS)
        
        # Check for document keywords
        doc_keywords = ['report', 'filing', 'form', 'release', 'presentation', 
                       'earnings', 'results', 'financials', 'proxy']
        has_doc_keyword = any(keyword in combined for keyword in doc_keywords)
        
        return has_doc_type or has_valid_ext or has_doc_keyword
    
    def _is_valid_document_url(self, url: str) -> bool:
        """Enhanced URL validation"""
        if not url:
            return False
        
        # Skip non-document URLs
        skip_patterns = ['javascript:', 'mailto:', '#', 'tel:', 'whatsapp:', 'twitter.com', 'linkedin.com']
        if any(pattern in url.lower() for pattern in skip_patterns):
            return False
        
        # Check for file extensions or document-like URLs
        doc_indicators = self.VALID_EXTENSIONS + [
            '/download/', '/files/', '/documents/', '/static/', '/media/',
            '/api/download', '/getfile', '/attachment', '/doc/',
            'format=pdf', 'type=pdf', 'download=true', '.ashx',
            '/secfiling/', '/sec/', '/ir/', 'edgar', '/reports/'
        ]
        return any(indicator in url.lower() for indicator in doc_indicators)
    
    def _determine_doc_type(self, url: str, text: str, title: str) -> str:
        """Determine document type"""
        combined = f"{url} {text} {title}".lower()
        
        # Check quarter first
        if any(q in combined for q in ['q3', 'third quarter', '3q']):
            return "Q3_2025"
        elif any(q in combined for q in ['q2', 'second quarter', '2q']):
            return "Q2_2025"
        elif any(q in combined for q in ['q1', 'first quarter', '1q']):
            return "Q1_2025"
        elif any(q in combined for q in ['q4', 'fourth quarter', '4q']):
            return "Q4_2025"
        
        # Check document types
        for doc_type, patterns in self.DOC_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, combined):
                    return doc_type
        
        return "Document_2025"
    
    def _find_pdfs_on_page(self, driver, url: str) -> List[Dict]:
        """Navigate to a page and find PDF links"""
        pdfs = []
        original_url = driver.current_url
        
        try:
            driver.get(url)
            time.sleep(2)
            self._wait_for_content(driver)
            
            # Look for all PDF links
            pdf_links = driver.find_elements(By.XPATH, "//a[contains(@href, '.pdf')]")
            
            # Also look for download buttons/links
            download_links = driver.find_elements(By.XPATH, 
                "//a[contains(text(), 'Download')] | //a[contains(text(), 'PDF')] | "
                "//button[contains(text(), 'Download')] | //button[contains(text(), 'PDF')]")
            
            all_links = pdf_links + download_links
            
            for link in all_links[:10]:
                try:
                    href = link.get_attribute('href')
                    text = link.text.strip()
                    
                    if href and ('.pdf' in href.lower() or 'download' in href.lower()):
                        # Check if it's 2025
                        if self._is_2025_document(href, text):
                            pdfs.append({
                                'url': href,
                                'text': text or 'PDF Document 2025',
                                'type': self._determine_doc_type(href, text, ''),
                                'is_pdf': True
                            })
                except:
                    continue
            
            # Return to original page
            if driver.current_url != original_url:
                driver.get(original_url)
                time.sleep(1)
                
        except Exception as e:
            logger.debug(f"PDF search on page error: {e}")
            try:
                driver.get(original_url)
            except:
                pass
        
        return pdfs
    
    def _deduplicate_documents(self, documents: List[Dict]) -> List[Dict]:
        """Remove duplicate documents and prioritize PDFs"""
        seen_urls = set()
        pdf_docs = []
        other_docs = []
        
        for doc in documents:
            url = doc['url']
            # Normalize URL for comparison
            normalized_url = url.split('?')[0].split('#')[0].rstrip('/').lower()
            
            if normalized_url not in seen_urls:
                seen_urls.add(normalized_url)
                # Prioritize PDFs
                is_pdf = doc.get('is_pdf', False) or '.pdf' in url.lower()
                if is_pdf:
                    doc['is_pdf'] = True
                    pdf_docs.append(doc)
                else:
                    doc['is_pdf'] = False
                    other_docs.append(doc)
        
        # Return PDFs first, then others if we need more documents
        result = pdf_docs
        if len(result) < 5:  # If we have less than 5 PDFs, add some HTML pages
            result.extend(other_docs[:5])
        
        return result
        """Remove duplicate documents"""
        seen_urls = set()
        unique = []
        
        for doc in documents:
            url = doc['url']
            # Normalize URL for comparison
            normalized_url = url.split('?')[0].split('#')[0].rstrip('/').lower()
            
            if normalized_url not in seen_urls:
                seen_urls.add(normalized_url)
                unique.append(doc)
        
        return unique
    
    def download_document(self, doc_info: Dict, company_dir: Path, ticker: str, max_retries: int = 3) -> bool:
        """Download document with retry logic"""
        for attempt in range(max_retries):
            try:
                url = doc_info['url']
                
                # Generate unique filename
                doc_type = doc_info.get('type', 'document_2025').replace(' ', '_')
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                
                # Determine extension
                ext = '.pdf'  # Default
                for extension in self.VALID_EXTENSIONS:
                    if extension in url.lower():
                        ext = extension
                        break
                
                # Check content type from HEAD request
                try:
                    head_response = requests.head(url, timeout=5, allow_redirects=True, verify=False)
                    content_type = head_response.headers.get('Content-Type', '').lower()
                    
                    if 'pdf' in content_type:
                        ext = '.pdf'
                    elif 'excel' in content_type or 'spreadsheet' in content_type:
                        ext = '.xlsx'
                    elif 'word' in content_type:
                        ext = '.docx'
                    elif 'html' in content_type:
                        ext = '.html'
                except:
                    pass
                
                filename = f"{ticker}_2025_{doc_type}_{timestamp}{ext}"
                filepath = company_dir / filename
                
                # Enhanced headers
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.5',
                    'Accept-Encoding': 'gzip, deflate',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                    'Referer': url.split('/')[0] + '//' + url.split('/')[2] + '/'
                }
                
                # Download with session
                session = requests.Session()
                session.headers.update(headers)
                
                response = session.get(url, timeout=30, stream=True, verify=False, allow_redirects=True)
                response.raise_for_status()
                
                # Check if we got HTML when we expected a document
                content_type = response.headers.get('Content-Type', '').lower()
                if 'text/html' in content_type and ext not in ['.html', '.ashx']:
                    session.close()
                    return False
                
                # Save file
                with open(filepath, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                
                # Verify file size
                if filepath.stat().st_size < 512:  # Less than 512 bytes
                    filepath.unlink()
                    session.close()
                    return False
                
                logger.debug(f"{ticker}: Downloaded {filename} ({filepath.stat().st_size} bytes)")
                session.close()
                return True
                
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff
                    continue
                logger.debug(f"{ticker}: Download failed after {max_retries} attempts - {str(e)[:50]}")
                return False
            except Exception as e:
                logger.debug(f"{ticker}: Download error - {str(e)[:50]}")
                return False
    
    def download_with_browser(self, doc_info: Dict, company_dir: Path, ticker: str, driver) -> bool:
        """Fallback download using browser - for JavaScript-triggered downloads"""
        try:
            url = doc_info['url']
            doc_type = doc_info.get('type', 'document_2025').replace(' ', '_')
            
            # Navigate to the URL
            original_url = driver.current_url
            driver.get(url)
            time.sleep(3)
            
            # Check if it's a PDF viewer or document page
            current_url = driver.current_url
            
            # Try multiple download strategies
            downloaded = False
            
            # Strategy 1: Look for download button
            download_selectors = [
                "//button[contains(text(), 'Download')]",
                "//a[contains(text(), 'Download')]",
                "//button[contains(@class, 'download')]",
                "//a[contains(@class, 'download')]",
                "//button[contains(@title, 'Download')]",
                "//a[contains(@title, 'Download')]",
                "//button[@aria-label='Download']",
                "//a[@aria-label='Download']",
                "//*[@id='download']"
            ]
            
            for selector in download_selectors:
                try:
                    download_btn = driver.find_element(By.XPATH, selector)
                    driver.execute_script("arguments[0].click();", download_btn)
                    time.sleep(3)
                    downloaded = True
                    break
                except:
                    continue
            
            # Strategy 2: Try keyboard shortcut (Ctrl+S)
            if not downloaded:
                try:
                    body = driver.find_element(By.TAG_NAME, 'body')
                    body.send_keys(Keys.CONTROL + 's')
                    time.sleep(3)
                    # Press Enter to confirm save dialog (if any)
                    body.send_keys(Keys.RETURN)
                    time.sleep(2)
                    downloaded = True
                except:
                    pass
            
            # Strategy 3: Check if the page itself is the document
            if not downloaded and ('pdf' in current_url.lower() or 'download' in current_url.lower()):
                # The navigation itself might trigger download
                time.sleep(3)
                downloaded = True
            
            # Return to original page
            if driver.current_url != original_url:
                driver.get(original_url)
                time.sleep(2)
            
            if downloaded:
                # Check if file was downloaded (look for new files in directory)
                files_after = list(company_dir.glob("*"))
                if len(files_after) > 0:
                    logger.debug(f"{ticker}: Browser download successful for {doc_type}")
                    return True
            
            return False
            
        except Exception as e:
            logger.debug(f"{ticker}: Browser download failed - {str(e)[:50]}")
            return False
    
    def run(self):
        """Main execution"""
        # Load IR results
        with open(self.input_file, 'r') as f:
            data = json.load(f)
            companies = [c for c in data.get('companies_processed', []) 
                        if c.get('ir_page_found')]
        
        print(f"\n{'='*60}")
        print(f"2025 DOCUMENT DOWNLOADER - Enhanced Version")
        print(f"{'='*60}")
        print(f"Companies: {len(companies)}")
        print(f"Workers: {self.max_workers}")
        print(f"Output: {self.output_dir}")
        print(f"Target: 2025 Documents Only")
        print(f"Debug Mode: {self.debug}")
        print(f"{'='*60}\n")
        
        start_time = time.time()
        self.execution_time = 0
        self.companies = companies
        
        # Process companies in parallel
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_company = {
                executor.submit(self.process_company, company): company
                for company in companies
            }
            
            completed = 0
            self.total_docs = 0
            
            for future in as_completed(future_to_company):
                company = future_to_company[future]
                completed += 1
                
                try:
                    result = future.result(timeout=300)  # 5 minute timeout
                    self.total_docs = self.stats['total_documents']
                    
                    # Progress update
                    elapsed = time.time() - start_time
                    rate = completed / elapsed if elapsed > 0 else 1
                    eta = (len(companies) - completed) / rate if rate > 0 else 0
                    
                    status = "✓" if result['status'] == 'success' else "✗"
                    strategies = ', '.join(result['strategies_used']) if result['strategies_used'] else 'none'
                    
                    print(f"[{completed}/{len(companies)}] {status} {result['ticker']}: "
                          f"{result['documents_downloaded']}/{result['documents_found']} docs | "
                          f"Strategies: {strategies} | ETA: {eta:.0f}s")
                    
                except Exception as e:
                    print(f"[{completed}/{len(companies)}] ✗ {company['ticker']}: Timeout/Error - {str(e)[:50]}")
                    with self.lock:
                        self.stats['failed_companies'].append(company['ticker'])
        
        # Calculate final stats
        self.execution_time = time.time() - start_time
        
        print(f"\n{'='*60}")
        print(f"DOWNLOAD COMPLETE")
        print(f"{'='*60}")
        print(f"Time: {self.execution_time:.1f} seconds ({self.execution_time/60:.1f} minutes)")
        print(f"Companies: {self.stats['companies_processed']}")
        print(f"Documents (2025): {self.stats['total_documents']}")
        print(f"Direct Downloads: {self.stats['direct_downloads']}")
        print(f"Browser Downloads: {self.stats['browser_downloads']}")
        print(f"Failed: {len(self.stats['failed_companies'])}")
        
        if self.stats['failed_companies']:
            print(f"Failed companies: {', '.join(self.stats['failed_companies'])}")
        
        if self.stats['companies_processed'] > 0:
            print(f"Average: {self.execution_time/len(companies):.1f}s per company")
            print(f"Documents per company: {self.stats['total_documents']/self.stats['companies_processed']:.1f}")
        print(f"{'='*60}")
        
        self.save_summary()
    
    def save_summary(self):
        """Save summary with proper JSON serialization"""
        summary = {
            'execution_time': self.execution_time,
            'total_companies': len(self.companies),
            'total_documents': self.total_docs,
            'companies': self.results,
            'timestamp': datetime.now().isoformat(),
            'stats': {
                'companies_processed': self.stats['companies_processed'],
                'total_documents': self.stats['total_documents'],
                'direct_downloads': self.stats['direct_downloads'],
                'browser_downloads': self.stats['browser_downloads'],
                'failed_companies': self.stats['failed_companies'],
                'start_time': self.stats['start_time'].isoformat()
            },
            'config': {
                'max_workers': self.max_workers,
                'headless': self.headless,
                'target_year': '2025'
            }
        }
        
        summary_path = self.output_dir / 'download_summary_2025.json'
        with open(summary_path, 'w') as f:
            json.dump(summary, f, indent=2, default=str)
        
        print(f"\nSummary saved to: {summary_path}")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Download 2025 earnings documents from Dow 30 companies')
    parser.add_argument('--workers', type=int, default=3, help='Number of parallel workers')
    parser.add_argument('--input', default='output/ir_finder_results.json', help='IR results file')
    parser.add_argument('--output', default='downloads_2025', help='Output directory')
    parser.add_argument('--headless', action='store_true', help='Run browsers in headless mode')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    
    args = parser.parse_args()
    
    downloader = Enhanced2025Downloader(
        input_file=args.input,
        output_dir=args.output,
        max_workers=args.workers,
        headless=args.headless,
        debug=args.debug
    )
    
    downloader.run()


if __name__ == "__main__":
    main()