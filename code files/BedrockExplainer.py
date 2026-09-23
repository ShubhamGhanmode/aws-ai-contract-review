import json
import os
from datetime import datetime, timezone

import boto3


s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
bedrock_runtime = boto3.client("bedrock-runtime")

TABLE_NAME = os.environ["TABLE_NAME"]
BEDROCK_MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
BEDROCK_ANALYSIS_PROMPT_VERSION = os.environ.get("BEDROCK_ANALYSIS_PROMPT_VERSION", "v1")
SUMMARY_PROMPT_VERSION = os.environ.get("SUMMARY_PROMPT_VERSION", "v1")
MAX_CLAUSES_FOR_BEDROCK = int(os.environ.get("MAX_CLAUSES_FOR_BEDROCK", "20"))
AI_ANALYSIS_METHOD = "bedrock_semantic_analysis"


ALLOWED_SEVERITIES = {"HIGH", "MEDIUM", "LOW", "NONE"}


def load_json_from_s3(bucket, key):
    response = s3.get_object(Bucket=bucket, Key=key)
    return json.loads(response["Body"].read().decode("utf-8"))


def clause_for_prompt(clause):
    return {
        "clause_id": clause.get("clause_id"),
        "text": clause.get("text"),
        "preliminary_clause_type": clause.get("clause_type"),
        "preliminary_confidence": clause.get("confidence"),
        "preliminary_flagged": clause.get("flagged"),
        "preliminary_severity": clause.get("severity"),
        "preliminary_flag_reason": clause.get("flag_reason"),
    }


def build_analysis_prompt(clauses_payload):
    clauses = clauses_payload.get("clauses", [])[:MAX_CLAUSES_FOR_BEDROCK]
    prompt_input = {
        "preliminary_analysis_method": clauses_payload.get("preliminary_analysis_method"),
        "preliminary_analysis_model_id": clauses_payload.get("preliminary_analysis_model_id"),
        "clauses": [clause_for_prompt(clause) for clause in clauses],
    }

    return f"""
You are analyzing contract clauses for a decision-support prototype.
This is not legal advice. The user is a non-lawyer who needs help identifying clauses to discuss with a solicitor.

Analyze the actual clause wording. Do not rely only on the preliminary rule-based labels or flags. Those fields are hints from an earlier deterministic preprocessing step.

Return only valid JSON with this shape:
{{
  "summary": "short plain-English contract summary",
  "clauses": [
    {{
      "clause_id": "clause id from the input",
      "clause_type": "semantic clause type",
      "severity": "HIGH, MEDIUM, LOW, or NONE",
      "risk_reason": "why this wording may matter, grounded in the clause text",
      "evidence_text": "short excerpt from the clause",
      "explanation": "plain-English explanation for a non-lawyer",
      "confidence": 0.0,
      "needs_review": true
    }}
  ]
}}

Input clauses:
{json.dumps(prompt_input)}
""".strip()


def invoke_bedrock_json(prompt):
    response = bedrock_runtime.converse(
        modelId=BEDROCK_MODEL_ID,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "text": prompt
                    }
                ],
            }
        ],
        inferenceConfig={
            "maxTokens": 2000,
            "temperature": 0
        },
    )

    content_blocks = response.get("output", {}).get("message", {}).get("content", [])
    text_parts = [
        block.get("text", "")
        for block in content_blocks
        if isinstance(block, dict) and block.get("text")
    ]

    if not text_parts:
        raise ValueError(f"Bedrock response did not contain text output: {response}")

    text = "\n".join(text_parts).strip()
    return json.loads(extract_json_object(text))


def extract_json_object(text):
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"Bedrock output was not JSON: {text}")

    return text[start:end + 1]


def normalize_clause_result(result, original_by_id):
    clause_id = result.get("clause_id")
    original = original_by_id.get(clause_id, {})
    severity = str(result.get("severity", "NONE")).upper()
    if severity not in ALLOWED_SEVERITIES:
        severity = "NONE"

    return {
        "clause_id": clause_id,
        "text": original.get("text"),
        "preliminary_clause_type": original.get("clause_type"),
        "preliminary_severity": original.get("severity"),
        "clause_type": result.get("clause_type") or original.get("clause_type") or "other",
        "severity": severity,
        "risk_reason": result.get("risk_reason"),
        "evidence_text": result.get("evidence_text"),
        "explanation": result.get("explanation"),
        "confidence": result.get("confidence"),
        "needs_review": bool(result.get("needs_review", severity in {"HIGH", "MEDIUM"})),
    }


def count_severity(clauses, severity):
    return sum(1 for clause in clauses if clause.get("severity") == severity)


def lambda_handler(event, context):
    print(f"Received event: {json.dumps(event)}")

    job_id = event["job_id"]
    bucket = event["bucket"]
    clauses_s3_key = event["clauses_s3_key"]

    clauses_payload = load_json_from_s3(bucket, clauses_s3_key)
    original_clauses = clauses_payload.get("clauses", [])
    original_by_id = {clause.get("clause_id"): clause for clause in original_clauses}

    bedrock_result = invoke_bedrock_json(build_analysis_prompt(clauses_payload))
    analyzed_clauses = [
        normalize_clause_result(result, original_by_id)
        for result in bedrock_result.get("clauses", [])
    ]

    high_risk_count = count_severity(analyzed_clauses, "HIGH")
    medium_risk_count = count_severity(analyzed_clauses, "MEDIUM")
    flagged_clauses = [
        clause for clause in analyzed_clauses
        if clause.get("severity") in {"HIGH", "MEDIUM"} or clause.get("needs_review")
    ]

    summary = bedrock_result.get("summary") or "Bedrock analysis completed, but no summary was returned."
    final_results_s3_key = f"processed/{job_id}/final_results.json"
    execution_arn = event.get("execution_arn")
    generated_at = datetime.now(timezone.utc).isoformat()

    final_payload = {
        "job_id": job_id,
        "status": "COMPLETE",
        "summary": summary,
        "high_risk_count": high_risk_count,
        "medium_risk_count": medium_risk_count,
        "results_s3_key": final_results_s3_key,
        "clauses_s3_key": clauses_s3_key,
        "preliminary_analysis_method": clauses_payload.get("preliminary_analysis_method"),
        "preliminary_analysis_model_id": clauses_payload.get("preliminary_analysis_model_id"),
        "ai_analysis_method": AI_ANALYSIS_METHOD,
        "bedrock_model_id": BEDROCK_MODEL_ID,
        "bedrock_analysis_prompt_version": BEDROCK_ANALYSIS_PROMPT_VERSION,
        "summary_prompt_version": SUMMARY_PROMPT_VERSION,
        "execution_arn": execution_arn,
        "generated_at": generated_at,
        "clauses": analyzed_clauses,
        "flagged_clauses": flagged_clauses,
    }

    s3.put_object(
        Bucket=bucket,
        Key=final_results_s3_key,
        Body=json.dumps(final_payload).encode("utf-8"),
        ContentType="application/json",
    )

    table = dynamodb.Table(TABLE_NAME)
    table.update_item(
        Key={"job_id": job_id},
        UpdateExpression=(
            "SET #status = :status, summary = :summary, "
            "high_risk_count = :high_risk_count, "
            "medium_risk_count = :medium_risk_count, "
            "results_s3_key = :results_s3_key, "
            "preliminary_analysis_method = :preliminary_analysis_method, "
            "preliminary_analysis_model_id = :preliminary_analysis_model_id, "
            "ai_analysis_method = :ai_analysis_method, "
            "bedrock_model_id = :bedrock_model_id, "
            "bedrock_analysis_prompt_version = :bedrock_analysis_prompt_version, "
            "summary_prompt_version = :summary_prompt_version, "
            "execution_arn = :execution_arn"
        ),
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":status": "COMPLETE",
            ":summary": summary,
            ":high_risk_count": high_risk_count,
            ":medium_risk_count": medium_risk_count,
            ":results_s3_key": final_results_s3_key,
            ":preliminary_analysis_method": clauses_payload.get("preliminary_analysis_method"),
            ":preliminary_analysis_model_id": clauses_payload.get("preliminary_analysis_model_id"),
            ":ai_analysis_method": AI_ANALYSIS_METHOD,
            ":bedrock_model_id": BEDROCK_MODEL_ID,
            ":bedrock_analysis_prompt_version": BEDROCK_ANALYSIS_PROMPT_VERSION,
            ":summary_prompt_version": SUMMARY_PROMPT_VERSION,
            ":execution_arn": execution_arn or "unknown",
        },
    )

    print(
        f"Saved Bedrock semantic analysis for job_id={job_id}, "
        f"flagged_count={len(flagged_clauses)}, "
        f"results_s3_key={final_results_s3_key}"
    )

    return {
        "job_id": job_id,
        "status": "COMPLETE",
        "summary": summary,
        "results_s3_key": final_results_s3_key,
        "high_risk_count": high_risk_count,
        "medium_risk_count": medium_risk_count,
        "preliminary_analysis_method": clauses_payload.get("preliminary_analysis_method"),
        "preliminary_analysis_model_id": clauses_payload.get("preliminary_analysis_model_id"),
        "ai_analysis_method": AI_ANALYSIS_METHOD,
        "bedrock_model_id": BEDROCK_MODEL_ID,
        "bedrock_analysis_prompt_version": BEDROCK_ANALYSIS_PROMPT_VERSION,
        "summary_prompt_version": SUMMARY_PROMPT_VERSION,
        "flagged_clause_count": len(flagged_clauses),
    }