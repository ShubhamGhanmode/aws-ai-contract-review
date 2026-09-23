import json
import os
from urllib.parse import unquote_plus

import boto3


dynamodb = boto3.resource("dynamodb")
stepfunctions = boto3.client("stepfunctions")

TABLE_NAME = os.environ["TABLE_NAME"]
STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]


def lambda_handler(event, context):
    print(f"Received S3 event: {json.dumps(event)}")

    record = event["Records"][0]
    bucket = record["s3"]["bucket"]["name"]
    key = unquote_plus(record["s3"]["object"]["key"])

    key_parts = key.split("/")
    if len(key_parts) < 3 or key_parts[0] != "uploads":
        raise ValueError(f"Unexpected upload key format: {key}")

    job_id = key_parts[1]

    table = dynamodb.Table(TABLE_NAME)
    table.update_item(
        Key={"job_id": job_id},
        UpdateExpression="SET #status = :status",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":status": "PROCESSING"},
    )

    execution_input = {
        "job_id": job_id,
        "bucket": bucket,
        "key": key,
    }

    response = stepfunctions.start_execution(
        stateMachineArn=STATE_MACHINE_ARN,
        input=json.dumps(execution_input),
    )

    print(
        f"Started Step Functions execution for job_id={job_id}, "
        f"executionArn={response['executionArn']}"
    )

    return {"status": "ok"}