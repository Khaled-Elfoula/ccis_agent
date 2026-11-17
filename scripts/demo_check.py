"""Run the twelve demo messages through the real agent and show the tools it called.
Usage: py scripts/demo_check.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent.loop as loop_module
import config
from agent.loop import FRIENDLY_ERROR, Agent

MESSAGES = [
    "What programs does the college offer?",
    "ما هي برامج البكالوريوس في الكلية؟",
    "How do I transfer to the college?",
    "What are the office and email of Dr. Saad Abdullah AlAhmadi?",
    "And Dr. Saad Abdallah AlAhmadi?",
    "When is Dr. Saad AlAhmadi free on Sunday?",
    "What about Dr. Duaa AlSaeed on Tuesday?",
    "When is Dr. Sami Alwakeel free?",
    "When are the final exams for the first semester?",
    "What is the last day to drop a course?",
    "I want to withdraw from a course after the deadline.",
    "What's the cafeteria menu?",
]


def main() -> int:
    """Run every demo message and report the answer and the tools behind it."""
    sys.stdout.reconfigure(encoding="utf-8")
    if not config.GEMINI_API_KEY:
        print("GEMINI_API_KEY is not set in .env, so the demo cannot run.")
        return 1

    calls: list[tuple[str, dict]] = []
    real_execute = loop_module.execute_tool

    def record(name: str, arguments: dict):
        calls.append((name, arguments))
        return real_execute(name, arguments)

    loop_module.execute_tool = record

    try:
        agent = Agent()
    except Exception as exc:  # missing key, bad SDK setup, offline
        print(f"Could not start the agent: {exc}")
        return 1

    for number, message in enumerate(MESSAGES, start=1):
        calls.clear()
        print(f"\n--- {number}. {message}")
        try:
            answer = agent.reply(1, message)
        except Exception as exc:
            print(f"    failed: {exc}")
            return 1
        print(f"    answer: {answer}")
        if not calls:
            print("    tool: none")
        for name, arguments in calls:
            shown = json.dumps(arguments, ensure_ascii=False, default=str)
            print(f"    tool: {name}({shown})")
        if answer == FRIENDLY_ERROR:
            print(
                "\nThe Gemini API did not answer, so the demo stopped here. "
                "Check GEMINI_API_KEY, MODEL and MODEL_FALLBACKS in .env, then run again."
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
