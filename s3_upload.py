#!/usr/bin/env python3
"""
S3 Uploader with PostgreSQL RDS Integration
-------------------------------------------
Uploads parsed documents to AWS S3 and logs metadata to PostgreSQL RDS

Usage:
    # Upload all with database logging
    python s3_upload.py --bucket your-bucket --parsed-root data/parsed
    
    # Upload specific ticker
    python s3_upload.py --bucket your-bucket --parsed-root data/parsed --ticker AXP
    
    # Upload without database
    python s3_upload.py --bucket your-bucket --parsed-root data/parsed --no-db
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

# Database imports (optional)
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
            logger.warning("Database logging disabled (libraries not installed)")
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
            logger.warning(f"Database connection failed: {e}. Continuing without DB.")
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


class S3DocumentUploader:
    """Upload parsed earnings documents to S3 with organized structure"""
    
    def __init__(self, 
                 bucket_name: str,
                 parsed_root: str = "data/parsed",
                 s3_prefix: str = "earnings-documents",
                 dry_run: bool = False,
                 enable_db: bool = True):
        """
        Initialize S3 uploader
        
        Args:
            bucket_name: S3 bucket name
            parsed_root: Local directory containing parsed documents
            s3_prefix: Prefix (folder) in S3 bucket for all uploads
            dry_run: If True, simulate uploads without actually uploading
            enable_db: If True, enable database logging
        """
        self.bucket_name = bucket_name
        self.parsed_root = Path(parsed_root)
        self.s3_prefix = s3_prefix
        self.dry_run = dry_run
        
        # Initialize boto3 S3 client
        try:
            self.s3_client = boto3.client('s3')
            logger.info("✓ AWS S3 client initialized successfully")
        except NoCredentialsError:
            logger.error("AWS credentials not found. Run 'aws configure' first.")
            raise
        
        # Initialize database
        self.db = None
        if enable_db and not dry_run:
            self.db = DatabaseLogger()
        
        # Stats tracking
        self.stats = {
            'companies_processed': 0,
            'files_uploaded': 0,
            'bytes_uploaded': 0,
            'failed_uploads': 0,
            'db_records_created': 0,
            'errors': []
        }
        
        # MIME type mapping
        self.mime_types = {
            '.pdf': 'application/pdf',
            '.csv': 'text/csv',
            '.json': 'application/json',
            '.txt': 'text/plain',
            '.md': 'text/markdown',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.jsonl': 'application/jsonl'
        }
    
    def generate_file_id(self, s3_key: str) -> str:
        """Generate unique file ID using SHA256 hash"""
        return hashlib.sha256(s3_key.encode()).hexdigest()[:64]
    
    def determine_document_type(self, file_path: Path) -> str:
        """Determine document type from path"""
        path_str = str(file_path).lower()
        
        if '/markdown/' in path_str or path_str.endswith('.md'):
            return 'markdown'
        elif '/tables/' in path_str or path_str.endswith('.csv'):
            return 'table'
        elif '/images/' in path_str or path_str.endswith(('.png', '.jpg', '.jpeg')):
            return 'image'
        elif '/text/' in path_str or path_str.endswith('.txt'):
            return 'text'
        elif '/json/' in path_str or path_str.endswith('.json'):
            return 'structured_data'
        else:
            return 'other'
    
    def verify_bucket_exists(self) -> bool:
        """Verify that the S3 bucket exists and is accessible"""
        try:
            self.s3_client.head_bucket(Bucket=self.bucket_name)
            logger.info(f"✓ S3 bucket '{self.bucket_name}' exists and is accessible")
            return True
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == '404':
                logger.error(f"✗ Bucket '{self.bucket_name}' does not exist")
            elif error_code == '403':
                logger.error(f"✗ Access denied to bucket '{self.bucket_name}'")
            else:
                logger.error(f"✗ Error accessing bucket: {e}")
            return False
    
    def create_bucket_if_not_exists(self, region: str = 'us-east-1') -> bool:
        """Create S3 bucket if it doesn't exist"""
        try:
            if self.verify_bucket_exists():
                return True
            
            if self.dry_run:
                logger.info(f"[DRY RUN] Would create bucket '{self.bucket_name}' in {region}")
                return True
            
            logger.info(f"Creating bucket '{self.bucket_name}' in {region}...")
            
            if region == 'us-east-1':
                self.s3_client.create_bucket(Bucket=self.bucket_name)
            else:
                self.s3_client.create_bucket(
                    Bucket=self.bucket_name,
                    CreateBucketConfiguration={'LocationConstraint': region}
                )
            
            logger.info(f"✓ Bucket '{self.bucket_name}' created successfully")
            return True
            
        except ClientError as e:
            logger.error(f"✗ Failed to create bucket: {e}")
            return False
    
    def get_content_type(self, file_path: Path) -> str:
        """Determine content type for a file"""
        ext = file_path.suffix.lower()
        
        if ext in self.mime_types:
            return self.mime_types[ext]
        
        content_type, _ = mimetypes.guess_type(str(file_path))
        return content_type or 'application/octet-stream'
    
    def get_s3_key(self, ticker: str, relative_path: Path) -> str:
        """Generate S3 key for a file"""
        parts = relative_path.parts
        
        if parts[0] == ticker:
            parts = parts[1:]
        
        relative_str = '/'.join(parts)
        return f"{self.s3_prefix}/{ticker}/{relative_str}"
    
    def upload_file(self, local_path: Path, s3_key: str, ticker: str) -> bool:
        """
        Upload a single file to S3 and log to database
        
        Args:
            local_path: Local file path
            s3_key: S3 object key (path in bucket)
            ticker: Company ticker
            
        Returns:
            True if successful, False otherwise
        """
        try:
            if self.dry_run:
                logger.debug(f"[DRY RUN] Would upload: {local_path}")
                return True
            
            # Get file info
            file_size = local_path.stat().st_size
            content_type = self.get_content_type(local_path)
            
            # Upload to S3
            extra_args = {'ContentType': content_type}
            
            self.s3_client.upload_file(
                str(local_path),
                self.bucket_name,
                s3_key,
                ExtraArgs=extra_args
            )
            
            # Update stats
            self.stats['files_uploaded'] += 1
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
                    'document_type': self.determine_document_type(local_path),
                    'document_subtype': 'quarterly',
                    'source_url': None,
                    'source_domain': None,
                    'download_timestamp': datetime.now(),
                    's3_bucket': self.bucket_name
                }
                
                if self.db.log_document(metadata):
                    self.stats['db_records_created'] += 1
            
            logger.debug(f"✓ Uploaded: {s3_key} ({file_size:,} bytes)")
            return True
            
        except ClientError as e:
            logger.error(f"✗ Failed to upload {local_path}: {e}")
            self.stats['failed_uploads'] += 1
            self.stats['errors'].append({
                'file': str(local_path),
                's3_key': s3_key,
                'error': str(e)
            })
            return False
        except Exception as e:
            logger.error(f"✗ Unexpected error uploading {local_path}: {e}")
            self.stats['failed_uploads'] += 1
            self.stats['errors'].append({
                'file': str(local_path),
                's3_key': s3_key,
                'error': str(e)
            })
            return False
    
    def upload_company_documents(self, ticker: str, ticker_dir: Path) -> Dict:
        """Upload all documents for a single company"""
        result = {
            'ticker': ticker,
            'status': 'pending',
            'files_uploaded': 0,
            'bytes_uploaded': 0,
            'db_records': 0,
            'failed_files': 0,
            'start_time': datetime.now().isoformat()
        }
        
        try:
            logger.info(f"\n{'='*70}")
            logger.info(f"Uploading documents for {ticker}")
            logger.info(f"{'='*70}")
            
            # Find all files recursively
            all_files = list(ticker_dir.rglob('*'))
            file_list = [f for f in all_files if f.is_file()]
            
            if not file_list:
                logger.warning(f"No files found for {ticker}")
                result['status'] = 'no_files'
                return result
            
            logger.info(f"Found {len(file_list)} files to upload")
            
            # Track DB records before upload
            db_before = self.stats['db_records_created']
            
            # Upload each file with progress bar
            with tqdm(file_list, desc=f"{ticker}", unit="file") as pbar:
                for file_path in pbar:
                    relative_path = file_path.relative_to(self.parsed_root)
                    s3_key = self.get_s3_key(ticker, relative_path)
                    
                    if self.upload_file(file_path, s3_key, ticker):
                        result['files_uploaded'] += 1
                        result['bytes_uploaded'] += file_path.stat().st_size
                        pbar.set_postfix({'uploaded': result['files_uploaded']})
                    else:
                        result['failed_files'] += 1
            
            result['status'] = 'success'
            result['db_records'] = self.stats['db_records_created'] - db_before
            result['end_time'] = datetime.now().isoformat()
            
            logger.info(f"✓ {ticker}: Uploaded {result['files_uploaded']}/{len(file_list)} files "
                       f"({result['bytes_uploaded']:,} bytes)")
            if self.db and self.db.connection:
                logger.info(f"  DB records created: {result['db_records']}")
            
            self.stats['companies_processed'] += 1
            
        except Exception as e:
            logger.error(f"✗ {ticker}: Error during upload - {e}")
            result['status'] = 'error'
            result['error'] = str(e)
            self.stats['errors'].append({
                'ticker': ticker,
                'error': str(e)
            })
        
        return result
    
    def upload_all_companies(self, specific_ticker: Optional[str] = None) -> List[Dict]:
        """Upload documents for all companies (or a specific ticker)"""
        results = []
        
        if not self.parsed_root.exists():
            logger.error(f"Parsed directory not found: {self.parsed_root}")
            return results
        
        # Get list of ticker directories
        if specific_ticker:
            ticker_dirs = [self.parsed_root / specific_ticker]
            if not ticker_dirs[0].exists():
                logger.error(f"Ticker directory not found: {ticker_dirs[0]}")
                return results
        else:
            ticker_dirs = [d for d in self.parsed_root.iterdir() if d.is_dir()]
        
        db_status = "Enabled" if (self.db and self.db.connection) else "Disabled"
        
        logger.info(f"\n{'='*70}")
        logger.info(f"S3 DOCUMENT UPLOAD")
        logger.info(f"{'='*70}")
        logger.info(f"Bucket: {self.bucket_name}")
        logger.info(f"Prefix: {self.s3_prefix}")
        logger.info(f"Source: {self.parsed_root}")
        logger.info(f"Companies: {len(ticker_dirs)}")
        logger.info(f"Database: {db_status}")
        logger.info(f"Dry Run: {self.dry_run}")
        logger.info(f"{'='*70}\n")
        
        start_time = datetime.now()
        
        # Upload each company
        for idx, ticker_dir in enumerate(sorted(ticker_dirs), 1):
            ticker = ticker_dir.name
            logger.info(f"\n[{idx}/{len(ticker_dirs)}] Processing {ticker}...")
            
            result = self.upload_company_documents(ticker, ticker_dir)
            results.append(result)
        
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        # Final summary
        logger.info(f"\n{'='*70}")
        logger.info(f"UPLOAD COMPLETE")
        logger.info(f"{'='*70}")
        logger.info(f"Duration: {duration:.1f}s ({duration/60:.1f} minutes)")
        logger.info(f"Companies processed: {self.stats['companies_processed']}/{len(ticker_dirs)}")
        logger.info(f"Files uploaded: {self.stats['files_uploaded']:,}")
        logger.info(f"Data uploaded: {self.stats['bytes_uploaded']:,} bytes ({self.stats['bytes_uploaded']/1024/1024:.1f} MB)")
        logger.info(f"DB records created: {self.stats['db_records_created']:,}")
        logger.info(f"Failed uploads: {self.stats['failed_uploads']}")
        
        if self.stats['errors']:
            logger.warning(f"\nErrors encountered: {len(self.stats['errors'])}")
            for error in self.stats['errors'][:5]:
                logger.warning(f"  - {error}")
        
        logger.info(f"{'='*70}\n")
        
        return results
    
    def save_upload_summary(self, results: List[Dict], output_path: Optional[Path] = None):
        """Save upload summary to JSON file"""
        if output_path is None:
            output_path = self.parsed_root / 'upload_summary.json'
        
        summary = {
            'upload_info': {
                'bucket': self.bucket_name,
                's3_prefix': self.s3_prefix,
                'source_directory': str(self.parsed_root),
                'timestamp': datetime.now().isoformat(),
                'dry_run': self.dry_run
            },
            'statistics': self.stats,
            'company_results': results
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, default=str)
        
        logger.info(f"Upload summary saved to: {output_path}")
    
    def list_uploaded_files(self, ticker: Optional[str] = None, max_files: int = 100):
        """List files in S3 bucket for verification"""
        try:
            prefix = f"{self.s3_prefix}/"
            if ticker:
                prefix = f"{self.s3_prefix}/{ticker}/"
            
            logger.info(f"\nListing files in s3://{self.bucket_name}/{prefix}")
            logger.info(f"{'='*70}")
            
            paginator = self.s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=self.bucket_name, Prefix=prefix)
            
            count = 0
            total_size = 0
            
            for page in pages:
                if 'Contents' not in page:
                    continue
                
                for obj in page['Contents']:
                    if count >= max_files:
                        break
                    
                    key = obj['Key']
                    size = obj['Size']
                    modified = obj['LastModified']
                    
                    logger.info(f"{key} ({size:,} bytes) - {modified}")
                    
                    count += 1
                    total_size += size
            
            logger.info(f"{'='*70}")
            logger.info(f"Total files: {count}")
            logger.info(f"Total size: {total_size:,} bytes ({total_size/1024/1024:.1f} MB)")
            
            if count >= max_files:
                logger.info(f"(Showing first {max_files} files)")
            
        except ClientError as e:
            logger.error(f"Error listing files: {e}")
    
    def close(self):
        """Cleanup resources"""
        if self.db:
            self.db.close()


def main():
    parser = argparse.ArgumentParser(
        description='Upload parsed earnings documents to AWS S3'
    )
    parser.add_argument(
        '--bucket',
        required=True,
        help='S3 bucket name'
    )
    parser.add_argument(
        '--parsed-root',
        default='data/parsed',
        help='Root directory containing parsed documents'
    )
    parser.add_argument(
        '--prefix',
        default='earnings-documents',
        help='S3 prefix (folder) for uploads'
    )
    parser.add_argument(
        '--ticker',
        help='Upload only this specific ticker'
    )
    parser.add_argument(
        '--create-bucket',
        action='store_true',
        help='Create bucket if it does not exist'
    )
    parser.add_argument(
        '--region',
        default='us-east-1',
        help='AWS region for bucket creation'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Simulate uploads without actually uploading'
    )
    parser.add_argument(
        '--no-db',
        action='store_true',
        help='Disable database logging'
    )
    parser.add_argument(
        '--list-files',
        action='store_true',
        help='List uploaded files after upload'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug logging'
    )
    
    args = parser.parse_args()
    
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Initialize uploader
    uploader = S3DocumentUploader(
        bucket_name=args.bucket,
        parsed_root=args.parsed_root,
        s3_prefix=args.prefix,
        dry_run=args.dry_run,
        enable_db=not args.no_db
    )
    
    try:
        # Create bucket if requested
        if args.create_bucket:
            if not uploader.create_bucket_if_not_exists(region=args.region):
                logger.error("Failed to create/verify bucket. Exiting.")
                return
        else:
            # Just verify bucket exists
            if not uploader.verify_bucket_exists():
                logger.error("Bucket does not exist. Use --create-bucket to create it.")
                return
        
        # Upload documents
        results = uploader.upload_all_companies(specific_ticker=args.ticker)
        
        # Save summary
        uploader.save_upload_summary(results)
        
        # List files if requested
        if args.list_files:
            uploader.list_uploaded_files(ticker=args.ticker, max_files=50)
    
    finally:
        uploader.close()


if __name__ == "__main__":
    main()