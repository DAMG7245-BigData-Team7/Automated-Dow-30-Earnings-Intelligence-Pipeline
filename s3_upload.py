#!/usr/bin/env python3
"""
Unified S3 Uploader - Raw Downloads + Parsed Documents
------------------------------------------------------
Uploads both raw PDFs and parsed documents to S3 with RDS metadata logging

S3 Structure:
  raw-documents/           <- Raw PDFs from downloads
  └── TICKER/
      └── *.pdf
  
  earnings-documents/      <- Parsed content
  └── TICKER/
      ├── images/
      ├── text/
      ├── json/
      ├── markdown/
      └── tables/

Usage:
    # Upload both raw and parsed
    python s3_upload_unified.py --bucket doc-dow-30-2025
    
    # Upload only raw PDFs
    python s3_upload_unified.py --bucket doc-dow-30-2025 --mode raw
    
    # Upload only parsed
    python s3_upload_unified.py --bucket doc-dow-30-2025 --mode parsed
    
    # Specific ticker
    python s3_upload_unified.py --bucket doc-dow-30-2025 --ticker AXP
"""

import os
import json
import logging
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
import mimetypes
import argparse

import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from tqdm import tqdm

# Database imports
try:
    import psycopg2
    from dotenv import load_dotenv
    DB_AVAILABLE = True
    load_dotenv()
except ImportError:
    DB_AVAILABLE = False
    print("⚠️  Database libraries not installed. Install with: pip install psycopg2-binary python-dotenv")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DatabaseLogger:
    """PostgreSQL logger for document metadata"""
    
    def __init__(self):
        """Initialize database connection from .env file"""
        if not DB_AVAILABLE:
            self.connection = None
            logger.warning("Database logging disabled")
            return
        
        self.host = os.getenv('DB_HOST')
        self.port = os.getenv('DB_PORT', '5432')
        self.database = os.getenv('DB_NAME', 'earnings_db')
        self.user = os.getenv('DB_USER', 'postgres')
        self.password = os.getenv('DB_PASSWORD')
        
        self.connection = None
        
        if not all([self.host, self.password]):
            logger.warning("Database credentials not in .env. Skipping DB logging.")
            return
        
        try:
            self.connection = psycopg2.connect(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
                connect_timeout=10
            )
            logger.info("✓ Database connection established")
        except Exception as e:
            logger.warning(f"Database connection failed: {e}")
            self.connection = None
    
    def log_document(self, metadata: Dict) -> bool:
        """Log document metadata to database"""
        if not self.connection:
            return False
        
        try:
            cursor = self.connection.cursor()
            
            insert_query = """
                INSERT INTO documents_metadata (
                    file_id, company_name, filename, file_path,
                    file_extension, file_size_bytes, file_size_mb,
                    document_type, document_subtype, source_url,
                    source_domain, download_timestamp, s3_bucket
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (file_id) DO UPDATE SET
                    download_timestamp = EXCLUDED.download_timestamp;
            """
            
            cursor.execute(insert_query, (
                metadata['file_id'],
                metadata['company_name'],
                metadata['filename'],
                metadata['file_path'],
                metadata['file_extension'],
                metadata['file_size_bytes'],
                metadata['file_size_mb'],
                metadata.get('document_type'),
                metadata.get('document_subtype'),
                metadata.get('source_url'),
                metadata.get('source_domain'),
                metadata['download_timestamp'],
                metadata['s3_bucket']
            ))
            
            self.connection.commit()
            cursor.close()
            return True
            
        except Exception as e:
            logger.debug(f"Database insert failed: {e}")
            if self.connection:
                self.connection.rollback()
            return False
    
    def close(self):
        """Close database connection"""
        if self.connection:
            self.connection.close()
            logger.info("Database connection closed")


class UnifiedS3Uploader:
    """Upload both raw downloads and parsed documents to S3"""
    
    def __init__(self, 
                 bucket_name: str,
                 downloads_root: str = "data/raw",
                 parsed_root: str = "data/parsed",
                 dry_run: bool = False,
                 enable_db: bool = True):
        """Initialize unified uploader"""
        
        self.bucket_name = bucket_name
        self.downloads_root = Path(downloads_root)
        self.parsed_root = Path(parsed_root)
        self.dry_run = dry_run
        
        # Initialize S3
        try:
            self.s3_client = boto3.client('s3')
            logger.info("✓ AWS S3 client initialized")
        except NoCredentialsError:
            logger.error("AWS credentials not found")
            raise
        
        # Initialize database
        self.db = None
        if enable_db and not dry_run:
            self.db = DatabaseLogger()
        
        # Stats
        self.stats = {
            'raw_files': 0,
            'parsed_files': 0,
            'total_files': 0,
            'bytes_uploaded': 0,
            'db_records': 0,
            'failed': 0
        }
        
        # MIME types
        self.mime_types = {
            '.pdf': 'application/pdf',
            '.csv': 'text/csv',
            '.json': 'application/json',
            '.txt': 'text/plain',
            '.md': 'text/markdown',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jsonl': 'application/jsonl'
        }
    
    def generate_file_id(self, s3_key: str) -> str:
        """Generate unique file ID"""
        return hashlib.sha256(s3_key.encode()).hexdigest()[:64]
    
    def get_content_type(self, file_path: Path) -> str:
        """Get content type"""
        ext = file_path.suffix.lower()
        if ext in self.mime_types:
            return self.mime_types[ext]
        content_type, _ = mimetypes.guess_type(str(file_path))
        return content_type or 'application/octet-stream'
    
    def determine_document_type(self, file_path: Path, is_raw: bool = False) -> str:
        """Determine document type"""
        if is_raw:
            return 'raw_pdf'
        
        path_str = str(file_path).lower()
        if '/markdown/' in path_str:
            return 'markdown'
        elif '/tables/' in path_str:
            return 'table'
        elif '/images/' in path_str:
            return 'image'
        elif '/text/' in path_str:
            return 'text'
        elif '/json/' in path_str:
            return 'structured_data'
        else:
            return 'other'
    
    def upload_file_with_metadata(self, local_path: Path, s3_key: str, 
                                   ticker: str, is_raw: bool = False) -> bool:
        """Upload file to S3 and log metadata to RDS"""
        try:
            if self.dry_run:
                return True
            
            file_size = local_path.stat().st_size
            content_type = self.get_content_type(local_path)
            
            # Upload to S3
            self.s3_client.upload_file(
                str(local_path),
                self.bucket_name,
                s3_key,
                ExtraArgs={'ContentType': content_type}
            )
            
            # Update stats
            if is_raw:
                self.stats['raw_files'] += 1
            else:
                self.stats['parsed_files'] += 1
            self.stats['total_files'] += 1
            self.stats['bytes_uploaded'] += file_size
            
            # Log to database
            if self.db and self.db.connection:
                metadata = {
                    'file_id': self.generate_file_id(s3_key),
                    'company_name': ticker,
                    'filename': local_path.name,
                    'file_path': s3_key,
                    'file_extension': local_path.suffix,
                    'file_size_bytes': file_size,
                    'file_size_mb': round(file_size / 1024 / 1024, 2),
                    'document_type': self.determine_document_type(local_path, is_raw),
                    'document_subtype': 'raw' if is_raw else 'parsed',
                    'source_url': None,
                    'source_domain': None,
                    'download_timestamp': datetime.now(),
                    's3_bucket': self.bucket_name
                }
                
                if self.db.log_document(metadata):
                    self.stats['db_records'] += 1
            
            return True
            
        except Exception as e:
            logger.error(f"✗ Upload failed: {e}")
            self.stats['failed'] += 1
            return False
    
    def upload_raw_pdfs(self, ticker: str, ticker_dir: Path) -> Dict:
        """Upload raw PDFs from downloads folder"""
        result = {'ticker': ticker, 'raw_files': 0, 'bytes': 0}
        
        # Find all PDFs
        pdf_files = list(ticker_dir.glob("*.pdf"))
        if not pdf_files:
            pdf_files = list(ticker_dir.rglob("*.pdf"))
        
        if not pdf_files:
            logger.warning(f"No PDFs found for {ticker} in downloads")
            return result
        
        logger.info(f"  Uploading {len(pdf_files)} raw PDFs...")
        
        for pdf_file in pdf_files:
            # S3 key: raw-documents/TICKER/filename.pdf
            s3_key = f"raw-documents/{ticker}/{pdf_file.name}"
            
            if self.upload_file_with_metadata(pdf_file, s3_key, ticker, is_raw=True):
                result['raw_files'] += 1
                result['bytes'] += pdf_file.stat().st_size
        
        return result
    
    def upload_parsed_files(self, ticker: str, ticker_dir: Path) -> Dict:
        """Upload parsed files from parsed folder"""
        result = {'ticker': ticker, 'parsed_files': 0, 'bytes': 0}
        
        # Find all files recursively
        all_files = [f for f in ticker_dir.rglob('*') if f.is_file()]
        
        if not all_files:
            logger.warning(f"No parsed files found for {ticker}")
            return result
        
        logger.info(f"  Uploading {len(all_files)} parsed files...")
        
        with tqdm(all_files, desc=f"  {ticker} parsed", leave=False) as pbar:
            for file_path in pbar:
                # Get relative path from parsed_root
                try:
                    relative_path = file_path.relative_to(self.parsed_root)
                    
                    # Remove ticker from path if first
                    parts = relative_path.parts
                    if parts[0] == ticker:
                        parts = parts[1:]
                    
                    # S3 key: earnings-documents/TICKER/folder/file
                    s3_key = f"earnings-documents/{ticker}/{'/'.join(parts)}"
                    
                    if self.upload_file_with_metadata(file_path, s3_key, ticker, is_raw=False):
                        result['parsed_files'] += 1
                        result['bytes'] += file_path.stat().st_size
                        
                except Exception as e:
                    logger.debug(f"Error uploading {file_path}: {e}")
                    continue
        
        return result
    
    def upload_company_complete(self, ticker: str, mode: str = 'both') -> Dict:
        """Upload both raw and parsed for a company"""
        
        logger.info(f"\n{'='*70}")
        logger.info(f"Processing {ticker}")
        logger.info(f"{'='*70}")
        
        result = {
            'ticker': ticker,
            'raw_files': 0,
            'parsed_files': 0,
            'total_files': 0,
            'bytes': 0,
            'status': 'success'
        }
        
        # Upload raw PDFs
        if mode in ['raw', 'both']:
            downloads_dir = self.downloads_root / ticker
            if downloads_dir.exists():
                raw_result = self.upload_raw_pdfs(ticker, downloads_dir)
                result['raw_files'] = raw_result['raw_files']
                result['bytes'] += raw_result['bytes']
                logger.info(f"  ✓ Raw PDFs: {raw_result['raw_files']} files")
            else:
                logger.warning(f"  Downloads folder not found: {downloads_dir}")
        
        # Upload parsed files
        if mode in ['parsed', 'both']:
            parsed_dir = self.parsed_root / ticker
            if parsed_dir.exists():
                parsed_result = self.upload_parsed_files(ticker, parsed_dir)
                result['parsed_files'] = parsed_result['parsed_files']
                result['bytes'] += parsed_result['bytes']
                logger.info(f"  ✓ Parsed files: {parsed_result['parsed_files']} files")
            else:
                logger.warning(f"  Parsed folder not found: {parsed_dir}")
        
        result['total_files'] = result['raw_files'] + result['parsed_files']
        
        logger.info(f"  ✓ Total: {result['total_files']} files ({result['bytes']/1024/1024:.1f} MB)")
        
        return result
    
    def upload_all_companies(self, specific_ticker: Optional[str] = None, 
                            mode: str = 'both') -> List[Dict]:
        """Upload all companies"""
        
        # Find all tickers from both directories
        tickers = set()
        
        if self.downloads_root.exists():
            tickers.update(d.name for d in self.downloads_root.iterdir() if d.is_dir())
        
        if self.parsed_root.exists():
            tickers.update(d.name for d in self.parsed_root.iterdir() if d.is_dir())
        
        if specific_ticker:
            tickers = {specific_ticker} if specific_ticker in tickers else set()
        
        if not tickers:
            logger.error("No companies found")
            return []
        
        db_status = "Enabled" if (self.db and self.db.connection) else "Disabled"
        
        logger.info(f"\n{'='*70}")
        logger.info(f"UNIFIED S3 UPLOAD - RAW + PARSED")
        logger.info(f"{'='*70}")
        logger.info(f"Bucket: {self.bucket_name}")
        logger.info(f"Mode: {mode.upper()}")
        logger.info(f"Downloads: {self.downloads_root}")
        logger.info(f"Parsed: {self.parsed_root}")
        logger.info(f"Companies: {len(tickers)}")
        logger.info(f"Database: {db_status}")
        logger.info(f"{'='*70}\n")
        
        start_time = datetime.now()
        results = []
        
        # Upload each company
        for idx, ticker in enumerate(sorted(tickers), 1):
            logger.info(f"[{idx}/{len(tickers)}] {ticker}")
            result = self.upload_company_complete(ticker, mode)
            results.append(result)
        
        duration = (datetime.now() - start_time).total_seconds()
        
        # Summary
        logger.info(f"\n{'='*70}")
        logger.info(f"UPLOAD COMPLETE")
        logger.info(f"{'='*70}")
        logger.info(f"Duration: {duration:.1f}s ({duration/60:.1f} min)")
        logger.info(f"Companies: {len(results)}")
        logger.info(f"Raw PDFs: {self.stats['raw_files']}")
        logger.info(f"Parsed files: {self.stats['parsed_files']}")
        logger.info(f"Total files: {self.stats['total_files']}")
        logger.info(f"Data uploaded: {self.stats['bytes_uploaded']/1024/1024:.1f} MB")
        logger.info(f"DB records: {self.stats['db_records']}")
        logger.info(f"Failed: {self.stats['failed']}")
        logger.info(f"{'='*70}\n")
        
        return results
    
    def save_summary(self, results: List[Dict]):
        """Save upload summary"""
        summary = {
            'timestamp': datetime.now().isoformat(),
            'bucket': self.bucket_name,
            'statistics': self.stats,
            'companies': results
        }
        
        summary_path = Path('upload_summary_unified.json')
        with open(summary_path, 'w') as f:
            json.dump(summary, f, indent=2, default=str)
        
        logger.info(f"Summary saved: {summary_path}")
    
    def verify_bucket(self) -> bool:
        """Verify bucket exists"""
        try:
            self.s3_client.head_bucket(Bucket=self.bucket_name)
            logger.info(f"✓ Bucket '{self.bucket_name}' verified")
            return True
        except ClientError:
            logger.error(f"✗ Bucket '{self.bucket_name}' not accessible")
            return False
    
    def close(self):
        """Cleanup"""
        if self.db:
            self.db.close()


def main():
    parser = argparse.ArgumentParser(
        description='Upload raw PDFs and parsed documents to S3'
    )
    parser.add_argument('--bucket', required=True, help='S3 bucket name')
    parser.add_argument('--downloads-root', default='data/raw', 
                       help='Raw downloads directory')
    parser.add_argument('--parsed-root', default='data/parsed', 
                       help='Parsed documents directory')
    parser.add_argument('--ticker', help='Upload specific ticker only')
    parser.add_argument('--mode', choices=['raw', 'parsed', 'both'], default='both',
                       help='Upload mode: raw PDFs only, parsed only, or both')
    parser.add_argument('--dry-run', action='store_true', help='Simulate upload')
    parser.add_argument('--no-db', action='store_true', help='Disable database')
    parser.add_argument('--debug', action='store_true', help='Debug logging')
    
    args = parser.parse_args()
    
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    uploader = UnifiedS3Uploader(
        bucket_name=args.bucket,
        downloads_root=args.downloads_root,
        parsed_root=args.parsed_root,
        dry_run=args.dry_run,
        enable_db=not args.no_db
    )
    
    try:
        if not uploader.verify_bucket():
            return
        
        results = uploader.upload_all_companies(
            specific_ticker=args.ticker,
            mode=args.mode
        )
        
        uploader.save_summary(results)
        
    finally:
        uploader.close()


if __name__ == "__main__":
    main()