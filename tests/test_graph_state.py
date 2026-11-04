"""
Unit tests for LangGraph AgentState and StateGraph cycle compatibility (TASK-4.1).
Validates schema definition, helper initialization, multi-company scoping,
message reducer integration (add_messages), and StateGraph execution cycle (DoD).
"""
import unittest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import StateGraph, START, END

from app.graph.state import (
    AgentState,
    VerificationStatus,
    create_initial_state,
)
from app.core.config import settings


class TestAgentStateCreation(unittest.TestCase):
    """Test suite for AgentState schema definition and initial state constructor."""

    def test_create_initial_state_defaults(self):
        """create_initial_state should populate standard defaults and wrap query in HumanMessage."""
        state = create_initial_state("Berapa saldo kas kecil?")

        self.assertEqual(state["user_query"], "Berapa saldo kas kecil?")
        self.assertIsNone(state["user_token"])
        self.assertEqual(state["company_id"], settings.DEFAULT_COMPANY_ID)
        self.assertEqual(state["retry_count"], 0)
        self.assertIsNone(state["detected_domain"])
        self.assertIsNone(state["detected_tool"])
        self.assertIsNone(state["extracted_params"])
        self.assertIsNone(state["tool_result"])
        self.assertIsNone(state["verification_status"])
        self.assertIsNone(state["final_answer"])
        self.assertIsNone(state["error_message"])

        # Check messages list
        self.assertEqual(len(state["messages"]), 1)
        self.assertIsInstance(state["messages"][0], HumanMessage)
        self.assertEqual(state["messages"][0].content, "Berapa saldo kas kecil?")

    def test_create_initial_state_with_explicit_parameters(self):
        """create_initial_state respects custom user_token, company_id, and pre-existing messages."""
        existing_msgs = [
            SystemMessage(content="You are an ERP assistant"),
            HumanMessage(content="Cek stok tinta"),
        ]
        state = create_initial_state(
            user_query="Cek stok tinta",
            user_token="test-jwt-token-xyz",
            company_id=3,
            messages=existing_msgs,
        )

        self.assertEqual(state["user_token"], "test-jwt-token-xyz")
        self.assertEqual(state["company_id"], 3)
        self.assertEqual(len(state["messages"]), 2)
        self.assertIsInstance(state["messages"][0], SystemMessage)
        self.assertIsInstance(state["messages"][1], HumanMessage)

    def test_partial_state_dict_validity(self):
        """AgentState supports partial dictionary representations (total=False)."""
        partial_state: AgentState = {
            "detected_domain": "accounting",
            "detected_tool": "get_journal_entries",
        }
        self.assertEqual(partial_state["detected_domain"], "accounting")
        self.assertEqual(partial_state["detected_tool"], "get_journal_entries")


class TestLangGraphCycleCompatibility(unittest.TestCase):
    """Test suite verifying AgentState compatibility with LangGraph StateGraph cycles (DoD)."""

    def test_stategraph_compilation_and_execution_cycle(self):
        """Definition of Done: StateGraph(AgentState) compiles and executes through multiple nodes."""

        # Define 3 sequential pipeline nodes returning partial state updates
        def router_node(state: AgentState) -> dict:
            return {
                "detected_domain": "accounting",
                "detected_tool": "get_account_balance",
                "extracted_params": {"account_code": "111100"},
            }

        def tool_mock_node(state: AgentState) -> dict:
            return {
                "tool_result": {
                    "account_code": state.get("extracted_params", {}).get("account_code"),
                    "net_balance": 1500000.0,
                },
                "verification_status": "VALID",
            }

        def answer_node(state: AgentState) -> dict:
            ans = f"Saldo akun {state['tool_result']['account_code']} adalah Rp {state['tool_result']['net_balance']:,.0f}"
            return {
                "final_answer": ans,
                "messages": [AIMessage(content=ans)],
            }

        # Build LangGraph workflow using AgentState
        workflow = StateGraph(AgentState)
        workflow.add_node("router", router_node)
        workflow.add_node("tool_exec", tool_mock_node)
        workflow.add_node("present_answer", answer_node)

        workflow.add_edge(START, "router")
        workflow.add_edge("router", "tool_exec")
        workflow.add_edge("tool_exec", "present_answer")
        workflow.add_edge("present_answer", END)

        app = workflow.compile()

        # Execute graph with initial state
        initial_state = create_initial_state("Berapa saldo kas 111100?", company_id=2)
        result = app.invoke(initial_state)

        # Assert full state propagation across nodes
        self.assertEqual(result["user_query"], "Berapa saldo kas 111100?")
        self.assertEqual(result["company_id"], 2)
        self.assertEqual(result["detected_domain"], "accounting")
        self.assertEqual(result["detected_tool"], "get_account_balance")
        self.assertEqual(result["extracted_params"], {"account_code": "111100"})
        self.assertEqual(result["tool_result"]["net_balance"], 1500000.0)
        self.assertEqual(result["verification_status"], "VALID")
        self.assertIn("Saldo akun 111100", result["final_answer"])

        # Assert messages reducer appended the AIMessage
        self.assertEqual(len(result["messages"]), 2)
        self.assertIsInstance(result["messages"][0], HumanMessage)
        self.assertIsInstance(result["messages"][1], AIMessage)

    def test_conditional_routing_on_verification_status(self):
        """Verifies conditional routing in StateGraph using verification_status."""

        def evaluate_node(state: AgentState) -> dict:
            return {"verification_status": "RETRY", "retry_count": state.get("retry_count", 0) + 1}

        def retry_node(state: AgentState) -> dict:
            return {"error_message": "Needs clarification"}

        def success_node(state: AgentState) -> dict:
            return {"final_answer": "Success"}

        def route_condition(state: AgentState) -> str:
            if state.get("verification_status") == "VALID":
                return "success"
            return "retry"

        workflow = StateGraph(AgentState)
        workflow.add_node("eval", evaluate_node)
        workflow.add_node("retry", retry_node)
        workflow.add_node("success", success_node)

        workflow.add_edge(START, "eval")
        workflow.add_conditional_edges("eval", route_condition, {"success": "success", "retry": "retry"})
        workflow.add_edge("retry", END)
        workflow.add_edge("success", END)

        app = workflow.compile()
        result = app.invoke(create_initial_state("Query retry test"))

        self.assertEqual(result["verification_status"], "RETRY")
        self.assertEqual(result["retry_count"], 1)
        self.assertEqual(result["error_message"], "Needs clarification")
        self.assertIsNone(result["final_answer"])


if __name__ == "__main__":
    unittest.main()
