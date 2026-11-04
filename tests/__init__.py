"""
Test package for erp-langgraph.
Sets offline testing environment flag to keep unit tests fast, deterministic,
and free from external LLM network dependencies.
"""
import os

os.environ["FASTPRINT_TEST_ENV"] = "true"
