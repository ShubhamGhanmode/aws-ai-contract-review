import json
import os
import uuid
from datetime import datetime, timezone

import boto3


s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")

BUCKET_NAME = os.environ["BUCKET_NAME"]
TABLE_NAME = os.environ["TABLE_NAME"]
EXPIRY_SECONDS = int(os.environ.get("UPLOAD_URL_EXPIRY_SECONDS", "900"))


def _json_response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def lambda_handler(event, context):
    body = json.loads(event.get("body") or "{}")
    filename = body.get("filename", "contract.pdf")

    job_id = str(uuid.uuid4())
    s3_key = f"uploads/{job_id}/contract.pdf"

    upload_url = s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": BUCKET_NAME,
            "Key": s3_key,
            "ContentType": "application/pdf",
        },
        ExpiresIn=EXPIRY_SECONDS,
    )

    table = dynamodb.Table(TABLE_NAME)
    table.put_item(
        Item={
            "job_id": job_id,
            "status": "PENDING",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "upload_s3_key": s3_key,
            "original_filename": filename,
        }
    )

    print(f"Generated upload URL for job_id={job_id}, s3_key={s3_key}")

    return _json_response(
        200,
        {
            "job_id": job_id,
            "upload_url": upload_url,
            "s3_key": s3_key,
        },
    )