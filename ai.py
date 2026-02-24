import json
import os
import re

from openai import OpenAI

from config import AI_INPUT_COST_PER_TOKEN, AI_OUTPUT_COST_PER_TOKEN


def generate_summary(full_text: str) -> dict | None:
    """Generate a summary + action items using GPT-5. Returns dict with summary, action_items, and usage, or None."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        client = OpenAI(api_key=api_key)
        # GPT-5 supports 400K tokens — send up to ~50K chars to cover long meetings
        text = full_text[:50000]
        resp = client.chat.completions.create(
            model="gpt-5",
            messages=[
                {"role": "system", "content": (
                    "You are a helpful assistant. Analyze the following transcript and return a JSON object with exactly two keys:\n"
                    "1. \"summary\": A 2-3 sentence summary. Focus on main topics discussed and key decisions. "
                    "Do not start with 'This transcript' or 'In this conversation' — just state what happened directly.\n"
                    "2. \"action_items\": An array of actionable tasks or follow-ups mentioned or implied in the conversation. "
                    "Each item should be a short, clear imperative sentence (e.g. \"Schedule follow-up meeting with design team\"). "
                    "If there are no action items, return an empty array.\n"
                    "Return ONLY valid JSON, no markdown fences, no extra text."
                )},
                {"role": "user", "content": text},
            ],
            max_completion_tokens=4000,
        )
        usage = resp.usage
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0
        cost = (input_tokens * AI_INPUT_COST_PER_TOKEN) + (output_tokens * AI_OUTPUT_COST_PER_TOKEN)

        raw = resp.choices[0].message.content.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        try:
            parsed = json.loads(raw)
            summary_text = parsed.get("summary", raw)
            action_items = parsed.get("action_items", [])
            if not isinstance(action_items, list):
                action_items = []
            # Ensure all items are strings
            action_items = [str(item) for item in action_items if item]
        except (json.JSONDecodeError, AttributeError):
            # Fallback: treat entire response as summary, no action items
            summary_text = raw
            action_items = []

        return {
            "text": summary_text,
            "action_items": action_items,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost": round(cost, 6),
        }
    except Exception:
        return None
