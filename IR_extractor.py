#!/usr/bin/env python3
"""
Multi-Strategy Investor Relations Page Finder for Dow 30
Updated with latest Dow 30 companies as of November 2024
"""

import os
import re
import json
import time
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, parse_qs
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import (
    TimeoutException, 
    NoSuchElementException,
    StaleElementReferenceException,
    WebDriverException,
    ElementNotInteractableException,
    ElementClickInterceptedException
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class MultiStrategyIRFinder:
    """Multi-strategy IR page finder without hardcoding"""
    
    # IR Keywords with priority levels
    IR_KEYWORDS = {
        'high': ['investor relations', 'investor relation', 'investors', 'investor'],
        'medium': ['ir', 'shareholder', 'shareholders', 'stockholder', 'stockholders'],
        'low': ['financial information', 'investor resources', 'investor center', 
                 'investor hub', 'financials', 'earnings', 'investor info',
                 'financial results', 'quarterly results', 'annual report']
    }
    
    # Common IR URL patterns
    IR_URL_PATTERNS = [
        r'/investor',
        r'/investors',
        r'/ir\b',
        r'/shareholder',
        r'/stockholder',
        r'/financial',
        r'/investor-relations',
        r'/investor_relations',
        r'investor\.',
        r'investors\.',
        r'ir\.'
    ]
    
    # IR page verification terms
    IR_VERIFICATION_TERMS = [
        'investor relations', 'quarterly results', 'annual report',
        'earnings', 'financial results', 'sec filing', 'form 10-k',
        'form 10-q', 'shareholder', 'stock information', 'dividend',
        'investor presentation', 'proxy statement', 'investor contact'
    ]
    
    def __init__(self, headless: bool = True, output_dir: str = "output"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.driver = None
        self.headless = headless
        self.session_data = {
            'start_time': datetime.now().isoformat(),
            'companies_processed': [],
            'success_count': 0,
            'failure_count': 0,
            'dow30_updated': 'November 8, 2024'
        }
        
    def setup_driver(self):
        """Setup Chrome driver with optimized options"""
        chrome_options = Options()
        
        if self.headless:
            chrome_options.add_argument("--headless=new")
        
        # Performance and stability options
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        # Enable JavaScript
        prefs = {
            "profile.default_content_setting_values.notifications": 2,
            "javascript.enabled": True
        }
        chrome_options.add_experimental_option("prefs", prefs)
        
        # User agent
        chrome_options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        
        try:
            self.driver = webdriver.Chrome(options=chrome_options)
            self.driver.set_page_load_timeout(30)
            self.driver.implicitly_wait(3)
            logger.info("Chrome driver initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Chrome driver: {e}")
            raise
    
    def find_ir_page_multi_strategy(self, ticker: str, name: str, url: str) -> Dict:
        """Use multiple strategies to find IR page"""
        result = {
            'ticker': ticker,
            'company_name': name,
            'company_url': url,
            'ir_page_found': False,
            'ir_url': None,
            'ir_page_title': None,
            'strategy_used': None,
            'verification_score': 0,
            'timestamp': datetime.now().isoformat(),
            'error': None
        }
        
        try:
            # Strategy 1: Try common IR subdomain patterns
            ir_url = self._try_common_subdomains(url)
            if ir_url:
                result['ir_url'] = ir_url
                result['ir_page_found'] = True
                result['strategy_used'] = 'subdomain_pattern'
                result['ir_page_title'] = self.driver.title
                result['verification_score'] = self._calculate_ir_score()
                return result
            
            # Strategy 2: Search for IR links on main page
            self.driver.get(url)
            time.sleep(3)
            
            # Look for IR links in different page areas
            ir_link_data = self._find_ir_link_comprehensive()
            if ir_link_data:
                # Try to navigate to the IR page
                if self._navigate_to_ir_page(ir_link_data):
                    result['ir_url'] = self.driver.current_url
                    result['ir_page_found'] = True
                    result['strategy_used'] = 'link_navigation'
                    result['ir_page_title'] = self.driver.title
                    result['verification_score'] = self._calculate_ir_score()
                    return result
            
            # Strategy 3: Check common IR URL paths
            ir_url = self._try_common_paths(url)
            if ir_url:
                result['ir_url'] = ir_url
                result['ir_page_found'] = True
                result['strategy_used'] = 'common_path'
                result['ir_page_title'] = self.driver.title
                result['verification_score'] = self._calculate_ir_score()
                return result
            
            # Strategy 4: Search page source for hidden IR links
            ir_url = self._search_page_source_for_ir(url)
            if ir_url:
                result['ir_url'] = ir_url
                result['ir_page_found'] = True
                result['strategy_used'] = 'page_source_search'
                result['ir_page_title'] = self.driver.title
                result['verification_score'] = self._calculate_ir_score()
                return result
                
        except Exception as e:
            logger.error(f"Error finding IR page for {ticker}: {e}")
            result['error'] = str(e)
        
        return result
    
    def _try_common_subdomains(self, base_url: str) -> Optional[str]:
        """Try common IR subdomain patterns"""
        parsed = urlparse(base_url)
        domain = parsed.netloc.replace('www.', '')
        
        # Common IR subdomain patterns
        subdomain_patterns = [
            f'https://investor.{domain}',
            f'https://investors.{domain}',
            f'https://ir.{domain}',
            f'https://{domain}/investor',
            f'https://{domain}/investors',
            f'https://{domain}/investor-relations',
            f'https://{domain}/ir'
        ]
        
        for pattern in subdomain_patterns:
            try:
                self.driver.get(pattern)
                time.sleep(2)
                
                # Check if it's an IR page
                if self._verify_ir_page():
                    logger.info(f"Found IR page via subdomain: {pattern}")
                    return self.driver.current_url
            except:
                continue
        
        return None
    
    def _find_ir_link_comprehensive(self) -> Optional[Dict]:
        """Comprehensive search for IR links on the page"""
        strategies = [
            self._find_ir_link_in_footer,
            self._find_ir_link_in_header,
            self._find_ir_link_in_navigation,
            self._find_ir_link_anywhere
        ]
        
        for strategy in strategies:
            link_data = strategy()
            if link_data:
                return link_data
        
        return None
    
    def _find_ir_link_in_footer(self) -> Optional[Dict]:
        """Search for IR links in the footer"""
        try:
            # Common footer selectors
            footer_selectors = [
                "footer",
                "[role='contentinfo']",
                ".footer",
                "#footer",
                "[class*='footer']",
                "[id*='footer']"
            ]
            
            for selector in footer_selectors:
                try:
                    footer = self.driver.find_element(By.CSS_SELECTOR, selector)
                    links = footer.find_elements(By.TAG_NAME, "a")
                    
                    for link in links:
                        link_data = self._analyze_link_for_ir(link)
                        if link_data:
                            logger.info(f"Found IR link in footer: {link_data['text']}")
                            return link_data
                except:
                    continue
        except:
            pass
        
        return None
    
    def _find_ir_link_in_header(self) -> Optional[Dict]:
        """Search for IR links in the header"""
        try:
            # Common header selectors
            header_selectors = [
                "header",
                "[role='banner']",
                ".header",
                "#header",
                "nav",
                ".navbar",
                "[class*='header']",
                "[id*='header']"
            ]
            
            for selector in header_selectors:
                try:
                    header = self.driver.find_element(By.CSS_SELECTOR, selector)
                    links = header.find_elements(By.TAG_NAME, "a")
                    
                    for link in links:
                        link_data = self._analyze_link_for_ir(link)
                        if link_data:
                            logger.info(f"Found IR link in header: {link_data['text']}")
                            return link_data
                except:
                    continue
        except:
            pass
        
        return None
    
    def _find_ir_link_in_navigation(self) -> Optional[Dict]:
        """Search for IR links in navigation menus"""
        try:
            # Look for navigation elements
            nav_selectors = [
                "nav",
                "[role='navigation']",
                ".navigation",
                ".nav",
                ".menu",
                "[class*='menu']",
                "[class*='nav']"
            ]
            
            for selector in nav_selectors:
                try:
                    nav = self.driver.find_element(By.CSS_SELECTOR, selector)
                    links = nav.find_elements(By.TAG_NAME, "a")
                    
                    for link in links:
                        link_data = self._analyze_link_for_ir(link)
                        if link_data:
                            logger.info(f"Found IR link in navigation: {link_data['text']}")
                            return link_data
                except:
                    continue
        except:
            pass
        
        return None
    
    def _find_ir_link_anywhere(self) -> Optional[Dict]:
        """Search for IR links anywhere on the page"""
        try:
            # Get all links
            all_links = self.driver.find_elements(By.TAG_NAME, "a")
            
            # Score and sort links
            scored_links = []
            for link in all_links:
                link_data = self._analyze_link_for_ir(link)
                if link_data:
                    scored_links.append(link_data)
            
            # Sort by score
            scored_links.sort(key=lambda x: x.get('score', 0), reverse=True)
            
            if scored_links:
                logger.info(f"Found {len(scored_links)} potential IR links")
                return scored_links[0]  # Return highest scoring link
        except:
            pass
        
        return None
    
    def _analyze_link_for_ir(self, link) -> Optional[Dict]:
        """Analyze a link element for IR relevance"""
        try:
            # Get link properties
            href = link.get_attribute('href')
            text = link.text.strip()
            title = link.get_attribute('title') or ''
            aria_label = link.get_attribute('aria-label') or ''
            
            # Also check for text in child elements if main text is empty
            if not text:
                try:
                    text = link.find_element(By.XPATH, ".//*").text.strip()
                except:
                    pass
            
            if not href:
                return None
            
            # Calculate score
            score = 0
            combined_text = f"{text} {title} {aria_label} {href}".lower()
            
            # Check keywords
            for priority, keywords in self.IR_KEYWORDS.items():
                for keyword in keywords:
                    if keyword in combined_text:
                        if priority == 'high':
                            score += 10
                        elif priority == 'medium':
                            score += 5
                        else:
                            score += 2
            
            # Check URL patterns
            for pattern in self.IR_URL_PATTERNS:
                if re.search(pattern, href.lower()):
                    score += 15
            
            if score > 0:
                return {
                    'element': link,
                    'href': href,
                    'text': text or title or aria_label or 'IR Link',
                    'score': score
                }
        except:
            pass
        
        return None
    
    def _navigate_to_ir_page(self, link_data: Dict) -> bool:
        """Navigate to IR page using various methods"""
        href = link_data['href']
        
        # Method 1: Direct navigation
        try:
            self.driver.get(href)
            time.sleep(3)
            if self._verify_ir_page():
                return True
        except:
            pass
        
        # Method 2: JavaScript navigation
        try:
            self.driver.execute_script(f"window.location.href = '{href}'")
            time.sleep(3)
            if self._verify_ir_page():
                return True
        except:
            pass
        
        # Method 3: Click the element
        try:
            element = link_data.get('element')
            if element:
                self.driver.execute_script("arguments[0].scrollIntoView(true);", element)
                time.sleep(1)
                element.click()
                time.sleep(3)
                if self._verify_ir_page():
                    return True
        except:
            pass
        
        return False
    
    def _try_common_paths(self, base_url: str) -> Optional[str]:
        """Try common IR URL paths"""
        common_paths = [
            '/investors',
            '/investor',
            '/investor-relations',
            '/investor_relations',
            '/ir',
            '/shareholders',
            '/shareholder',
            '/financial-information',
            '/financials'
        ]
        
        base = base_url.rstrip('/')
        
        for path in common_paths:
            try:
                test_url = base + path
                self.driver.get(test_url)
                time.sleep(2)
                
                if self._verify_ir_page():
                    logger.info(f"Found IR page via common path: {test_url}")
                    return self.driver.current_url
            except:
                continue
        
        return None
    
    def _search_page_source_for_ir(self, url: str) -> Optional[str]:
        """Search page source for IR links"""
        try:
            self.driver.get(url)
            time.sleep(2)
            page_source = self.driver.page_source.lower()
            
            # Look for IR URLs in the page source
            ir_url_patterns = [
                r'href=["\']([^"\']*investor[^"\']*)["\']',
                r'href=["\']([^"\']*ir[^"\']*)["\']',
                r'href=["\']([^"\']*shareholder[^"\']*)["\']'
            ]
            
            for pattern in ir_url_patterns:
                matches = re.findall(pattern, page_source)
                for match in matches:
                    # Filter out non-IR links
                    if any(term in match for term in ['career', 'job', 'blog', 'news', 'press']):
                        continue
                    
                    # Construct full URL
                    if match.startswith('http'):
                        test_url = match
                    elif match.startswith('/'):
                        test_url = urljoin(url, match)
                    else:
                        continue
                    
                    # Test the URL
                    try:
                        self.driver.get(test_url)
                        time.sleep(2)
                        if self._verify_ir_page():
                            logger.info(f"Found IR page via source search: {test_url}")
                            return self.driver.current_url
                    except:
                        continue
        except:
            pass
        
        return None
    
    def _verify_ir_page(self) -> bool:
        """Verify if current page is an IR page"""
        try:
            # Check URL
            current_url = self.driver.current_url.lower()
            url_score = sum(1 for pattern in self.IR_URL_PATTERNS 
                          if re.search(pattern, current_url))
            
            # Check title
            title = self.driver.title.lower()
            title_score = sum(1 for term in ['investor', 'shareholder', 'financial', 'ir ']
                            if term in title)
            
            # Check page content
            try:
                # Use JavaScript to get text content
                body_text = self.driver.execute_script(
                    "return document.body.innerText || document.body.textContent || '';"
                ).lower()[:5000]
            except:
                body_text = ""
            
            # Count IR indicators
            content_score = sum(1 for term in self.IR_VERIFICATION_TERMS 
                              if term in body_text)
            
            # Verification logic
            total_score = url_score * 2 + title_score * 2 + content_score
            
            return total_score >= 3
            
        except Exception as e:
            logger.debug(f"Error verifying IR page: {e}")
            return False
    
    def _calculate_ir_score(self) -> int:
        """Calculate confidence score for IR page"""
        score = 0
        
        try:
            url = self.driver.current_url.lower()
            title = self.driver.title.lower()
            
            # URL scoring
            if 'investor' in url: score += 20
            if 'ir' in url: score += 15
            if 'shareholder' in url: score += 10
            
            # Title scoring
            if 'investor' in title: score += 15
            if 'financial' in title: score += 10
            
            # Content scoring
            try:
                body_text = self.driver.execute_script(
                    "return document.body.innerText || document.body.textContent || '';"
                ).lower()[:5000]
                
                important_terms = {
                    'investor relations': 20,
                    'annual report': 15,
                    'quarterly results': 15,
                    'earnings': 10,
                    '10-k': 10,
                    '10-q': 10,
                    'sec filing': 10,
                    'financial results': 10
                }
                
                for term, points in important_terms.items():
                    if term in body_text:
                        score += points
            except:
                pass
            
            return score
            
        except:
            return 0
    
    def process_company(self, ticker: str, name: str, url: str) -> Dict:
        """Process a single company"""
        logger.info(f"Processing {ticker} - {name}")
        
        result = self.find_ir_page_multi_strategy(ticker, name, url)
        
        # Update session data
        if result['ir_page_found']:
            logger.info(f"✓ {ticker}: Successfully found IR page using {result['strategy_used']}")
            self.session_data['success_count'] += 1
        else:
            logger.warning(f"✗ {ticker}: Could not find IR page")
            self.session_data['failure_count'] += 1
        
        self.session_data['companies_processed'].append(result)
        
        # Save intermediate results
        self._save_results()
        
        return result
    
    def _save_results(self):
        """Save results to JSON file"""
        output_file = self.output_dir / "ir_finder_results.json"
        
        try:
            with open(output_file, 'w') as f:
                json.dump(self.session_data, f, indent=2)
            logger.debug(f"Results saved to {output_file}")
        except Exception as e:
            logger.error(f"Error saving results: {e}")
    
    def close(self):
        """Close driver and finalize results"""
        if self.driver:
            self.driver.quit()
            logger.info("Driver closed")
        
        # Final save
        self.session_data['end_time'] = datetime.now().isoformat()
        self._save_results()
        
        # Create summary report
        self._create_summary_report()
    
    def _create_summary_report(self):
        """Create summary report"""
        summary_file = self.output_dir / "ir_finder_summary.txt"
        
        try:
            with open(summary_file, 'w') as f:
                f.write("="*60 + "\n")
                f.write("INVESTOR RELATIONS PAGE FINDER - SUMMARY REPORT\n")
                f.write("="*60 + "\n\n")
                
                f.write(f"Session Start: {self.session_data['start_time']}\n")
                f.write(f"Session End: {self.session_data.get('end_time', 'N/A')}\n")
                f.write(f"Dow 30 List Updated: {self.session_data.get('dow30_updated', 'N/A')}\n\n")
                
                f.write(f"Total Companies: {len(self.session_data['companies_processed'])}\n")
                f.write(f"Successful: {self.session_data['success_count']}\n")
                f.write(f"Failed: {self.session_data['failure_count']}\n")
                f.write(f"Success Rate: {self.session_data['success_count']}/{len(self.session_data['companies_processed'])} ")
                f.write(f"({100*self.session_data['success_count']/max(1,len(self.session_data['companies_processed'])):.1f}%)\n\n")
                
                # Strategy breakdown
                strategies = {}
                for company in self.session_data['companies_processed']:
                    if company['ir_page_found']:
                        strategy = company.get('strategy_used', 'unknown')
                        strategies[strategy] = strategies.get(strategy, 0) + 1
                
                if strategies:
                    f.write("STRATEGIES USED:\n")
                    f.write("-"*40 + "\n")
                    for strategy, count in strategies.items():
                        f.write(f"{strategy}: {count}\n")
                    f.write("\n")
                
                f.write("SUCCESSFUL COMPANIES:\n")
                f.write("-"*40 + "\n")
                for company in self.session_data['companies_processed']:
                    if company['ir_page_found']:
                        f.write(f"{company['ticker']}: {company['ir_url']}\n")
                        f.write(f"  Strategy: {company.get('strategy_used', 'unknown')}\n")
                        f.write(f"  Score: {company.get('verification_score', 0)}\n")
                
                f.write("\nFAILED COMPANIES:\n")
                f.write("-"*40 + "\n")
                for company in self.session_data['companies_processed']:
                    if not company['ir_page_found']:
                        f.write(f"{company['ticker']}: {company.get('error', 'No IR page found')}\n")
                
            logger.info(f"Summary report saved to {summary_file}")
            
        except Exception as e:
            logger.error(f"Error creating summary report: {e}")


def main():
    """Main execution"""
    
    # Updated Dow 30 companies as of November 8, 2024
    DOW30_COMPANIES = [
        # Companies sorted alphabetically by ticker
        ("AAPL", "Apple Inc.", "https://www.apple.com"),
        ("AMGN", "Amgen Inc.", "https://www.amgen.com"),
        ("AMZN", "Amazon.com Inc.", "https://www.amazon.com"),  # Added Feb 26, 2024
        ("AXP", "American Express", "https://www.americanexpress.com"),
        ("BA", "Boeing", "https://www.boeing.com"),
        ("CAT", "Caterpillar", "https://www.caterpillar.com"),
        ("CRM", "Salesforce", "https://www.salesforce.com"),
        ("CSCO", "Cisco", "https://www.cisco.com"),
        ("CVX", "Chevron", "https://www.chevron.com"),
        ("DIS", "The Walt Disney Company", "https://thewaltdisneycompany.com"),
        ("GS", "Goldman Sachs", "https://www.goldmansachs.com"),
        ("HD", "The Home Depot", "https://www.homedepot.com"),
        ("HON", "Honeywell", "https://www.honeywell.com"),
        ("IBM", "IBM", "https://www.ibm.com"),
        ("JNJ", "Johnson & Johnson", "https://www.jnj.com"),
        ("JPM", "JPMorgan Chase", "https://www.jpmorganchase.com"),
        ("KO", "Coca-Cola", "https://www.coca-colacompany.com"),
        ("MCD", "McDonald's", "https://corporate.mcdonalds.com"),
        ("MMM", "3M", "https://www.3m.com"),
        ("MRK", "Merck", "https://www.merck.com"),
        ("MSFT", "Microsoft", "https://www.microsoft.com"),
        ("NKE", "Nike", "https://www.nike.com"),
        ("NVDA", "NVIDIA", "https://www.nvidia.com"),  # Added Nov 8, 2024
        ("PG", "Procter & Gamble", "https://us.pg.com"),
        ("SHW", "The Sherwin-Williams Company", "https://www.sherwin-williams.com"),  # Added Nov 8, 2024
        ("TRV", "Travelers", "https://www.travelers.com"),
        ("UNH", "UnitedHealth Group", "https://www.unitedhealthgroup.com"),
        ("V", "Visa", "https://www.visa.com"),
        ("VZ", "Verizon", "https://www.verizon.com"),
        ("WMT", "Walmart", "https://corporate.walmart.com")
    ]
    
    # Initialize finder
    finder = MultiStrategyIRFinder(headless=True)
    
    try:
        # Setup driver
        finder.setup_driver()
        
        print("="*60)
        print("MULTI-STRATEGY INVESTOR RELATIONS PAGE DISCOVERY")
        print("Latest Dow 30 Companies (Updated November 8, 2024)")
        print("="*60)
        
        # Display recent changes
        print("\n📌 Recent Dow 30 Changes:")
        print("  • NVDA (NVIDIA) - Added November 8, 2024")
        print("  • SHW (Sherwin-Williams) - Added November 8, 2024")
        print("  • AMZN (Amazon) - Added February 26, 2024")
        print("  • Removed: INTC (Intel), DOW (Dow Inc.)")
        print("")
        
        # Process each company
        for i, (ticker, name, url) in enumerate(DOW30_COMPANIES, 1):
            print(f"\n[{i}/{len(DOW30_COMPANIES)}] Processing {ticker} - {name}")
            print("-"*40)
            
            result = finder.process_company(ticker, name, url)
            
            if result['ir_page_found']:
                print(f"✓ SUCCESS: Found IR page")
                print(f"  URL: {result['ir_url']}")
                print(f"  Strategy: {result['strategy_used']}")
                print(f"  Score: {result['verification_score']}")
            else:
                print(f"✗ FAILED: Could not find IR page")
            
            # Rate limiting
            time.sleep(2)
        
        print("\n" + "="*60)
        print("PROCESSING COMPLETE")
        print(f"Success Rate: {finder.session_data['success_count']}/{len(DOW30_COMPANIES)}")
        print(f"Results saved to: {finder.output_dir}")
        print("="*60)
        
    except KeyboardInterrupt:
        print("\n\nProcess interrupted by user")
    except Exception as e:
        print(f"\n\nFatal error: {e}")
    finally:
        finder.close()


if __name__ == "__main__":
    main()