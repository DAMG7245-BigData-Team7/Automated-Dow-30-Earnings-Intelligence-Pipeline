import os
import boto3
import logging
from botocore.exceptions import ClientError, NoCredentialsError
from pathlib import Path
from typing import Optional, List
import mimetypes
from datetime import datetime

class S3Uploader:
    def __init__(self, bucket_name: str, aws_access_key_id: Optional[str] = None,
                 aws_secret_access_key: Optional[str] = None, region_name: str = 'us-east-1'):
        """
        Initialize S3 uploader

        Args:
            bucket_name: Name of the S3 bucket
            aws_access_key_id: AWS access key (optional, can use env vars or IAM roles)
            aws_secret_access_key: AWS secret key (optional, can use env vars or IAM roles)
            region_name: AWS region name
        """
        self.bucket_name = bucket_name
        self.region_name = region_name

        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)

        # Initialize S3 client
        try:
            if aws_access_key_id and aws_secret_access_key:
                self.s3_client = boto3.client(
                    's3',
                    aws_access_key_id=aws_access_key_id,
                    aws_secret_access_key=aws_secret_access_key,
                    region_name=region_name
                )
            else:
                # Use default credentials (env vars, IAM roles, or AWS CLI config)
                self.s3_client = boto3.client('s3', region_name=region_name)

            self.logger.info(f"S3 client initialized for bucket: {bucket_name}")

        except Exception as e:
            self.logger.error(f"Failed to initialize S3 client: {e}")
            raise

    def upload_file(self, local_file_path: str, s3_key: Optional[str] = None) -> bool:
        """
        Upload a single file to S3

        Args:
            local_file_path: Path to the local file
            s3_key: S3 object key (path in bucket). If None, uses filename

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Convert to Path object for easier manipulation
            file_path = Path(local_file_path)

            if not file_path.exists():
                self.logger.error(f"File not found: {local_file_path}")
                return False

            # Generate S3 key if not provided
            if s3_key is None:
                s3_key = file_path.name

            # Get file content type
            content_type, _ = mimetypes.guess_type(local_file_path)
            if content_type is None:
                content_type = 'binary/octet-stream'

            # Upload file
            extra_args = {'ContentType': content_type}

            self.s3_client.upload_file(
                str(file_path),
                self.bucket_name,
                s3_key,
                ExtraArgs=extra_args
            )

            self.logger.info(f"Successfully uploaded: {local_file_path} -> s3://{self.bucket_name}/{s3_key}")
            return True

        except FileNotFoundError:
            self.logger.error(f"File not found: {local_file_path}")
            return False
        except NoCredentialsError:
            self.logger.error("AWS credentials not found")
            return False
        except ClientError as e:
            self.logger.error(f"AWS error uploading {local_file_path}: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error uploading {local_file_path}: {e}")
            return False

    def upload_directory(self, local_directory: str, s3_prefix: str = "",
                        preserve_structure: bool = True) -> dict:
        """
        Upload all files in a directory to S3

        Args:
            local_directory: Path to the local directory
            s3_prefix: Prefix to add to S3 keys (like a folder path)
            preserve_structure: Whether to preserve directory structure in S3

        Returns:
            dict: Summary of upload results
        """
        results = {
            'total_files': 0,
            'successful_uploads': 0,
            'failed_uploads': 0,
            'uploaded_files': [],
            'failed_files': []
        }

        try:
            directory_path = Path(local_directory)

            if not directory_path.exists():
                self.logger.error(f"Directory not found: {local_directory}")
                return results

            if not directory_path.is_dir():
                self.logger.error(f"Path is not a directory: {local_directory}")
                return results

            # Get all files recursively
            all_files = []
            for file_path in directory_path.rglob('*'):
                if file_path.is_file():
                    all_files.append(file_path)

            results['total_files'] = len(all_files)
            self.logger.info(f"Found {len(all_files)} files to upload from {local_directory}")

            for file_path in all_files:
                if preserve_structure:
                    # Maintain directory structure
                    relative_path = file_path.relative_to(directory_path)
                    s3_key = f"{s3_prefix}/{relative_path}" if s3_prefix else str(relative_path)
                else:
                    # Flat structure - just filename
                    s3_key = f"{s3_prefix}/{file_path.name}" if s3_prefix else file_path.name

                # Replace backslashes with forward slashes for S3
                s3_key = s3_key.replace('\\', '/')

                if self.upload_file(str(file_path), s3_key):
                    results['successful_uploads'] += 1
                    results['uploaded_files'].append({
                        'local_path': str(file_path),
                        's3_key': s3_key,
                        'size_bytes': file_path.stat().st_size
                    })
                else:
                    results['failed_uploads'] += 1
                    results['failed_files'].append(str(file_path))

            self.logger.info(f"Upload complete: {results['successful_uploads']}/{results['total_files']} files uploaded successfully")

        except Exception as e:
            self.logger.error(f"Error uploading directory {local_directory}: {e}")

        return results

    def list_bucket_objects(self, prefix: str = "") -> List[str]:
        """
        List objects in the S3 bucket

        Args:
            prefix: Filter objects by prefix

        Returns:
            List[str]: List of object keys
        """
        try:
            objects = []
            paginator = self.s3_client.get_paginator('list_objects_v2')

            for page in paginator.paginate(Bucket=self.bucket_name, Prefix=prefix):
                if 'Contents' in page:
                    for obj in page['Contents']:
                        objects.append(obj['Key'])

            return objects

        except Exception as e:
            self.logger.error(f"Error listing bucket objects: {e}")
            return []


def main():
    """
    Main function to upload files from downloads directory to S3
    """
    # Configuration - modify these values as needed
    BUCKET_NAME = "your-bucket-name"  # Replace with your actual bucket name
    DOWNLOADS_DIR = "downloads_2025"   # Directory containing files to upload
    S3_PREFIX = f"dow30-earnings/{datetime.now().strftime('%Y-%m-%d')}"  # S3 folder structure

    # AWS credentials (optional - can use environment variables or IAM roles)
    AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
    AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
    AWS_REGION = os.getenv('AWS_DEFAULT_REGION', 'us-east-1')

    try:
        # Initialize uploader
        uploader = S3Uploader(
            bucket_name=BUCKET_NAME,
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_REGION
        )

        # Upload all files from downloads directory
        print(f"Starting upload of files from {DOWNLOADS_DIR} to S3 bucket {BUCKET_NAME}")
        print(f"S3 prefix: {S3_PREFIX}")

        results = uploader.upload_directory(
            local_directory=DOWNLOADS_DIR,
            s3_prefix=S3_PREFIX,
            preserve_structure=True
        )

        # Print summary
        print("\n" + "="*50)
        print("UPLOAD SUMMARY")
        print("="*50)
        print(f"Total files found: {results['total_files']}")
        print(f"Successfully uploaded: {results['successful_uploads']}")
        print(f"Failed uploads: {results['failed_uploads']}")

        if results['failed_files']:
            print(f"\nFailed files:")
            for failed_file in results['failed_files']:
                print(f"  - {failed_file}")

        # Calculate total size uploaded
        total_size = sum(file_info['size_bytes'] for file_info in results['uploaded_files'])
        total_size_mb = total_size / (1024 * 1024)
        print(f"\nTotal data uploaded: {total_size_mb:.2f} MB")

        print(f"\nAll uploaded files are available at: s3://{BUCKET_NAME}/{S3_PREFIX}/")

    except Exception as e:
        print(f"Error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())