#!/usr/bin/env python3
"""
Focused Parallel Document Downloader
Downloads earnings documents from IR pages in parallel
"""

import json
import time
import logging
import requests
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional
import threading
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(threadName)-10s] - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class ParallelDocumentDownloader:
    """Parallel downloader optimized for speed"""
    
    def __init__(self, 
                 input_file: str = "output/ir_finder_results.json",
                 output_dir: str = "downloads",
                 max_workers: int = 5,
                 headless: bool = True):
        
        self.input_file = Path(input_file)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.max_workers = max_workers
        self.headless = headless
        
        # Thread-safe counters
        self.lock = threading.Lock()
        self.stats = {
            'companies_processed': 0,
            'total_documents': 0,
            'failed_companies': [],
            'start_time': datetime.now()
        }
        
        # Document patterns
        self.YEAR_PATTERNS = ['2025', '2024', 'q3 2024', 'q2 2025', 'q1 2025']
        self.DOC_KEYWORDS = ['earnings', 'quarterly', 'annual', '10-q', '10-k', 
                            'presentation', 'results', 'report', 'financial']
    
    def create_driver(self) -> webdriver.Chrome:
        """Create Chrome driver for each thread"""
        chrome_options = Options()
        
        if self.headless:
            chrome_options.add_argument("--headless=new")
        
        # Minimal options for speed
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-images")  # Don't load images
        chrome_options.add_argument("--disable-javascript")  # Try without JS first
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.page_load_strategy = 'eager'  # Don't wait for all resources
        
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(20)  # Shorter timeout
        driver.implicitly_wait(3)
        
        return driver
    
    def process_company(self, company_data: Dict) -> Dict:
        """Process a single company - main worker function"""
        ticker = company_data['ticker']
        ir_url = company_data['ir_url']
        
        result = {
            'ticker': ticker,
            'company_name': company_data['company_name'],
            'documents_found': 0,
            'documents_downloaded': 0,
            'status': 'pending'
        }
        
        driver = None
        
        try:
            # Create company directory
            company_dir = self.output_dir / ticker
            company_dir.mkdir(exist_ok=True)
            
            logger.info(f"Starting {ticker}")
            
            # Method 1: Try direct document search without Selenium first
            direct_docs = self.find_documents_direct(ir_url)
            
            if direct_docs:
                logger.info(f"{ticker}: Found {len(direct_docs)} documents via direct search")
                result['documents_found'] = len(direct_docs)
                
                # Download documents
                for doc in direct_docs[:10]:  # Limit to 10 docs
                    if self.download_document(doc, company_dir, ticker):
                        result['documents_downloaded'] += 1
            
            # Method 2: Use Selenium if no documents found directly
            if result['documents_found'] == 0:
                logger.info(f"{ticker}: Using Selenium for dynamic content")
                driver = self.create_driver()
                driver.get(ir_url)
                time.sleep(2)
                
                selenium_docs = self.find_documents_selenium(driver)
                result['documents_found'] = len(selenium_docs)
                
                for doc in selenium_docs[:10]:
                    if self.download_document(doc, company_dir, ticker):
                        result['documents_downloaded'] += 1
            
            result['status'] = 'success'
            
            # Update stats
            with self.lock:
                self.stats['companies_processed'] += 1
                self.stats['total_documents'] += result['documents_downloaded']
            
            logger.info(f"✓ {ticker}: Downloaded {result['documents_downloaded']} documents")
            
        except Exception as e:
            logger.error(f"✗ {ticker}: {str(e)[:100]}")
            result['status'] = 'failed'
            result['error'] = str(e)
            
            with self.lock:
                self.stats['failed_companies'].append(ticker)
        
        finally:
            if driver:
                try:
                    driver.quit()
                except:
                    pass
        
        return result
    
    def find_documents_direct(self, ir_url: str) -> List[Dict]:
        """Try to find document links directly without Selenium"""
        documents = []
        
        try:
            # Try common document URL patterns
            base_url = '/'.join(ir_url.split('/')[:3])
            
            # Common paths to check
            doc_paths = [
                '/financials/quarterly-results',
                '/sec-filings',
                '/financial-information/quarterly-earnings',
                '/investors/financial-information',
                '/investor-relations/financials'
            ]
            
            for path in doc_paths:
                try:
                    test_url = base_url + path
                    response = requests.get(test_url, timeout=5)
                    
                    if response.status_code == 200:
                        # Parse for document links
                        content = response.text.lower()
                        
                        # Simple regex to find PDF/Excel links
                        import re
                        pdf_pattern = r'href=["\']([^"\']*\.pdf[^"\']*)["\'"]'
                        excel_pattern = r'href=["\']([^"\']*\.xlsx?[^"\']*)["\'"]'
                        
                        pdf_links = re.findall(pdf_pattern, content, re.IGNORECASE)
                        excel_links = re.findall(excel_pattern, content, re.IGNORECASE)
                        
                        for link in pdf_links + excel_links:
                            # Check if it's a recent document
                            if any(year in link.lower() for year in self.YEAR_PATTERNS):
                                if any(kw in link.lower() for kw in self.DOC_KEYWORDS):
                                    full_url = link if link.startswith('http') else base_url + link
                                    documents.append({
                                        'url': full_url,
                                        'type': 'direct_find'
                                    })
                        
                        if documents:
                            break
                            
                except:
                    continue
                    
        except Exception as e:
            logger.debug(f"Direct search failed: {e}")
        
        return documents[:10]  # Limit results
    
    def find_documents_selenium(self, driver) -> List[Dict]:
        """Find documents using Selenium"""
        documents = []
        
        try:
            # Quick scan for document links
            links = driver.find_elements(By.PARTIAL_LINK_TEXT, "2024")
            links.extend(driver.find_elements(By.PARTIAL_LINK_TEXT, "2025"))
            links.extend(driver.find_elements(By.PARTIAL_LINK_TEXT, "Q3"))
            links.extend(driver.find_elements(By.PARTIAL_LINK_TEXT, "Q2"))
            links.extend(driver.find_elements(By.PARTIAL_LINK_TEXT, "Earnings"))
            
            # Also get all links and filter
            all_links = driver.find_elements(By.TAG_NAME, "a")[:100]  # Limit scan
            
            for link in all_links:
                try:
                    href = link.get_attribute('href')
                    text = link.text.lower()
                    
                    if not href:
                        continue
                    
                    # Check if it's a document
                    if any(ext in href.lower() for ext in ['.pdf', '.xlsx', '.xls']):
                        # Check if it's recent/relevant
                        combined = f"{href} {text}".lower()
                        if any(pattern in combined for pattern in self.YEAR_PATTERNS):
                            documents.append({
                                'url': href,
                                'text': text or 'Document'
                            })
                            
                except:
                    continue
            
        except Exception as e:
            logger.debug(f"Selenium search error: {e}")
        
        return documents[:10]
    
    def download_document(self, doc_info: Dict, company_dir: Path, ticker: str) -> bool:
        """Download a single document"""
        try:
            url = doc_info['url']
            
            # Generate filename
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            
            # Determine extension from URL
            if '.pdf' in url.lower():
                ext = '.pdf'
            elif '.xlsx' in url.lower():
                ext = '.xlsx'
            elif '.xls' in url.lower():
                ext = '.xls'
            else:
                ext = '.pdf'  # Default
            
            filename = f"{ticker}_doc_{timestamp}{ext}"
            filepath = company_dir / filename
            
            # Skip if similar file exists (avoid duplicates)
            existing_files = list(company_dir.glob(f"{ticker}_doc_*{ext}"))
            if len(existing_files) >= 3:  # Already have enough docs of this type
                return False
            
            # Download with timeout
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=15, stream=True, verify=False)
            response.raise_for_status()
            
            # Check content type
            content_type = response.headers.get('Content-Type', '')
            if 'text/html' in content_type and ext != '.html':
                return False  # Skip HTML pages when expecting documents
            
            # Save file
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            # Verify minimum size
            if filepath.stat().st_size < 1024:  # Less than 1KB
                filepath.unlink()
                return False
            
            logger.debug(f"{ticker}: Downloaded {filename}")
            return True
            
        except Exception as e:
            logger.debug(f"{ticker}: Download failed - {str(e)[:50]}")
            return False
    
    def run(self):
        """Main execution"""
        # Load IR results
        with open(self.input_file, 'r') as f:
            data = json.load(f)
            companies = [c for c in data.get('companies_processed', []) 
                        if c.get('ir_page_found')]
        
        print(f"\n{'='*60}")
        print(f"PARALLEL DOCUMENT DOWNLOADER")
        print(f"{'='*60}")
        print(f"Companies: {len(companies)}")
        print(f"Workers: {self.max_workers}")
        print(f"Output: {self.output_dir}")
        print(f"{'='*60}\n")
        
        start_time = time.time()
        
        # Process companies in parallel
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all tasks
            future_to_company = {
                executor.submit(self.process_company, company): company
                for company in companies
            }
            
            # Process as they complete
            completed = 0
            for future in as_completed(future_to_company):
                company = future_to_company[future]
                completed += 1
                
                try:
                    result = future.result(timeout=60)  # 1 minute timeout per company
                    
                    # Progress update
                    elapsed = time.time() - start_time
                    rate = completed / elapsed
                    eta = (len(companies) - completed) / rate if rate > 0 else 0
                    
                    status = "✓" if result['status'] == 'success' else "✗"
                    print(f"[{completed}/{len(companies)}] {status} {result['ticker']}: "
                          f"{result['documents_downloaded']} docs | "
                          f"ETA: {eta:.0f}s")
                    
                except Exception as e:
                    print(f"[{completed}/{len(companies)}] ✗ {company['ticker']}: Timeout/Error")
        
        # Final summary
        elapsed_time = time.time() - start_time
        
        print(f"\n{'='*60}")
        print(f"DOWNLOAD COMPLETE")
        print(f"{'='*60}")
        print(f"Time: {elapsed_time:.1f} seconds ({elapsed_time/60:.1f} minutes)")
        print(f"Companies: {self.stats['companies_processed']}")
        print(f"Documents: {self.stats['total_documents']}")
        print(f"Failed: {len(self.stats['failed_companies'])}")
        
        if self.stats['failed_companies']:
            print(f"Failed companies: {', '.join(self.stats['failed_companies'])}")
        
        print(f"Average: {elapsed_time/len(companies):.1f}s per company")
        print(f"{'='*60}")
        
        # Save summary
        self.save_summary()
    
    def save_summary(self):
        """Save download summary"""
        summary = {
            'timestamp': datetime.now().isoformat(),
            'stats': self.stats,
            'duration_seconds': (datetime.now() - self.stats['start_time']).total_seconds()
        }
        
        summary_file = self.output_dir / "parallel_download_summary.json"
        with open(summary_file, 'w') as f:
            json.dump(summary, f, indent=2)
        
        print(f"\nSummary saved to: {summary_file}")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Parallel document downloader')
    parser.add_argument('--workers', type=int, default=5, help='Number of parallel workers')
    parser.add_argument('--input', default='output/ir_finder_results.json', help='IR results file')
    parser.add_argument('--output', default='downloads', help='Output directory')
    parser.add_argument('--headless', action='store_true', help='Run browsers in headless mode')
    
    args = parser.parse_args()
    
    downloader = ParallelDocumentDownloader(
        input_file=args.input,
        output_dir=args.output,
        max_workers=args.workers,
        headless=args.headless
    )
    
    downloader.run()


if __name__ == "__main__":
    main()