"""Bounded local comparison runner. Frozen source inputs only; no review answers.

Each backend gets a fresh owned subprocess. Limits are sampled watchdog limits,
not a hardware RAM compatibility claim or an operating-system hard memory cap.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .semantic_neighbors import (MAX_PACKET_BYTES, validate_packet, lexical_vectors,
    build_neighbor_graph, export_graph_json, export_graph_html)
from .sentence_encoder import file_sha256, local_file

WALL_LIMIT_SECONDS = 180
TREE_MEMORY_LIMIT_BYTES = 2 * 1024**3


def _write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def load_packet(path, expected_sha256):
    path = local_file(path)
    if path.stat().st_size > MAX_PACKET_BYTES or file_sha256(path) != expected_sha256:
        raise ValueError("Source packet changed or exceeds its size bound.")
    packet = json.loads(path.read_text(encoding="utf-8-sig"))
    validate_packet(packet)
    return packet


def run_worker(*, packet_path, packet_sha256, profile_path, profile_sha256, backend, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    began = time.perf_counter()
    report = dict(backend=backend, status="started", frozen_executable=bool(getattr(sys, "frozen", False)))
    try:
        packet = load_packet(packet_path, packet_sha256)
        compute_start = time.perf_counter()
        if backend == "lexical_tfidf":
            vectors = lexical_vectors(packet)
            provenance = dict(algorithm="unigram+bigram TF-IDF without stopword removal", input_fields=["node.text"],
                              training=False, network_calls=0, reviewer_kind="none")
        elif backend == "sentence_embeddings":
            from .sentence_encoder import encode_packet
            encoded = encode_packet(packet, profile_path, profile_sha256)
            vectors, provenance = encoded["vectors"], encoded["provenance"]
            _write(out / "encoder-output.json", encoded)
        else:
            raise ValueError("Unknown vector backend; no fallback is permitted.")
        report["vector_seconds"] = time.perf_counter() - compute_start
        graph = build_neighbor_graph(packet, vectors, backend=backend, provenance=provenance)
        with (out / "graph.json").open("x", encoding="utf-8") as stream:
            stream.write(export_graph_json(graph))
        with (out / "graph.html").open("x", encoding="utf-8") as stream:
            stream.write(export_graph_html(graph))
        report.update(status="completed", packet_sha256=packet["packet_sha256"], graph_sha256=graph["graph_sha256"],
                      source_turns=len(packet["nodes"]), candidate_pairs=len(graph["edges"]), isolates=len(graph["isolates"]))
        code = 0
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
        code = 1
    report["worker_seconds"] = time.perf_counter() - began
    _write(out / "worker.json", report)
    return code


def run_comparison(packet_path, packet_sha256, profile_path, profile_sha256, out, *, cancel=None):
    """Run both backends on the same exact packet and retain every raw artifact."""
    import psutil
    packet_path, profile_path = local_file(packet_path), local_file(profile_path)
    load_packet(packet_path, packet_sha256)
    if profile_path.stat().st_size > 128 * 1024 or file_sha256(profile_path) != profile_sha256:
        raise ValueError("Encoder profile hash changed.")
    out = Path(out).resolve()
    if str(out).startswith(("\\\\", "//")):
        raise ValueError("Output must be a local directory.")
    out.mkdir(parents=True, exist_ok=False)
    report = dict(recorded_utc=datetime.now(timezone.utc).isoformat(), status="running",
        packet_path=str(packet_path), packet_file_sha256=packet_sha256, profile_path=str(profile_path),
        profile_sha256=profile_sha256, wall_limit_seconds_per_backend=WALL_LIMIT_SECONDS,
        process_tree_memory_limit_bytes=TREE_MEMORY_LIMIT_BYTES, memory_limit_kind="sampled_not_OS_hard_cap",
        training=False, network_calls=0, model_naming_calls=0, human_semantic_accuracy=False, arms=[])
    environment = dict(os.environ)
    environment.update(OMP_NUM_THREADS="2", OPENBLAS_NUM_THREADS="2", MKL_NUM_THREADS="2", NUMEXPR_NUM_THREADS="2",
                       TOKENIZERS_PARALLELISM="false", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    prefix = [sys.executable, "--neighbor-worker"] if getattr(sys, "frozen", False) else [sys.executable, "-m", "themeforge.neighbor_runner", "--worker"]
    start = time.perf_counter()
    try:
        for backend in ("lexical_tfidf", "sentence_embeddings"):
            if cancel is not None and cancel.is_set():
                raise RuntimeError("Comparison cancelled before the next backend.")
            if psutil.virtual_memory().available < 3 * 1024**3:
                raise RuntimeError("At least 3 GiB currently available RAM is required by this development guard.")
            command = prefix + ["--packet", str(packet_path), "--packet-sha256", packet_sha256,
                "--profile", str(profile_path), "--profile-sha256", profile_sha256,
                "--backend", backend, "--out", str(out / backend)]
            arm = dict(backend=backend, command=command, peak_sampled_tree_rss_bytes=0,
                       peak_os_process_working_set_bytes=0, stopped_by_guard=None, owned_processes_closed=False)
            report["arms"].append(arm)
            launched = time.perf_counter()
            process, owned = None, {}
            try:
                with (out / (backend + ".log")).open("xb") as log:
                    process = subprocess.Popen(command, cwd=Path(__file__).resolve().parent.parent if not getattr(sys, "frozen", False) else out,
                        env=environment, stdout=log, stderr=subprocess.STDOUT,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    observed = psutil.Process(process.pid)
                    owned[process.pid] = observed.create_time()
                    while process.poll() is None:
                        rss = 0
                        try:
                            for p in [observed] + observed.children(recursive=True):
                                owned[p.pid] = p.create_time()
                                mem = p.memory_info()
                                rss += mem.rss
                                arm["peak_os_process_working_set_bytes"] = max(arm["peak_os_process_working_set_bytes"], getattr(mem, "peak_wset", mem.rss))
                        except psutil.NoSuchProcess:
                            pass
                        arm["peak_sampled_tree_rss_bytes"] = max(arm["peak_sampled_tree_rss_bytes"], rss)
                        if rss > TREE_MEMORY_LIMIT_BYTES:
                            arm["stopped_by_guard"] = "process_tree_memory"
                        elif time.perf_counter() - launched > WALL_LIMIT_SECONDS:
                            arm["stopped_by_guard"] = "wall_time"
                        elif cancel is not None and cancel.is_set():
                            arm["stopped_by_guard"] = "user_cancel"
                        if arm["stopped_by_guard"]:
                            break
                        time.sleep(.05)
            finally:
                # Stop only processes whose identity was observed in this child tree.
                for pid, created in reversed(list(owned.items())):
                    try:
                        p = psutil.Process(pid)
                        if p.create_time() == created and p.is_running():
                            p.kill()
                    except psutil.NoSuchProcess:
                        pass
                if process is not None:
                    process.wait(timeout=10)
                    arm["exit_code"] = process.returncode
                arm["owned_processes_closed"] = all(not psutil.pid_exists(pid) or psutil.Process(pid).create_time() != created
                                                     for pid, created in owned.items())
                arm["elapsed_seconds"] = time.perf_counter() - launched
            if arm.get("exit_code") != 0 or arm["stopped_by_guard"] or not arm["owned_processes_closed"]:
                raise RuntimeError(f"{backend} failed; inspect its preserved log and resource record.")
        if file_sha256(packet_path) != packet_sha256 or file_sha256(profile_path) != profile_sha256:
            raise ValueError("An input changed during comparison.")
        report["status"] = "completed"
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
    finally:
        report["elapsed_seconds"] = time.perf_counter() - start
        report["owned_processes_closed"] = all(arm["owned_processes_closed"] for arm in report["arms"])
        _write(out / "comparison-runtime.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    for name in ("packet", "packet-sha256", "profile", "profile-sha256", "out"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--backend", choices=("lexical_tfidf", "sentence_embeddings"))
    args = parser.parse_args(argv)
    if args.worker:
        if not args.backend:
            parser.error("Worker requires an explicit backend.")
        return run_worker(packet_path=args.packet, packet_sha256=args.packet_sha256, profile_path=args.profile,
                          profile_sha256=args.profile_sha256, backend=args.backend, out=args.out)
    if args.backend:
        parser.error("The comparison runs both backends; --backend is worker-only.")
    report = run_comparison(args.packet, args.packet_sha256, args.profile, args.profile_sha256, args.out)
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
