
from openai import OpenAI
import os
from dotenv import load_dotenv
import requests

load_dotenv()

OLLAMA_ENDPOINT = "http://localhost:11434/v1"
GROQ_ENDPOINT= "https://api.groq.com/openai/v1"

GET_OLLAMA_MODELS =  "http://localhost:11434/v1/models"
GET_GROQ_MODELS = "https://api.groq.com/openai/v1"


api_key = os.environ.get("GROQ_API")

client = OpenAI(
    api_key=os.environ.get("GROQ_API"),
    base_url=GROQ_ENDPOINT,
)

response = client.responses.create(
    instructions="You are a useful agent inside an app that helps create api endpoints for a frontend dev in FastAPI",
    input="create a /hello endpoint for a book reading to do app",
    model="openai/gpt-oss-20b",
)
print(response.output_text)


headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}

response = requests.get(url, headers=headers)

print(response.json())  