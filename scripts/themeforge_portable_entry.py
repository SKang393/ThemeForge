"""Portable wrapper; normal launch delegates to the ordinary app unchanged."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
import time

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _source_cards_smoke(app, dialog, source_path, output):
    """Exercise actual source-card windows while preserving conceptual drafts."""
    from copy import deepcopy
    from themeforge.source_cards import validate_cards
    from themeforge.source_card_dialog import MAX_FILE_BYTES, SourceCardDialog
    source_path = _absolute_local(source_path, "Source-card JSON")
    if source_path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Source-card input exceeds the UI file bound")
    raw = source_path.read_bytes()
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("Source-card input grew beyond the UI file bound")
    cards = validate_cards(raw)
    before = deepcopy((app.documents, app.result, dialog.documents, dialog.result, dialog._codes, dialog.reviewed.get()))
    original_factory = dialog._client_factory
    attempts = []
    def no_client(*args, **kwargs):
        attempts.append(True)
        raise RuntimeError("Source-only review must not construct a model client")
    dialog._client_factory = no_client
    windows = []

    def inspect(window):
        assert isinstance(window, SourceCardDialog)
        window.withdraw()
        app.update()
        assert window.state() == "withdrawn" and window.cards == cards
        expected = {card["card_id"]: card for card in cards["cards"]}
        assert set(window.card_tree.get_children()) == set(expected)
        sources = {doc["name"]: doc["text"] for doc in cards["source_documents"]}
        checks = []
        for card_id, card in expected.items():
            assert tuple(window.card_tree.item(card_id, "values")) == (card["source_name"], card["speaker"])
            window.card_tree.selection_set(card_id)
            window._selection_changed()
            app.update()
            detail = window.detail.get("1.0", "end-1c")
            source = sources[card["source_name"]]
            assert sha256(source.encode("utf-8")).hexdigest() == card["source_hash"]
            assert source[card["source_start"]:card["source_end"]] == card["text"]
            for value in (card_id, card["origin"], card["source_name"], card["speaker"], card["source_hash"], card["text"],
                          f"Characters {card['source_start']}:{card['source_end']}"):
                assert value in detail
            for span in card["support_spans"]:
                assert source[span["source_start"]:span["source_end"]] == span["text"]
                assert span["source_hash"] == card["source_hash"] and span["unit_id"] == card["unit_id"]
                for value in (span["span_id"], span["text"], f"Characters {span['source_start']}:{span['source_end']}"):
                    assert value in detail
            context = card["context"]
            assert source[context["source_start"]:context["source_end"]] == context["text"]
            for value in (context["text"], context["reference_context"],
                          f"Characters {context['source_start']}:{context['source_end']}",
                          "Context, not additional supporting evidence"):
                assert value in detail
            checks.append({"card_id": card_id, "span_count": len(card["support_spans"]),
                           "exact_source_hash_text_offsets_context": True,
                           "visible_detail_sha256": sha256(detail.encode("utf-8")).hexdigest()})
        return checks

    try:
        window = dialog.load_source_cards(source_path)
        assert window is not None, dialog.status.get()
        windows.append(window)
        checked = inspect(window)
        exports = {}
        for format in ("json", "html"):
            target = output / ("source-cards." + format)
            assert window.export(format, target), window.status.get()
            exports[format] = {"path": str(target), "bytes": target.stat().st_size, "sha256": _file_sha256(target)}
        reopened = dialog.load_source_cards(output / "source-cards.json")
        assert reopened is not None, dialog.status.get()
        windows.append(reopened)
        assert inspect(reopened) == checked
        assert validate_cards((output / "source-cards.json").read_bytes()) == cards
        assert source_path.read_bytes() == raw
        assert (app.documents, app.result, dialog.documents, dialog.result, dialog._codes, dialog.reviewed.get()) == before
        assert dialog._thread is None and dialog._client is None and not attempts
        assert app.state() == dialog.state() == "withdrawn"
        return {"input_path": str(source_path), "input_sha256": sha256(raw).hexdigest(),
                "input_unchanged": True, "card_count": len(checked), "card_checks": checked,
                "actual_source_card_UI": True, "UI_JSON_reopen_exact": True,
                "conceptual_draft_and_review_unchanged": True, "client_construction_attempts": len(attempts),
                "new_model_calls": 0, "exports": exports, "interpretation_or_human_coding_created": False}
    finally:
        dialog._client_factory = original_factory
        for window in windows:
            if window.winfo_exists():
                window.destroy()


def portable_smoke(output, draft=None, *, source_cards=None):
    """Withdrawn real Tk startup/export check; no new or simulated model calls."""
    output = Path(output)
    if not output.is_absolute() or output.exists() or str(output).startswith(("\\\\", "//")):
        raise ValueError("Packaged smoke requires a fresh absolute local output directory")
    output.mkdir(parents=True, exist_ok=False)
    report = {"status": "started", "frozen_executable": bool(getattr(sys, "frozen", False)),
              "executable": sys.executable, "model_calls": 0, "downloads": 0,
              "semantic_validation": False, "synthetic_lexical_input": True,
              "draft_bundled": False, "new_evidence_revisions": False}
    app = dialog = None
    started = time.monotonic()
    try:
        import subprocess
        from themeforge.app import ThemeForgeApp
        from themeforge.analysis_types import TranscriptDocument
        from themeforge.exports import export_json, export_markdown
        from themeforge.discovery_exports import export_discovery_html

        def no_process(*args, **kwargs):
            raise RuntimeError("Subprocess/model launch is prohibited in packaging smoke")
        subprocess.Popen = no_process

        class WithdrawnApp(ThemeForgeApp):
            def _build_layout(self):
                self.withdraw()
                super()._build_layout()

        # Fail instead of blocking on an unexpected modal error.
        from tkinter import messagebox
        def no_modal(*args, **kwargs):
            raise RuntimeError("Unexpected modal during packaging smoke: " + str(args[:2]))
        messagebox.showerror = messagebox.showinfo = messagebox.showwarning = no_modal
        app = WithdrawnApp()
        app.update()
        assert app.state() == "withdrawn" and not app.documents
        report["hidden_empty_project_startup"] = True
        if draft is not None:
            draft = Path(draft)
            if not draft.is_absolute() or str(draft).startswith(("\\\\", "//")) or draft.stat().st_size > 16 * 1024**2:
                raise ValueError("Draft must be a bounded absolute local JSON path")
            original_raw = draft.read_bytes()
            original_hash = sha256(original_raw).hexdigest()
            original = json.loads(original_raw.decode("utf-8-sig"))
            from themeforge.local_codebook import has_completed_definition, validate_result_sources
            validate_result_sources(original)
            dialog = app.open_local_codebook()
            dialog.withdraw()
            dialog._client_factory = no_process
            assert dialog.load_result(draft), dialog.status.get()
            app.update()
            assert not app.documents and not dialog.documents and dialog._thread is None
            assert dialog.result == original and not dialog.reviewed.get()
            assert len(dialog.code_tree.get_children()) == len(original["codes"])
            for code_id in dialog.code_tree.get_children():
                dialog.code_tree.selection_set(code_id)
                dialog._selection_changed()
                app.update()
                assert dialog.detail.get("1.0", "end-1c").strip()
            role_code = next((code for code in original["codes"]
                              if has_completed_definition(code) and code.get("evidence_role_origins")), None)
            if role_code is None:
                role_code = next((code for code in original["codes"] if has_completed_definition(code)), None)
            if role_code is None:
                raise ValueError("Role-editor smoke requires a code with a completed definition")
            dialog.code_tree.selection_set(role_code["id"])
            dialog._selection_changed()
            app.update()
            assert str(dialog.evidence_roles_button["state"]) == "normal"
            role_window = dialog.open_evidence_roles_review()
            assert role_window is not None
            role_window.withdraw()
            app.update()
            assert role_window.state() == "withdrawn"
            spans = {sid: span for sid, span in original["registry"].items()
                     if span["unit_id"] == role_code["unit_id"]}
            rows = role_window.span_tree.get_children()
            assert set(rows) == set(spans)
            authors = role_window.author_label.cget("text")
            origins = {role: role_code.get("evidence_role_origins", {}).get(
                role, role_code.get("evidence_selection_origin", "local_model") if role == "support" else "local_model")
                for role in ("support", "qualifications", "counterexamples")}
            for role, origin in origins.items():
                assert f"{role}: {origin}" in authors
            assert "Changed roles here: researcher" in authors
            assert role_window.role_choice.get() == "support"
            assert str(role_window.assign_button["state"]) != "disabled"
            roles = {sid: "unassigned" for sid in spans}
            for sid in role_code["support_span_ids"]:
                roles[sid] = "support"
            for role in ("qualifications", "counterexamples"):
                for entry in role_code[role]:
                    roles[next(sid for sid, span in spans.items() if span == entry)] = role
            for sid in rows:
                span = spans[sid]
                assert tuple(role_window.span_tree.item(sid, "values")) == (roles[sid], sid, span["text"])
                role_window.span_tree.selection_set(sid)
                app.update()
                preview = role_window.preview.get("1.0", "end-1c")
                for exact in (sid, span["source_name"], span["speaker"], span["source_hash"], span["text"],
                              f"Characters {span['source_start']}:{span['source_end']}"):
                    assert exact in preview
            role_window.destroy()  # Cancel inspection without Assign or Apply.
            app.update()
            assert dialog.result == original and dialog._thread is None and not dialog.reviewed.get()
            report["read_only_role_editor"] = {
                "code_id": role_code["id"], "same_unit_span_rows_checked": len(rows),
                "exact_role_text_hash_and_offset_previews": True, "current_role_authors": origins,
                "explicit_role_origins_present": bool(role_code.get("evidence_role_origins")),
                "closed_without_apply": True, "result_and_review_history_unchanged": True,
                "human_revisions_created": 0}
            for format in ("json", "html"):
                assert dialog.export(format, output / ("draft-export." + format)), dialog.status.get()
            exported = json.loads((output / "draft-export.json").read_text(encoding="utf-8-sig"))
            assert exported == original
            assert sha256(draft.read_bytes()).hexdigest() == original_hash
            assert app.state() == dialog.state() == "withdrawn"
            report.update(draft_path=str(draft), draft_file_sha256=original_hash,
                standalone_draft_open_export=True, draft_original_unchanged=True,
                draft_codes=len(original["codes"]), raw_model_calls_preserved=len(original["calls"]),
                revision_history_events_preserved=len(original.get("review_history", [])),
                source_and_registry_preserved=True, draft_review_status=original.get("review_status"))
            if source_cards is None:
                dialog.request_close()
                dialog = None
        if source_cards is not None:
            if dialog is None:
                dialog = app.open_local_codebook()
                dialog.withdraw()
                dialog._client_factory = no_process
            report["source_cards_smoke"] = _source_cards_smoke(app, dialog, source_cards, output)
            dialog.request_close()
            dialog = None
        app.documents = [TranscriptDocument("synthetic-packaging-smoke.txt",
            "P1: I keep a spare copy on a different drive because a failed disk should not destroy my work.\n"
            "P2: I store another copy on a separate machine so I can recover my work after a disk fails.")]
        app.semantic_mode.set("TF-IDF")
        app.analyze()
        app.update()
        assert app.result is not None and app.result.quote_count > 0
        for filename, content in (("lexical-export.json", export_json(app.result)),
                                  ("lexical-export.md", export_markdown(app.result)),
                                  ("lexical-export.html", export_discovery_html(app.result))):
            assert content.strip()
            with (output / filename).open("x", encoding="utf-8") as stream:
                stream.write(content)
        json.loads((output / "lexical-export.json").read_text(encoding="utf-8"))
        report.update(status="completed", lexical_analysis_and_exports=True,
                      lexical_quote_count=app.result.quote_count, root_state=app.state(),
                      ui_widget_count=len(app.winfo_children()))
        return_code = 0
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
        return_code = 1
    finally:
        if dialog is not None:
            dialog.request_close()
        if app is not None:
            app.destroy()
        report["wall_seconds"] = time.monotonic() - started
        report["outputs"] = {p.name: {"bytes": p.stat().st_size, "sha256": sha256(p.read_bytes()).hexdigest()}
                             for p in output.iterdir() if p.is_file()}
        with (output / "packaged-smoke.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
    return return_code


def _absolute_local(value, label):
    path = Path(value)
    if not path.is_absolute() or str(path).startswith(("\\\\", "//")):
        raise ValueError(f"{label} must be an absolute local path")
    return path


def _file_sha256(path):
    value = sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024**2), b""):
            value.update(chunk)
    return value.hexdigest()


def _inference_inputs(packet_path, profile_path, profile_sha256):
    """Validate bounded inputs without launching a process or importing user code."""
    import re
    from themeforge.local_codebook import validate_prepared
    from themeforge.local_inference import LocalInferenceConfig, preflight, staged_inference_limits
    packet_path = _absolute_local(packet_path, "Packet")
    profile_path = _absolute_local(profile_path, "Profile")
    if packet_path.stat().st_size > 16 * 1024**2 or profile_path.stat().st_size > 65536:
        raise ValueError("Packet/profile exceeds its bounded input size")
    if not isinstance(profile_sha256, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", profile_sha256):
        raise ValueError("An explicit profile SHA-256 is required")
    if _file_sha256(profile_path) != profile_sha256.lower():
        raise ValueError("Profile SHA-256 mismatch")
    packet = json.loads(packet_path.read_text(encoding="utf-8-sig"))
    validate_prepared(packet)
    if len(packet["selected_unit_ids"]) != 1 or len(packet["decision_requests"]) != 1:
        raise ValueError("Packaged inference requires a frozen packet with exactly one selected target")
    if packet["decision_requests"][0]["unit_id"] != packet["selected_unit_ids"][0]:
        raise ValueError("The single target and decision request must agree")
    profile = json.loads(profile_path.read_text(encoding="utf-8-sig"))
    limits = staged_inference_limits(profile)
    if (limits.threads != 2 or limits.context_tokens != 4096 or limits.output_tokens != 512
            or limits.memory_limit_bytes is None or limits.memory_limit_bytes > 5 * 1024**3
            or limits.wall_seconds > 180 or limits.startup_seconds > 90):
        raise ValueError("Packaged inference requires CPU2/context4096/output512, <=5GiB server tripwire, <=180s/request and <=90s startup")
    manifest_path = _absolute_local(profile["runtime_file_manifest"], "Runtime manifest")
    if manifest_path.stat().st_size > 1024**2:
        raise ValueError("Runtime manifest exceeds size bound")
    if _file_sha256(manifest_path) != profile["runtime_file_manifest_sha256"]:
        raise ValueError("Runtime manifest SHA-256 mismatch")
    runtime = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if not isinstance(runtime, list) or not runtime or len(runtime) > 1000:
        raise ValueError("Runtime manifest must contain bounded file records")
    for entry in runtime:
        path = _absolute_local(entry["path"], "Runtime file")
        if _file_sha256(path) != entry["sha256"] or path.stat().st_size != entry["bytes"]:
            raise ValueError("Runtime payload identity mismatch")
    config = LocalInferenceConfig(**{key: profile[key] for key in (
        "executable_path", "executable_sha256", "model_path", "model_sha256")}, limits=limits)
    # This hashes the model/executable and fixes CPU/offline/loopback/cache flags;
    # it does not launch a server or send any HTTP request.
    plan = preflight(config)
    return packet, config, plan, len(runtime)


class _FourCallClient:
    def __init__(self, client, journal_directory=None):
        self.client, self.calls, self.blocked_calls = client, 0, 0
        self.journal_directory = journal_directory

    def _append(self, name, value):
        if self.journal_directory is not None:
            with (self.journal_directory / name).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")

    def start(self, cancel=None):
        return self.client.start(cancel)

    def generate(self, messages, schema, cancel=None):
        if self.calls >= 4:
            self.blocked_calls += 1
            raise RuntimeError("Packaged four-call budget exhausted before another request")
        self.calls += 1
        self._append("request-journal.jsonl", {"call": self.calls, "messages": messages, "schema": schema})
        try:
            response = self.client.generate(messages, schema, cancel)
            self._append("response-journal.jsonl", {"call": self.calls, "response": response})
            return response
        except BaseException as error:
            self._append("response-journal.jsonl", {"call": self.calls, "failure": f"{type(error).__name__}: {error}"})
            raise

    def diagnostics(self):
        return self.client.diagnostics()

    def close(self):
        return self.client.close()


def portable_inference(output, packet_path, profile_path, profile_sha256):
    """Explicit single-target product-backend execution, never used by review smoke.

    External supervision owns the 900s/6GiB tree/Job envelope. The unchanged local
    client owns <=180s per request and <=5GiB sampled server memory. No hard OS
    containment is claimed by this entry point itself.
    """
    output = _absolute_local(output, "Output")
    if output.exists():
        raise FileExistsError("Inference output must be fresh; existing artifacts are preserved")
    output.mkdir(parents=True, exist_ok=False)
    report = {"status": "validating", "frozen_executable": bool(getattr(sys, "frozen", False)),
              "execution_requested_explicitly": True, "backend": "product_run_codebook",
              "max_selected_targets": 1, "max_model_calls": 4, "training": False,
              "semantic_quality": "not_human_validated", "downloads": 0,
              "external_supervision_required": True, "hard_OS_memory_cap_claimed": False}
    started = time.monotonic()
    proxy = None
    result = None
    code = 1
    try:
        from themeforge.local_codebook import run_codebook, validate_result_sources, export_codebook_html
        from themeforge.local_inference import LlamaServerClient
        packet, config, plan, runtime_count = _inference_inputs(packet_path, profile_path, profile_sha256)
        packet_file = _absolute_local(packet_path, "Packet")
        profile_file = _absolute_local(profile_path, "Profile")
        report.update(packet_path=str(packet_file), packet_file_sha256=_file_sha256(packet_file),
                      packet_sha256=packet["packet_sha256"], profile_path=str(profile_file),
                      profile_sha256=profile_sha256.lower(), selected_unit_ids=packet["selected_unit_ids"],
                      runtime_files_verified=runtime_count, preflight_plan=plan)
        with (output / "input-packet.json").open("x", encoding="utf-8") as stream:
            json.dump(packet, stream, ensure_ascii=False, indent=2, allow_nan=False)
        proxy = _FourCallClient(LlamaServerClient(config), output)
        def progress(event):
            with (output / "progress.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(dict(event, observed_utc=time.time()), ensure_ascii=False, allow_nan=False) + "\n")
        result = run_codebook([], proxy, prepared=packet, on_progress=progress)
        validate_result_sources(result)
        with (output / "candidate-codebook.json").open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        with (output / "candidate-codebook.html").open("x", encoding="utf-8") as stream:
            stream.write(export_codebook_html(result))
        if _file_sha256(packet_file) != report["packet_file_sha256"] or _file_sha256(profile_file) != report["profile_sha256"]:
            raise ValueError("Input packet/profile changed during packaged execution")
        report.update(status="completed" if result["status"] in {"completed", "completed_with_rejections"} else "backend_runtime_failure",
                      result_status=result["status"], result_sha256=result["result_sha256"],
                      code_count=len(result["codes"]), review_status=result["review_status"],
                      contract_rejections_are_not_semantic_accuracy=True)
        code = 0 if report["status"] == "completed" else 1
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
        if result is not None and not (output / "candidate-codebook.json").exists():
            with (output / "observed-result.json").open("x", encoding="utf-8") as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    finally:
        if proxy is not None:
            try:
                proxy.close()
                report["client_diagnostics"] = proxy.diagnostics()
                report["owned_server_closed"] = report["client_diagnostics"].get("owned_process_closed", False)
            except Exception as error:
                report.update(status="cleanup_failed", cleanup_error=f"{type(error).__name__}: {error}")
                code = 1
            report.update(model_calls_started=proxy.calls, blocked_over_budget_calls=proxy.blocked_calls)
            if not report.get("owned_server_closed", False):
                report["status"] = "cleanup_failed"
                code = 1
        else:
            report.update(model_calls_started=0, owned_server_closed=True)
        report["wall_seconds"] = time.monotonic() - started
        report["outputs"] = {p.name: {"bytes": p.stat().st_size, "sha256": _file_sha256(p)}
                             for p in output.iterdir() if p.is_file()}
        with (output / "packaged-inference.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
    return code


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == '--main-review-offline-check':
        from themeforge.main_review_qa import main as review_main
        return review_main(argv[1:])
    if argv and argv[0] == '--candidate-review-smoke':
        from themeforge.candidate_review_qa import main as candidate_review_main
        return candidate_review_main(argv[1:])
    if argv and argv[0] == '--study-smoke':
        from themeforge.study_qa import main as study_main
        return study_main(argv[1:])
    if argv and argv[0] == '--study-native-smoke':
        from themeforge.study_native_qa import main as native_study_main
        return native_study_main(argv[1:])
    if argv and argv[0] == '--study-encoder-smoke':
        from themeforge.study_encoder_qa import main as encoder_study_main
        return encoder_study_main(argv[1:])
    if argv and argv[0] == '--study-resume-smoke':
        from themeforge.study_resume_qa import main as resume_study_main
        return resume_study_main(argv[1:])
    if argv and argv[0] == '--study-boundary-smoke':
        from themeforge.study_boundary_qa import main as boundary_main
        return boundary_main(argv[1:])
    if argv and argv[0] == '--study-boundary-native-smoke':
        from themeforge.study_boundary_native_qa import main as boundary_native_main
        return boundary_native_main(argv[1:])
    if argv and argv[0] == '--study-recovered-graph-smoke':
        from themeforge.study_recovered_graph_qa import main as recovered_graph_main
        return recovered_graph_main(argv[1:])
    if argv and argv[0] == '--study-pair-review-smoke':
        from themeforge.study_pair_review_qa import main as pair_review_main
        return pair_review_main(argv[1:])
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == '--neighbor-rules':
        from themeforge.neighbor_policies import main as neighbor_rules_main
        return neighbor_rules_main(argv[1:])
    if argv and argv[0] in {'--neighbor-worker', '--neighbor-compare'}:
        from themeforge.neighbor_runner import main as neighbor_main
        return neighbor_main((['--worker'] if argv[0] == '--neighbor-worker' else []) + argv[1:])
    if argv and argv[0] == '--neighbor-review-smoke':
        from themeforge.neighbor_qa import main as neighbor_qa_main
        return neighbor_qa_main(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portable-smoke", metavar="FRESH_OUTPUT_DIRECTORY")
    parser.add_argument("--category-review-smoke", metavar="FRESH_OUTPUT_DIRECTORY",
                        help="Hidden native category/no_group UI reopen/export only; requires --draft")
    parser.add_argument("--reviewed-grouping-smoke", metavar="FRESH_OUTPUT_DIRECTORY",
                        help="Hidden explicit AI-reviewed category editor apply/export/reopen; no model")
    parser.add_argument("--category-input", help="Absolute bounded AI-authored category specification JSON")
    parser.add_argument("--draft", help="Optional existing frozen draft for standalone open/export, never bundled")
    parser.add_argument("--source-cards", help="Optional absolute source-card JSON for read-only UI/export/reopen smoke")
    parser.add_argument("--portable-inference", action="store_true", help="Explicit bounded single-target inference; requires external resource supervision")
    parser.add_argument("--packet")
    parser.add_argument("--profile")
    parser.add_argument("--profile-sha256")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    inference_args = (args.packet, args.profile, args.profile_sha256, args.out)
    if args.reviewed_grouping_smoke:
        if not args.draft or not args.category_input or args.portable_smoke or args.category_review_smoke or args.portable_inference or args.source_cards or any(inference_args):
            parser.error("--reviewed-grouping-smoke requires --draft and --category-input, exclusively")
        from themeforge.reviewed_grouping_qa import reviewed_grouping_smoke
        return reviewed_grouping_smoke(args.reviewed_grouping_smoke, args.draft, args.category_input)
    if args.category_input:
        parser.error("--category-input requires explicit --reviewed-grouping-smoke")
    if args.category_review_smoke:
        if not args.draft or args.portable_smoke or args.portable_inference or args.source_cards or any(inference_args):
            parser.error("--category-review-smoke requires --draft and cannot be combined with other smoke/inference inputs")
        from themeforge.category_review_qa import category_review_smoke
        return category_review_smoke(args.category_review_smoke, args.draft)
    if args.portable_inference:
        if args.portable_smoke or args.draft or args.source_cards or not all(inference_args):
            parser.error("--portable-inference requires --packet, --profile, --profile-sha256 and fresh --out, and cannot be combined with review smoke")
        return portable_inference(args.out, args.packet, args.profile, args.profile_sha256)
    if any(inference_args):
        parser.error("Inference inputs require explicit --portable-inference")
    if args.portable_smoke:
        if args.source_cards:
            return portable_smoke(args.portable_smoke, args.draft, source_cards=args.source_cards)
        return portable_smoke(args.portable_smoke, args.draft)
    if args.draft or args.source_cards:
        parser.error("--draft/--source-cards require --portable-smoke")
    from themeforge.app import main as app_main
    return app_main()


if __name__ == "__main__":
    raise SystemExit(main())
