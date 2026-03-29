"""
One-time script to create the S3 bucket for Sirena EHR document storage.

Configures:
  - Block all public access
  - Bucket versioning enabled
  - Server-side encryption (AES-256 / SSE-S3)

Usage:
    python scripts/create_s3_bucket.py
"""
import os
import sys
from pathlib import Path

# Load .env from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / '.env')

import boto3
from botocore.exceptions import ClientError

BUCKET_NAME = os.getenv('AWS_STORAGE_BUCKET_NAME', 'bakerstreetehr-documents')
REGION = os.getenv('AWS_S3_REGION_NAME', 'us-east-1')


def _get_s3_client():
    return boto3.client(
        's3',
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
        region_name=REGION,
    )


def _create_bucket(s3):
    try:
        if REGION == 'us-east-1':
            s3.create_bucket(Bucket=BUCKET_NAME)
        else:
            s3.create_bucket(
                Bucket=BUCKET_NAME,
                CreateBucketConfiguration={'LocationConstraint': REGION},
            )
        print(f'[OK] Bucket "{BUCKET_NAME}" created in {REGION}')
    except ClientError as exc:
        code = exc.response['Error']['Code']
        if code in ('BucketAlreadyOwnedByYou', 'BucketAlreadyExists'):
            print(f'[OK] Bucket "{BUCKET_NAME}" already exists — continuing setup')
        else:
            print(f'[ERROR] Failed to create bucket: {exc}')
            sys.exit(1)


def _block_public_access(s3):
    try:
        s3.put_public_access_block(
            Bucket=BUCKET_NAME,
            PublicAccessBlockConfiguration={
                'BlockPublicAcls': True,
                'IgnorePublicAcls': True,
                'BlockPublicPolicy': True,
                'RestrictPublicBuckets': True,
            },
        )
        print('[OK] Public access blocked')
    except ClientError as exc:
        print(f'[WARN] Could not set public access block: {exc}')


def _enable_versioning(s3):
    try:
        s3.put_bucket_versioning(
            Bucket=BUCKET_NAME,
            VersioningConfiguration={'Status': 'Enabled'},
        )
        print('[OK] Versioning enabled')
    except ClientError as exc:
        print(f'[WARN] Could not enable versioning: {exc}')


def _enable_encryption(s3):
    try:
        s3.put_bucket_encryption(
            Bucket=BUCKET_NAME,
            ServerSideEncryptionConfiguration={
                'Rules': [{
                    'ApplyServerSideEncryptionByDefault': {
                        'SSEAlgorithm': 'AES256',
                    },
                    'BucketKeyEnabled': True,
                }],
            },
        )
        print('[OK] Server-side encryption enabled (AES-256)')
    except ClientError as exc:
        print(f'[WARN] Could not enable encryption: {exc}')


def create_bucket():
    s3 = _get_s3_client()
    _create_bucket(s3)
    _block_public_access(s3)
    _enable_versioning(s3)
    _enable_encryption(s3)
    print(f'\nDone! Bucket "{BUCKET_NAME}" is ready.')
    print(f'Region: {REGION}')


if __name__ == '__main__':
    if not os.getenv('AWS_ACCESS_KEY_ID') or not os.getenv('AWS_SECRET_ACCESS_KEY'):
        print('[ERROR] AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set in .env')
        sys.exit(1)
    create_bucket()
