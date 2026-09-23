import json
import os

import boto3


textract = boto3.client("textract")
dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ.get("TABLE_NAME")


def lambda_handler(event, context):
    print(f"Received event: {json.dumps(event)}")

    job_id = event["job_id"]
    bucket = event["bucket"]
    key = event["key"]

    response = textract.start_document_text_detection(
        DocumentLocation={
            "S3Object": {
                "Bucket": bucket,
                "Name": key,
            }
        }
    )

    textract_job_id = response["JobId"]

    if TABLE_NAME:
        table = dynamodb.Table(TABLE_NAME)
        table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET textract_job_id = :textract_job_id",
            ExpressionAttributeValues={":textract_job_id": textract_job_id},
        )

    return {
        "job_id": job_id,
        "bucket": bucket,
        "key": key,
        "textract_job_id": textract_job_id,
        "textract_status": "IN_PROGRESS",
    }