import json
import os

import boto3


textract = boto3.client("textract")
s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ.get("TABLE_NAME")


def fetch_all_textract_pages(textract_job_id):
    pages = []
    next_token = None

    while True:
        kwargs = {"JobId": textract_job_id}
        if next_token:
            kwargs["NextToken"] = next_token

        response = textract.get_document_text_detection(**kwargs)
        pages.append(response)

        next_token = response.get("NextToken")
        if not next_token:
            break

    return pages


def flatten_lines(pages):
    lines = []
    for page in pages:
        for block in page.get("Blocks", []):
            if block.get("BlockType") == "LINE" and block.get("Text"):
                lines.append(block["Text"])
    return "\n".join(lines)


def lambda_handler(event, context):
    print(f"Received event: {json.dumps(event)}")

    job_id = event["job_id"]
    bucket = event["bucket"]
    key = event["key"]
    textract_job_id = event["textract_job_id"]

    status_response = textract.get_document_text_detection(JobId=textract_job_id)
    status = status_response["JobStatus"]

    if status == "IN_PROGRESS":
        return {
            "job_id": job_id,
            "bucket": bucket,
            "key": key,
            "textract_job_id": textract_job_id,
            "textract_status": "IN_PROGRESS",
        }

    if status != "SUCCEEDED":
        raise RuntimeError(f"Textract job {textract_job_id} ended with status {status}")

    pages = fetch_all_textract_pages(textract_job_id)
    raw_text = flatten_lines(pages)

    textract_json_s3_key = f"processed/{job_id}/textract.json"
    raw_text_s3_key = f"processed/{job_id}/raw_text.txt"

    s3.put_object(
        Bucket=bucket,
        Key=textract_json_s3_key,
        Body=json.dumps(pages).encode("utf-8"),
        ContentType="application/json",
    )

    s3.put_object(
        Bucket=bucket,
        Key=raw_text_s3_key,
        Body=raw_text.encode("utf-8"),
        ContentType="text/plain",
    )

    if TABLE_NAME:
        table = dynamodb.Table(TABLE_NAME)
        table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET raw_text_s3_key = :raw_text_s3_key",
            ExpressionAttributeValues={":raw_text_s3_key": raw_text_s3_key},
        )

    return {
        "job_id": job_id,
        "bucket": bucket,
        "key": key,
        "textract_job_id": textract_job_id,
        "textract_status": "SUCCEEDED",
        "textract_json_s3_key": textract_json_s3_key,
        "raw_text_s3_key": raw_text_s3_key,
    }