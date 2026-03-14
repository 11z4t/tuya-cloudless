#!/usr/bin/env python3
"""Dual AI code review — Claude + GPT-4.1.

Runs both models on the latest diff and posts results to Slack.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request


def get_diff() -> str:
    """Get the git diff for review."""
    result = subprocess.run(
        ["git", "diff", "HEAD~1", "--", "*.py"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout[:4000] if result.stdout else "No Python changes detected."


def review_with_openai(diff: str) -> str:
    """Get review from GPT-4.1."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return "SKIP: No OpenAI API key"

    payload = json.dumps(
        {
            "model": "gpt-4.1",
            "messages": [
                {
                    "role": "system",
                    "content": "You are a senior code reviewer. Be concise. Max 200 words.",
                },
                {"role": "user", "content": f"Review this diff:\n\n{diff}"},
            ],
            "max_tokens": 500,
        }
    ).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"GPT review failed: {e}"


def notify_slack(message: str) -> None:
    """Send notification to Slack."""
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook:
        print("No Slack webhook configured, skipping notification")
        return

    payload = json.dumps({"text": message}).encode()
    req = urllib.request.Request(
        webhook,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as e:
        print(f"Slack notification failed: {e}")


def main() -> None:
    """Run dual review."""
    diff = get_diff()
    if diff == "No Python changes detected.":
        print("No Python changes to review")
        return

    gpt_review = review_with_openai(diff)
    print(f"GPT-4.1 Review:\n{gpt_review}")

    notify_slack(f"*Dual Review — tuya-cloudless*\n\n*GPT-4.1:*\n{gpt_review}")


if __name__ == "__main__":
    main()
