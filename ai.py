import json
import os
import re

from openai import OpenAI

from config import AI_INPUT_COST_PER_TOKEN, AI_OUTPUT_COST_PER_TOKEN


def generate_summary(full_text: str, user_name: str | None = None) -> dict | None:
    """Generate a summary + action items using GPT-5. Returns dict with summary, action_items, and usage, or None."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        client = OpenAI(api_key=api_key)
        # GPT-5 supports 400K tokens — send up to ~50K chars to cover long meetings
        text = full_text[:50000]

        system_prompt = (
            "You are a helpful assistant. Analyze the following meeting transcript and return a JSON object with exactly two keys:\n"
            "1. \"summary\": A 2-3 sentence summary. Focus on main topics discussed and key decisions. "
            "Do not start with 'This transcript' or 'In this conversation' — just state what happened directly.\n"
            "2. \"action_items\": An array of actionable tasks or follow-ups that were explicitly discussed, assigned, or committed to. "
            "IMPORTANT: Only include genuine action items — tasks someone agreed to do, deadlines that were set, or follow-ups that were explicitly requested. "
            "Do NOT invent or infer action items from general discussion. If the meeting was purely informational, a casual conversation, or had no concrete commitments, return an empty array.\n"
        )

        if user_name:
            system_prompt += (
                f'The user\'s name is "{user_name}". For each action item, return an object with two keys:\n'
                '  - "text": A short, clear imperative sentence describing the task.\n'
                '  - "assigned_to": One of "user" (if the task is for the user specifically — their name is mentioned, '
                'someone directed the task at them, or they volunteered), "other" (if the task is clearly for someone else), '
                'or null (if the assignee is unclear or it\'s a general team task).\n'
            )
        else:
            system_prompt += (
                "Each action item should be a short, clear imperative sentence "
                '(e.g. "Schedule follow-up meeting with design team").\n'
            )

        system_prompt += "Return ONLY valid JSON, no markdown fences, no extra text."

        resp = client.chat.completions.create(
            model="gpt-5",
            messages=[
                {"role": "system", "content": system_prompt},
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
            action_items_raw = parsed.get("action_items", [])
            if not isinstance(action_items_raw, list):
                action_items_raw = []
            # Normalize: support both string and object format
            action_items = []
            for item in action_items_raw:
                if not item:
                    continue
                if isinstance(item, dict):
                    action_items.append({
                        "text": str(item.get("text", "")),
                        "assigned_to": item.get("assigned_to"),
                    })
                else:
                    action_items.append({
                        "text": str(item),
                        "assigned_to": None,
                    })
        except (json.JSONDecodeError, AttributeError):
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
