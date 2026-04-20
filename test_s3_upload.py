"""
Quick S3 upload smoke test.
Run from the backend root:  python test_s3_upload.py
"""
import os
import sys
import io
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
sys.path.insert(0, os.path.dirname(__file__))
django.setup()

from django.conf import settings
import boto3
from botocore.exceptions import ClientError, NoCredentialsError

BUCKET   = getattr(settings, 'AWS_STORAGE_BUCKET_NAME', '')
REGION   = getattr(settings, 'AWS_S3_REGION_NAME', 'us-east-1')
KEY_ID   = getattr(settings, 'AWS_ACCESS_KEY_ID', '')
SECRET   = getattr(settings, 'AWS_SECRET_ACCESS_KEY', '')
TEST_KEY = 'sirena-test/upload-smoke-test.txt'

print('\n=== Sirena S3 Upload Smoke Test ===========================')
print(f'Bucket : {BUCKET}')
print(f'Region : {REGION}')
print(f'Key ID : {KEY_ID[:8]}...  (truncated)')
print('============================================================\n')

if not all([BUCKET, KEY_ID, SECRET]):
    print('FAIL  Missing AWS credentials or bucket name in settings.')
    sys.exit(1)

try:
    s3 = boto3.client(
        's3',
        region_name=REGION,
        aws_access_key_id=KEY_ID,
        aws_secret_access_key=SECRET,
    )

    # 1 ── Upload a small test file
    content = b'Sirena Health EHR - S3 upload smoke test.'
    print('[ 1 ] Uploading test object...')
    s3.put_object(
        Bucket=BUCKET,
        Key=TEST_KEY,
        Body=content,
        ContentType='text/plain',
    )
    print('      OK  Object uploaded.')

    # 2 ── Generate a presigned URL
    print('[ 2 ] Generating presigned URL (300s TTL)...')
    url = s3.generate_presigned_url(
        'get_object',
        Params={'Bucket': BUCKET, 'Key': TEST_KEY},
        ExpiresIn=300,
    )
    print(f'      OK  URL generated (first 80 chars): {url[:80]}...')

    # 3 ── Delete the test object (clean up)
    print('[ 3 ] Cleaning up test object...')
    s3.delete_object(Bucket=BUCKET, Key=TEST_KEY)
    print('      OK  Test object deleted.\n')

    print('ALL CHECKS PASSED  —  S3 upload is working correctly.')

except NoCredentialsError:
    print('FAIL  AWS credentials are missing or invalid.')
    sys.exit(1)
except ClientError as e:
    code = e.response['Error']['Code']
    msg  = e.response['Error']['Message']
    print(f'FAIL  AWS ClientError [{code}]: {msg}')
    sys.exit(1)
except Exception as e:
    print(f'FAIL  Unexpected error: {e}')
    sys.exit(1)
