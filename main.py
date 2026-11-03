"""
Interactive CLI Runner for FastPrint ERP LangGraph Agent.
Run with: python main.py
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Ensure current folder is in sys.path
current_dir = Path(__file__).resolve().parent
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

# Load local .env file
load_dotenv(current_dir / ".env")

from client import ERPClient
from graph import create_erp_graph
from langchain_core.messages import HumanMessage


def main():
    print("=" * 65)
    print("🤖 FastPrint ERP — LangGraph Assistant CLI")
    print("=" * 65)

    provider = os.getenv("MODEL_PROVIDER", "openrouter")
    print(f"• LLM Provider : {provider.upper()}")
    print(f"• ERP Base URL : {os.getenv('ERP_BASE_URL', 'http://127.0.0.1:8000')}")
    print(f"• Default Co.  : ID {os.getenv('DEFAULT_COMPANY_ID', '1')}")
    print("• Commands     : 'exit' or 'quit' to end session.")
    print("-" * 65)

    use_dynamic = os.getenv("USE_DYNAMIC_TOOLS", "false").lower() == "true"

    try:
        client = ERPClient()
        graph, tools = create_erp_graph(
            client=client,
            use_dynamic_tools=use_dynamic,
            model_provider=provider,
        )
        print(f"✓ Loaded {len(tools)} tools ({'Dynamic Discovery' if use_dynamic else 'Static Typed'}):")
        for t in tools:
            print(f"  - {t.name}")
        print("-" * 65)
    except Exception as init_err:
        print(f"\n❌ Failed to initialize agent graph: {init_err}")
        print("Please check your .env configuration and ERP server status.")
        sys.exit(1)

    thread_config = {"configurable": {"thread_id": "cli_session_1"}}

    while True:
        try:
            user_input = input("\nUser > ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "q"):
                print("\nGoodbye!")
                break

            print("\nThinking...", flush=True)

            inputs = {"messages": [HumanMessage(content=user_input)]}
            for event in graph.stream(inputs, thread_config, stream_mode="values"):
                last_msg = event["messages"][-1]

            if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                for tc in last_msg.tool_calls:
                    print(f"  [Tool Invoked] {tc['name']}({tc['args']})")

            print(f"\nAssistant:\n{last_msg.content}\n")

        except KeyboardInterrupt:
            print("\nSession interrupted.")
            break
        except Exception as loop_err:
            print(f"\n❌ Error during execution: {loop_err}")


if __name__ == "__main__":
    main()
