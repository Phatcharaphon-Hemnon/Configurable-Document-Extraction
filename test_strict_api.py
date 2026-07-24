import asyncio
from openai import AsyncOpenAI
from app.core.config import Settings

async def test_schema():
    settings = Settings()
    client = AsyncOpenAI(
        api_key=settings.github_models_token.strip(),
        base_url="https://models.github.ai/inference"
    )
    
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "value": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "number"},
                    {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": True
                        }
                    }
                ]
            }
        },
        "required": ["value"]
    }
    
    try:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "hello"}],
            response_format={"type": "json_schema", "json_schema": {"name": "test_schema", "schema": schema, "strict": True}},
        )
        print("Success with items: {}")
    except Exception as e:
        print(f"Failed with items {{}}: {e}")

asyncio.run(test_schema())
