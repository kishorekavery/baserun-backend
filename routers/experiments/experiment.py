import os
import sys
import json
from pathlib import Path

cur_path = Path(__file__).resolve().parent
# Add project root to sys.path
sys.path.append(str(cur_path.parent.parent))

from models.datamodel import RegisterRequest
import boto3
from dotenv import load_dotenv

load_dotenv()

SYSTEM_PROMPT = "You are the welcome assistant for an entry level runner, create a welcome message for the user"
DEFAULT_BEDROCK_MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID",
    "arn:aws:bedrock:eu-north-1:193793987425:inference-profile/eu.amazon.nova-2-lite-v1:0"
)


def llm_client():
    kwargs = {}
    if os.getenv("AWS_ACCESS_KEY") and os.getenv("AWS_SECRET_KEY") and os.getenv("AWS_REGION"):
        kwargs["aws_access_key_id"] = os.getenv("AWS_ACCESS_KEY")
        kwargs["aws_secret_access_key"] = os.getenv("AWS_SECRET_KEY")
        kwargs["region_name"] = os.getenv("AWS_REGION")
    return boto3.client("bedrock-runtime", **kwargs)


def llm_invoke(text: str) -> str:
    client = llm_client()
    prompt = f"SYSTEM_PROMPT : {SYSTEM_PROMPT}\n\nUSER_PROMPT : {text}\n"
    payload = {
        "messages": [
            {"role": "user", "content": [{"text": prompt}]}
        ]
    }
    response = client.invoke_model(
        modelId=DEFAULT_BEDROCK_MODEL_ID,
        body=json.dumps(payload),
        contentType="application/json",
        accept="application/json"
    )

    response_body = json.loads(response['body'].read().decode())
    return response_body["output"]["message"]["content"][0]["text"]


def welcome_user(request: RegisterRequest) -> str:
    return llm_invoke(f"new user name : {request.first_name} {request.last_name or ''}")


if __name__ == "__main__":
    test_req = RegisterRequest(
        username="johndoe",
        first_name="John",
        last_name="Doe",
        email="doe@gmail.com",
        password="password123",
        phone="7397262198"
    )
    print(welcome_user(test_req))


