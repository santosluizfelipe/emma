"""Chat with the agent in the terminal.

    python -m app.agent                         # interactive
    python -m app.agent "Who should I worry about this week?"
"""

from __future__ import annotations

import logging
import sys

from app import config
from app.agent.loop import Agent


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    agent = Agent(org_unit_ids=config.AGENT_ORG_UNIT_IDS)
    history = []
    questions = [" ".join(sys.argv[1:])] if len(sys.argv) > 1 else None
    print(f"Care data agent (data as of {agent.ctx.as_of}). Ctrl+C to quit.\n")
    while True:
        try:
            question = questions.pop(0) if questions is not None else input("you> ").strip()
        except (EOFError, KeyboardInterrupt, IndexError):
            break
        if not question:
            continue
        result = agent.ask(question, history)
        tools = ", ".join(t["tool"] for t in result.tool_calls) or "none"
        print(f"\n{result.answer}\n\n  [tools: {tools}]")
        if result.removed_citations:
            print(f"  [removed unverified citations: {', '.join(result.removed_citations)}]")
        print()
        history += [{"role": "user", "content": question}, {"role": "assistant", "content": result.answer}]
        if questions is not None and not questions:
            break


if __name__ == "__main__":
    main()
