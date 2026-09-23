import json
import os

import boto3


dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ["TABLE_NAME"]


def _json_response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def lambda_handler(event, context):
    job_id = (event.get("pathParameters") or {}).get("jobId")
    if not job_id:
        return _json_response(400, {"message": "Missing path parameter: jobId"})

    table = dynamodb.Table(TABLE_NAME)
    response = table.get_item(Key={"job_id": job_id})
    item = response.get("Item")

    if not item:
        return _json_response(404, {"message": "Job not found", "job_id": job_id})

    return _json_response(
        200,
        {
            "job_id": item["job_id"],
            "status": item.get("status"),
            "summary": item.get("summary"),
            "results_s3_key": item.get("results_s3_key"),
            "high_risk_count": item.get("high_risk_count"),
            "medium_risk_count": item.get("medium_risk_count"),
        },
    )