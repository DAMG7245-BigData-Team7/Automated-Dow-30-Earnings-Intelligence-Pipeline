#!/usr/bin/env python3
"""
Enhanced Earnings Document Downloader
Handles multiple quarters and various IR page structures
"""

import os
import re
import json
import time
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse
from datetime import datetime, timedelta
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class EnhancedEarningsDownloader:
    """Enhanced downloader for latest earnings documents"""
    
    def __init__(self, input_file: str = "output/ir_finder_results.json", 
                 output_dir: str = "downloads", headless: bool = True):
        self.input_file = Path(input_file)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.headless = headless
        self.driver = None
        self.session_data = {
            'start_time': datetime.now().isoformat(),
            'companies': []
        }
        
        # Expanded document patterns - more flexible
        self.DOC_PATTERNS = {
            'earnings': [
                r'earnings\s*release', r'earnings\s*report', r'financial\s*results',
                r'quarterly\s*results', r'q[1-4]\s*20\d{2}', r'quarterly\s*earnings',
                r'results\s*of\s*operations', r'financial\s*performance'
            ],
            'presentation': [
                r'presentation', r'slides', r'investor\s*deck', r'earnings\s*call',
                r'conference\s*call', r'webcast'
            ],
            '10-Q': [r'10-?q', r'form\s*10-?q', r'quarterly\s*report\s*on\s*form'],
            '10-K': [r'10-?k', r'form\s*10-?k', r'annual\s*report\s*on\s*form'],
            '8-K': [r'8-?k', r'form\s*8-?k', r'current\s*report']
        }
        
        # Get current and recent quarters
        self.target_periods = self._get_target_periods()
        
    def _get_target_periods(self) -> List[Dict]:
        """Get current and recent quarters to search for"""
        current_date = datetime.now()
        periods = []
        
        # Current year quarters
        current_year = current_date.year
        current_quarter = (current_date.month - 1) // 3 + 1
        
        # Add current quarter and previous 2 quarters
        for q_offset in range(3):
            q = current_quarter - q_offset
            year = current_year
            
            if q <= 0:
                q += 4
                year -= 1
            
            periods.append({
                'year': year,
                'quarter': q,
                'patterns': [
                    f"Q{q} {year}",
                    f"Q{q}'{str(year)[2:]}",
                    f"{year} Q{q}",
                    f"FY{year} Q{q}",
                    f"FY{str(year)[2:]} Q{q}",
                    self._quarter_to_month_range(q, year)
                ]
            })
        
        # Also add annual patterns
        periods.extend([
            {'year': current_year, 'type': 'annual', 'patterns': [f"{current_year}", f"FY{current_year}", f"FY{str(current_year)[2:]}"]},
            {'year': current_year - 1, 'type': 'annual', 'patterns': [f"{current_year - 1}", f"FY{current_year - 1}"]}
        ])
        
        return periods
    
    def _quarter_to_month_range(self, quarter: int, year: int) -> str:
        """Convert quarter to month range string"""
        quarters = {
            1: "January - March",
            2: "April - June", 
            3: "July - September",
            4: "October - December"
        }
        return f"{quarters[quarter]} {year}"
    
    def setup_driver(self):
        """Setup Chrome driver with anti-detection"""
        chrome_options = Options()
        
        if self.headless:
            chrome_options.add_argument("--headless=new")
        
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        
        self.driver = webdriver.Chrome(options=chrome_options)
        self.driver.set_page_load_timeout(30)
        self.driver.implicitly_wait(5)
        logger.info("Chrome driver initialized")
    
    def find_latest_earnings(self, ticker: str, ir_url: str) -> List[Dict]:
        """Find latest earnings documents with multiple strategies"""
        documents = []
        
        try:
            logger.info(f"Processing {ticker}: {ir_url}")
            self.driver.get(ir_url)
            time.sleep(3)
            
            # Strategy 1: Direct search for latest earnings
            docs = self._find_by_latest_earnings_section()
            if docs:
                documents.extend(docs)
            
            # Strategy 2: Search in SEC filings with flexible navigation
            sec_docs = self._find_in_sec_filings()
            if sec_docs:
                documents.extend(sec_docs)
            
            # Strategy 3: Events/News section
            event_docs = self._find_in_events_section()
            if event_docs:
                documents.extend(event_docs)
            
            # Strategy 4: Direct page scan for recent documents
            page_docs = self._scan_page_for_recent_docs()
            if page_docs:
                documents.extend(page_docs)
            
            # Deduplicate
            seen_urls = set()
            unique_docs = []
            for doc in documents:
                if doc['url'] not in seen_urls:
                    seen_urls.add(doc['url'])
                    unique_docs.append(doc)
            
            # Sort by date if available, prioritize most recent
            unique_docs.sort(key=lambda x: x.get('date_score', 0), reverse=True)
            
            return unique_docs[:10]  # Return top 10 most relevant
            
        except Exception as e:
            logger.error(f"Error processing {ticker}: {e}")
            return []
    
    def _find_by_latest_earnings_section(self) -> List[Dict]:
        """Find documents in latest earnings section"""
        documents = []
        
        try:
            # Look for "Latest Results" or similar sections
            latest_patterns = [
                "Latest Results", "Recent Results", "Latest Earnings",
                "Quarterly Results", "Financial Results", "Recent Reports",
                "Latest Reports", "Current Quarter"
            ]
            
            for pattern in latest_patterns:
                try:
                    # Try to find and click the section
                    element = self.driver.find_element(By.XPATH, 
                        f"//*[contains(text(), '{pattern}')]")
                    
                    # Check if it's a link
                    if element.tag_name == 'a':
                        href = element.get_attribute('href')
                        if href:
                            self.driver.get(href)
                            time.sleep(2)
                    
                    # Look for documents in this section
                    docs = self._extract_documents_from_page()
                    if docs:
                        documents.extend(docs)
                        break
                        
                except:
                    continue
                    
        except Exception as e:
            logger.debug(f"Error in latest earnings search: {e}")
        
        return documents
    
    def _find_in_sec_filings(self) -> List[Dict]:
        """Enhanced SEC filings search"""
        documents = []
        
        try:
            # Multiple ways to find SEC filings
            sec_urls = self._generate_sec_urls()
            
            for url in sec_urls:
                try:
                    self.driver.get(url)
                    time.sleep(2)
                    
                    # Check if valid page
                    if '404' not in self.driver.title.lower():
                        docs = self._extract_documents_from_page()
                        if docs:
                            documents.extend(docs)
                            break
                except:
                    continue
            
            # Also try clicking SEC filings links
            if not documents:
                self.driver.get(self.current_ir_url)  # Go back to IR page
                time.sleep(2)
                
                sec_link_patterns = ["SEC Filings", "Filings", "SEC", "Edgar"]
                for pattern in sec_link_patterns:
                    try:
                        link = self.driver.find_element(By.PARTIAL_LINK_TEXT, pattern)
                        link.click()
                        time.sleep(3)
                        
                        docs = self._extract_documents_from_page()
                        if docs:
                            documents.extend(docs)
                            break
                    except:
                        continue
                        
        except Exception as e:
            logger.debug(f"Error in SEC filings search: {e}")
        
        return documents
    
    def _generate_sec_urls(self) -> List[str]:
        """Generate possible SEC filing URLs"""
        base_url = self.driver.current_url
        parsed = urlparse(base_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        
        patterns = [
            '/sec-filings',
            '/financials/sec-filings',
            '/investor-relations/sec-filings',
            '/investors/sec-filings',
            '/investor/sec-filings',
            '/ir/sec-filings',
            '/financial-information/sec-filings'
        ]
        
        return [base + pattern for pattern in patterns]
    
    def _find_in_events_section(self) -> List[Dict]:
        """Find documents in events/news section"""
        documents = []
        
        try:
            event_patterns = [
                "Events", "News", "Press Releases", "Events & Presentations",
                "News & Events", "Latest News", "Newsroom"
            ]
            
            for pattern in event_patterns:
                try:
                    element = self.driver.find_element(By.PARTIAL_LINK_TEXT, pattern)
                    element.click()
                    time.sleep(2)
                    
                    docs = self._extract_documents_from_page()
                    if docs:
                        documents.extend(docs)
                        break
                except:
                    continue
                    
        except Exception as e:
            logger.debug(f"Error in events search: {e}")
        
        return documents
    
    def _scan_page_for_recent_docs(self) -> List[Dict]:
        """Scan current page for recent documents"""
        documents = []
        
        try:
            # Get all links
            links = self.driver.find_elements(By.TAG_NAME, "a")
            
            for link in links:
                try:
                    href = link.get_attribute('href')
                    text = link.text.strip()
                    
                    if not href or not text:
                        continue
                    
                    # Check if it matches our document patterns
                    doc_info = self._analyze_link(link, href, text)
                    if doc_info:
                        documents.append(doc_info)
                        
                except:
                    continue
                    
        except Exception as e:
            logger.debug(f"Error scanning page: {e}")
        
        return documents
    
    def _extract_documents_from_page(self) -> List[Dict]:
        """Extract relevant documents from current page"""
        documents = []
        
        try:
            # Look in tables
            tables = self.driver.find_elements(By.TAG_NAME, "table")
            for table in tables:
                rows = table.find_elements(By.TAG_NAME, "tr")
                for row in rows:
                    row_text = row.text.lower()
                    
                    # Check if row contains recent period
                    for period in self.target_periods:
                        if any(p.lower() in row_text for p in period['patterns']):
                            # Get links in this row
                            links = row.find_elements(By.TAG_NAME, "a")
                            for link in links:
                                doc_info = self._analyze_link(
                                    link, 
                                    link.get_attribute('href'),
                                    link.text,
                                    period=period
                                )
                                if doc_info:
                                    documents.append(doc_info)
            
            # Also check divs and lists
            containers = self.driver.find_elements(By.CSS_SELECTOR, "div.document, div.filing, li")
            for container in containers:
                container_text = container.text.lower()
                
                for period in self.target_periods:
                    if any(p.lower() in container_text for p in period['patterns']):
                        links = container.find_elements(By.TAG_NAME, "a")
                        for link in links:
                            doc_info = self._analyze_link(
                                link,
                                link.get_attribute('href'),
                                link.text,
                                period=period
                            )
                            if doc_info:
                                documents.append(doc_info)
                                
        except Exception as e:
            logger.debug(f"Error extracting documents: {e}")
        
        return documents
    
    def _analyze_link(self, element, href: str, text: str, period: Dict = None) -> Optional[Dict]:
        """Analyze if a link is a relevant document"""
        if not href:
            return None
        
        # Skip javascript and mailto
        if href.startswith(('javascript:', 'mailto:')):
            return None
        
        combined_text = f"{href} {text}".lower()
        
        # Check document type
        doc_type = None
        type_score = 0
        
        for dtype, patterns in self.DOC_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, combined_text):
                    doc_type = dtype
                    type_score = 10
                    break
            if doc_type:
                break
        
        # Check file extension
        valid_ext = any(ext in href.lower() for ext in ['.pdf', '.xlsx', '.xls'])
        if valid_ext:
            type_score += 5
        
        # Calculate date relevance score
        date_score = 0
        if period:
            date_score = 20
        else:
            # Check if any target period is mentioned
            for p in self.target_periods:
                if any(pattern.lower() in combined_text for pattern in p['patterns']):
                    date_score = 15
                    period = p
                    break
        
        # Total score
        total_score = type_score + date_score
        
        if total_score >= 10:  # Lower threshold for better coverage
            return {
                'url': href,
                'text': text or 'Document',
                'type': doc_type or 'Unknown',
                'period': period,
                'score': total_score,
                'date_score': date_score
            }
        
        return None
    
    def download_document(self, doc_info: Dict, company_dir: Path, ticker: str) -> bool:
        """Download a document"""
        try:
            url = doc_info['url']
            
            # Generate filename
            doc_type = doc_info.get('type', 'document').replace(' ', '_')
            period_str = ''
            if doc_info.get('period'):
                p = doc_info['period']
                if 'quarter' in p:
                    period_str = f"_Q{p['quarter']}_{p['year']}"
                else:
                    period_str = f"_{p['year']}"
            
            timestamp = datetime.now().strftime('%Y%m%d')
            extension = '.pdf'  # Default
            
            # Check URL for extension
            for ext in ['.pdf', '.xlsx', '.xls', '.doc', '.docx']:
                if ext in url.lower():
                    extension = ext
                    break
            
            filename = f"{ticker}_{doc_type}{period_str}_{timestamp}{extension}"
            filepath = company_dir / filename
            
            # Skip if exists
            if filepath.exists():
                logger.info(f"Already exists: {filename}")
                return True
            
            logger.info(f"Downloading: {filename}")
            
            # Download with requests
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=30, verify=False)
            response.raise_for_status()
            
            # Save file
            with open(filepath, 'wb') as f:
                f.write(response.content)
            
            file_size_mb = filepath.stat().st_size / (1024 * 1024)
            logger.info(f"Downloaded: {filename} ({file_size_mb:.2f} MB)")
            
            doc_info['downloaded'] = True
            doc_info['filename'] = filename
            doc_info['size_mb'] = file_size_mb
            
            return True
            
        except Exception as e:
            logger.error(f"Download failed: {e}")
            doc_info['downloaded'] = False
            doc_info['error'] = str(e)
            return False
    
    def process_company(self, company_data: Dict) -> Dict:
        """Process a single company"""
        ticker = company_data['ticker']
        ir_url = company_data['ir_url']
        
        # Store current URL for navigation
        self.current_ir_url = ir_url
        
        # Create company directory
        company_dir = self.output_dir / ticker
        company_dir.mkdir(exist_ok=True)
        
        # Find documents
        documents = self.find_latest_earnings(ticker, ir_url)
        
        result = {
            'ticker': ticker,
            'company_name': company_data['company_name'],
            'ir_url': ir_url,
            'documents_found': len(documents),
            'documents_downloaded': 0,
            'documents': []
        }
        
        # Download documents (limit to top 5 most relevant)
        for doc in documents[:5]:
            if self.download_document(doc, company_dir, ticker):
                result['documents_downloaded'] += 1
            result['documents'].append(doc)
        
        return result
    
    def run(self):
        """Main execution"""
        # Load IR results
        with open(self.input_file, 'r') as f:
            data = json.load(f)
            companies = [c for c in data.get('companies_processed', []) if c.get('ir_page_found')]
        
        # Setup driver
        self.setup_driver()
        
        try:
            print(f"\n{'='*60}")
            print("ENHANCED EARNINGS DOCUMENT DOWNLOADER")
            print(f"{'='*60}\n")
            print(f"Processing {len(companies)} companies")
            print(f"Target periods: {', '.join([f'Q{p['quarter']} {p['year']}' for p in self.target_periods[:3] if 'quarter' in p])}\n")
            
            # Process each company
            for i, company in enumerate(companies, 1):
                print(f"\n[{i}/{len(companies)}] {company['ticker']} - {company['company_name']}")
                print("-" * 40)
                
                result = self.process_company(company)
                self.session_data['companies'].append(result)
                
                print(f"Found: {result['documents_found']} documents")
                print(f"Downloaded: {result['documents_downloaded']} documents")
                
                # Rate limiting
                time.sleep(3)
                
                # Restart driver every 5 companies
                if i % 5 == 0:
                    self.setup_driver()
            
            # Save results
            self.save_results()
            
        finally:
            if self.driver:
                self.driver.quit()
    
    def save_results(self):
        """Save download results"""
        # Save JSON log
        log_file = self.output_dir / "enhanced_download_log.json"
        with open(log_file, 'w') as f:
            json.dump(self.session_data, f, indent=2)
        
        # Create summary
        summary_file = self.output_dir / "enhanced_summary.txt"
        with open(summary_file, 'w') as f:
            f.write("ENHANCED EARNINGS DOWNLOAD SUMMARY\n")
            f.write("=" * 60 + "\n\n")
            
            total_found = sum(c['documents_found'] for c in self.session_data['companies'])
            total_downloaded = sum(c['documents_downloaded'] for c in self.session_data['companies'])
            
            f.write(f"Companies processed: {len(self.session_data['companies'])}\n")
            f.write(f"Total documents found: {total_found}\n")
            f.write(f"Total documents downloaded: {total_downloaded}\n\n")
            
            f.write("BY COMPANY:\n")
            f.write("-" * 40 + "\n")
            
            for company in self.session_data['companies']:
                f.write(f"\n{company['ticker']}: {company['documents_downloaded']}/{company['documents_found']} documents\n")
                for doc in company['documents']:
                    if doc.get('downloaded'):
                        f.write(f"  ✓ {doc.get('filename', 'Unknown')}\n")
        
        print(f"\nResults saved to {self.output_dir}")


def main():
    downloader = EnhancedEarningsDownloader()
    downloader.run()


if __name__ == "__main__":
    main()