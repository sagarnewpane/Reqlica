import os
from openai import OpenAI



def main():
    client = OpenAI(
      base_url="https://openrouter.ai/api/v1",
      api_key=os.environ["OPENROUTER_API_KEY"],
    )
    
    # First API call with reasoning
    response = client.chat.completions.create(
      model="minimax/minimax-m3.1-flash:free",
      messages=[
              {
                "role": "user",
                "content": "How many r's are in the word 'strawberry'?"
              }
            ],
      extra_body={
              "provider": {
                "only": [
                  "minimax"
                ],
                "allow_fallbacks": False
              },
              "reasoning": {
                "enabled": True
              }
            }
    )
    
    # Extract the assistant message with reasoning_details
    response = response.choices[0].message
    
    print(response)
    
    # Preserve the assistant message with reasoning_details
    messages = [
      {"role": "user", "content": "How many r's are in the word 'strawberry'?"},
      {
        "role": "assistant",
        "content": response.content,
        "reasoning_details": response.reasoning_details  # Pass back unmodified
      },
      {"role": "user", "content": "Are you sure? Think carefully."}
    ]
    
    # Second API call - model continues reasoning from where it left off
    response2 = client.chat.completions.create(
      model="minimax/minimax-m3.1-flash:free",
      messages=messages,
      extra_body={
              "provider": {
                "only": [
                  "minimax"
                ],
                "allow_fallbacks": False
              },
              "reasoning": {
                "enabled": True
              }
            }
    )


if __name__ == "__main__":
    main()
