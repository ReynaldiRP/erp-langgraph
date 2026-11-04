"""
Interactive CLI Runner for FastPrint ERP LangGraph Agent.
Uses the 5-node StateGraph pipeline (router -> extractor -> tool_node -> verifier -> synthesizer).
Run with: python cli.py
"""
import sys
import uuid
from typing import Optional

from app.core.config import settings
from app.graph.workflow import run_workflow_sync


def main():
    print("=" * 68)
    print("🤖 FastPrint ERP — LangGraph StateGraph Assistant CLI")
    print("=" * 68)

    provider = settings.MODEL_PROVIDER
    model_name = settings.OPENROUTER_MODEL if provider == "openrouter" else settings.OPENAI_MODEL
    print(f"• Pipeline     : StateGraph 5-Node (Router -> Extractor -> Tool -> Verifier -> Synthesizer)")
    print(f"• LLM Provider : {provider.upper()} ({model_name})")
    print(f"• ERP Base URL : {settings.ERP_BASE_URL}")
    print(f"• Default Co.  : ID {settings.DEFAULT_COMPANY_ID}")
    print("• Perintah     : 'exit', 'quit', 'q'   -> Keluar dari sesi")
    print("                 'new', 'reset'         -> Mulai thread percakapan baru")
    print("                 'co <id>'              -> Ubah Company ID aktif")
    print("                 'token <jwt>'          -> Set user token JWT (atau 'token clear')")
    print("                 'verbose'              -> Toggle rincian eksekusi pipeline")
    print("-" * 68)

    current_company_id = settings.DEFAULT_COMPANY_ID
    current_token: Optional[str] = settings.ERP_API_TOKEN
    session_id = f"cli_{uuid.uuid4().hex[:8]}"
    verbose = False

    while True:
        try:
            prompt_label = f"\nUser [Co:{current_company_id}] > "
            user_input = input(prompt_label).strip()
            if not user_input:
                continue

            cmd_lower = user_input.lower()
            if cmd_lower in ("exit", "quit", "q"):
                print("\nSampai jumpa! Sesi CLI diakhiri.")
                break

            if cmd_lower in ("new", "reset"):
                session_id = f"cli_{uuid.uuid4().hex[:8]}"
                print(f"\n✨ Sesi percakapan di-reset. Thread ID baru: {session_id}")
                continue

            if cmd_lower == "verbose":
                verbose = not verbose
                print(f"\n⚙️ Mode Verbose: {'AKTIF' if verbose else 'NONAKTIF'}")
                continue

            if cmd_lower.startswith("company ") or cmd_lower.startswith("co "):
                parts = user_input.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    current_company_id = int(parts[1])
                    print(f"\n🏢 Active Company ID diubah ke: {current_company_id}")
                else:
                    print("\n⚠️ Format tidak valid. Gunakan: co <id> (contoh: co 2)")
                continue

            if cmd_lower.startswith("token "):
                token_val = user_input[6:].strip()
                if token_val.lower() == "clear":
                    current_token = None
                    print("\n🔑 User token dikosongkan.")
                else:
                    current_token = token_val
                    print(f"\n🔑 User token diset ({current_token[:15]}...).")
                continue

            print("\nMemproses alur StateGraph...", flush=True)

            final_state = run_workflow_sync(
                user_query=user_input,
                user_token=current_token,
                company_id=current_company_id,
                thread_id=session_id,
            )

            domain = final_state.get("detected_domain")
            tool = final_state.get("detected_tool")
            status = final_state.get("verification_status")
            retries = final_state.get("retry_count", 0)
            params = final_state.get("extracted_params") or {}
            answer = (
                final_state.get("final_answer")
                or final_state.get("error_message")
                or "Tidak ada respons yang dihasilkan."
            )

            if verbose:
                print("\n--- [Rincian Eksekusi Pipeline StateGraph] ---")
                print(f"• Domain        : {domain}")
                print(f"• Target Tool   : {tool or '(None / Guardrail)'}")
                print(f"• Filter Params : {params}")
                print(f"• Status Verif  : {status} (Retries: {retries})")
                if final_state.get("tool_result"):
                    tr = final_state["tool_result"]
                    records = (
                        tr.get("total_count", len(tr.get("entries", [])))
                        if isinstance(tr, dict)
                        else len(tr)
                        if isinstance(tr, list)
                        else 1
                    )
                    print(f"• Jumlah Data   : {records}")
                print("----------------------------------------------\n")
            else:
                badge = f"[Route: {domain} -> {tool or 'guardrail'} | Status: {status or 'DONE'}]"
                print(f"  {badge}")

            print(f"\nAssistant:\n{answer}\n")

        except (KeyboardInterrupt, EOFError):
            print("\nSesi dihentikan.")
            break
        except Exception as loop_err:
            print(f"\n❌ Kendala saat menjalankan workflow: {loop_err}")


if __name__ == "__main__":
    main()
