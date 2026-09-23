import json
import os
import re

import boto3


s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")

TABLE_NAME = os.environ.get("TABLE_NAME")
PRELIMINARY_ANALYSIS_METHOD = os.environ.get("PRELIMINARY_ANALYSIS_METHOD", "heuristic_rules_v1")
PRELIMINARY_ANALYSIS_MODEL_ID = os.environ.get("PRELIMINARY_ANALYSIS_MODEL_ID", "none_rule_based")
CLAUSE_MIN_CHARS = int(os.environ.get("CLAUSE_MIN_CHARS", "60"))

RISK_BY_CLAUSE_TYPE = {
    "indemnity": "HIGH",
    "liability_cap": "HIGH",
    "ip_ownership": "HIGH",
    "termination": "MEDIUM",
    "payment_terms": "MEDIUM",
    "warranty": "MEDIUM",
    "confidentiality": "LOW",
    "governing_law": "LOW",
    "dispute_resolution": "LOW",
    "force_majeure": "LOW",
}

KEYWORD_RULES = {
    "confidentiality": ["confidential", "non-disclosure", "nda"],
    "termination": ["terminate", "termination", "notice period"],
    "payment_terms": ["payment", "invoice", "fees", "late fee"],
    "liability_cap": ["limitation of liability", "liability cap", "maximum liability"],
    "indemnity": ["indemnify", "indemnification", "hold harmless"],
    "ip_ownership": ["intellectual property", "ownership of work product", "copyright"],
    "warranty": ["warranty", "warrant", "represents and warrants"],
    "governing_law": ["governing law", "jurisdiction"],
    "dispute_resolution": ["arbitration", "mediation", "dispute resolution"],
    "force_majeure": ["force majeure", "acts of god"],
}


def load_raw_text(bucket, raw_text_s3_key):
    response = s3.get_object(Bucket=bucket, Key=raw_text_s3_key)
    return response["Body"].read().decode("utf-8")


def normalize_text(value):
    return re.sub(r"\s+", " ", value).strip()


def split_into_clauses(raw_text):
    numbered_chunks = re.split(
        r"\n(?=(?:\d+(?:\.\d+)*|[A-Z]\.|[IVX]+\.?)\s+)",
        raw_text,
    )

    clauses = [normalize_text(chunk) for chunk in numbered_chunks if normalize_text(chunk)]

    if len(clauses) < 3:
        paragraph_chunks = re.split(r"\n\s*\n", raw_text)
        clauses = [normalize_text(chunk) for chunk in paragraph_chunks if normalize_text(chunk)]

    return [clause for clause in clauses if len(clause) >= CLAUSE_MIN_CHARS]


def heuristic_classify(text):
    lowered = text.lower()

    for clause_type, keywords in KEYWORD_RULES.items():
        if any(keyword in lowered for keyword in keywords):
            return {
                "clause_type": clause_type,
                "confidence": 0.65,
            }

    return {
        "clause_type": "other",
        "confidence": 0.30,
    }


def risk_for_clause_type(clause_type):
    severity = RISK_BY_CLAUSE_TYPE.get(clause_type)
    if not severity:
        return False, None, None

    return True, severity, f"Clause type {clause_type} is mapped to {severity} risk in prototype rules"


def build_clause_record(clause_id, text, prediction):
    clause_type = prediction["clause_type"]
    confidence = prediction.get("confidence")
    flagged, severity, flag_reason = risk_for_clause_type(clause_type)

    return {
        "clause_id": clause_id,
        "text": text,
        "clause_type": clause_type,
        "confidence": confidence,
        "flagged": flagged,
        "severity": severity,
        "flag_reason": flag_reason,
    }


def lambda_handler(event, context):
    print(f"Received event: {json.dumps(event)}")

    job_id = event["job_id"]
    bucket = event["bucket"]
    raw_text_s3_key = event["raw_text_s3_key"]

    raw_text = load_raw_text(bucket, raw_text_s3_key)
    clauses = split_into_clauses(raw_text)

    clause_records = []

    for index, clause_text in enumerate(clauses, start=1):
        prediction = heuristic_classify(clause_text)

        clause_records.append(
            build_clause_record(
                clause_id=f"clause-{index}",
                text=clause_text,
                prediction=prediction,
            )
        )

    high_risk_count = sum(1 for clause in clause_records if clause["severity"] == "HIGH")
    medium_risk_count = sum(1 for clause in clause_records if clause["severity"] == "MEDIUM")

    clauses_s3_key = f"processed/{job_id}/clauses.json"
    output_payload = {
        "job_id": job_id,
        "raw_text_s3_key": raw_text_s3_key,
        "preliminary_analysis_method": PRELIMINARY_ANALYSIS_METHOD,
        "preliminary_analysis_model_id": PRELIMINARY_ANALYSIS_MODEL_ID,
        "clause_count": len(clause_records),
        "high_risk_count": high_risk_count,
        "medium_risk_count": medium_risk_count,
        "clauses": clause_records,
    }

    s3.put_object(
        Bucket=bucket,
        Key=clauses_s3_key,
        Body=json.dumps(output_payload).encode("utf-8"),
        ContentType="application/json",
    )

    if TABLE_NAME:
        table = dynamodb.Table(TABLE_NAME)
        table.update_item(
            Key={"job_id": job_id},
            UpdateExpression=(
                "SET preliminary_analysis_method = :preliminary_analysis_method, "
                "preliminary_analysis_model_id = :preliminary_analysis_model_id"
            ),
            ExpressionAttributeValues={
                ":preliminary_analysis_method": PRELIMINARY_ANALYSIS_METHOD,
                ":preliminary_analysis_model_id": PRELIMINARY_ANALYSIS_MODEL_ID,
            },
        )

    return {
        "job_id": job_id,
        "bucket": bucket,
        "clauses_s3_key": clauses_s3_key,
        "preliminary_analysis_method": PRELIMINARY_ANALYSIS_METHOD,
        "preliminary_analysis_model_id": PRELIMINARY_ANALYSIS_MODEL_ID,
        "clause_count": len(clause_records),
        "high_risk_count": high_risk_count,
        "medium_risk_count": medium_risk_count,
    }