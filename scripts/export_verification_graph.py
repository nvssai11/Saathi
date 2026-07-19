from unittest.mock import AsyncMock, MagicMock

from langgraph.checkpoint.memory import MemorySaver

from agents.verification.agent import VerificationAgent

FAN_OUT_EDGE = "\tmodel -.-> execute_tool;\n"


def main() -> None:
    agent = VerificationAgent(
        client=MagicMock(),
        order_repo=AsyncMock(),
        trust_repo=AsyncMock(),
        verification_repo=AsyncMock(),
        checkpointer=MemorySaver(),
    )
    mermaid = agent._graph.get_graph().draw_mermaid()
    lines = mermaid.splitlines(keepends=True)
    insert_at = next(i for i, line in enumerate(lines) if "__start__ -->" in line) + 1
    lines.insert(insert_at, FAN_OUT_EDGE)
    print("".join(lines))


if __name__ == "__main__":
    main()
