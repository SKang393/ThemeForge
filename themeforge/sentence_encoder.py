"""Pinned, CPU-only MiniLM inference. No downloads, remote code or lexical fallback.

The external model profile is supplied explicitly. Whole turns longer than the
model recipe are represented by recorded, nonoverlapping token chunks; chunk
averaging does not preserve every relation across a chunk boundary.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import time

from .semantic_neighbors import validate_packet

VERSION = "themeforge.pinned-minilm-onnx.v1"
MODEL_REPOSITORY = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
MODEL_SHA256 = "b941bf19f1f1283680f449fa6a7336bb5600bdcd5f84d10ddc5cd72218a0fd21"
TOKENIZER_SHA256 = "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037"  # gitleaks:allow -- public tokenizer.json SHA256, not a credential
MAX_SEQUENCE_TOKENS = 256
PAYLOAD_TOKENS = 254
MAX_CHUNKS = 8
DIMENSION = 384


def file_sha256(path):
    h = sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def local_file(value, *, base=None):
    value = str(value)
    if value.startswith(("\\\\", "//")) or "://" in value:
        raise ValueError("Encoder files must be local paths, not a URL or network share.")
    path = Path(value)
    if not path.is_absolute():
        if base is None:
            raise ValueError("An absolute local profile path is required.")
        path = Path(base) / path
    path = path.resolve(strict=True)
    if not path.is_file():
        raise ValueError("Encoder input is not a file.")
    return path


def _read_pinned_json(path, expected, limit):
    if type(expected) is not str or len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise ValueError("Supply an explicit lowercase SHA256 pin.")
    if path.stat().st_size > limit or file_sha256(path) != expected:
        raise ValueError("Pinned input is oversized or its SHA256 has changed.")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def verify_profile(profile_path, expected_sha256):
    """Verify this reviewed model recipe and external files before runtime import."""
    path = local_file(profile_path)
    profile = _read_pinned_json(path, expected_sha256, 128 * 1024)
    pins = dict(repository=MODEL_REPOSITORY, revision=MODEL_REVISION,
                model_sha256=MODEL_SHA256, tokenizer_sha256=TOKENIZER_SHA256, license="Apache-2.0")
    if any(profile.get(key) != value for key, value in pins.items()):
        raise ValueError("Profile is not the reviewed MiniLM revision and quantization.")
    model = local_file(profile["model_path"], base=path.parent)
    tokenizer = local_file(profile["tokenizer_path"], base=path.parent)
    if model.stat().st_size > 32 * 1024**2 or tokenizer.stat().st_size > 4 * 1024**2:
        raise ValueError("Model/tokenizer exceeds the bounded recipe size.")
    for file, expected in ((model, MODEL_SHA256), (tokenizer, TOKENIZER_SHA256)):
        if file_sha256(file) != expected:
            raise ValueError(f"Encoder artifact hash mismatch: {file.name}")
    manifest_path = local_file(profile["artifact_manifest_path"], base=path.parent)
    manifest = _read_pinned_json(manifest_path, profile["artifact_manifest_sha256"], 256 * 1024)
    files = manifest.get("files")
    if type(files) is not list or not 1 <= len(files) <= 64:
        raise ValueError("Encoder manifest must pin 1..64 local files.")
    verified = set()
    for row in files:
        file = local_file(row["path"], base=manifest_path.parent)
        if file.stat().st_size != row["bytes"] or file_sha256(file) != row["sha256"]:
            raise ValueError(f"Encoder manifest mismatch: {file.name}")
        verified.add(file)
    if not {model, tokenizer}.issubset(verified):
        raise ValueError("Model and tokenizer must both be in the pinned manifest.")
    runtime = profile["runtime"]
    if (runtime.get("providers") != ["CPUExecutionProvider"]
            or runtime.get("intra_op_threads") != 2 or runtime.get("inter_op_threads") != 1
            or runtime.get("execution_mode") != "ORT_SEQUENTIAL"
            or runtime.get("trust_remote_code") is not False or runtime.get("local_files_only") is not True):
        raise ValueError("Profile must use the reviewed CPU-only, local runtime settings.")
    return profile, model, tokenizer, len(files)


def token_chunks(tokenizer, text):
    """Keep every payload token, with explicit offsets and no silent truncation."""
    if type(text) is not str or not text.strip() or len(text) > 50000:
        raise ValueError("Encode a nonblank source turn of at most 50,000 characters.")
    encoded = tokenizer.encode(text, add_special_tokens=False)
    ids, offsets = list(encoded.ids), list(encoded.offsets)
    if not ids or len(ids) != len(offsets) or len(ids) > PAYLOAD_TOKENS * MAX_CHUNKS:
        raise ValueError("Source turn is empty or exceeds eight explicit 254-token chunks.")
    chunks = []
    for start in range(0, len(ids), PAYLOAD_TOKENS):
        end = min(start + PAYLOAD_TOKENS, len(ids))
        chunks.append(dict(payload_token_start=start, payload_token_end=end,
                           source_char_start=offsets[start][0], source_char_end=offsets[end - 1][1],
                           payload_tokens=end - start, input_ids=[101] + ids[start:end] + [102]))
    return chunks


def masked_mean_normalize(hidden, attention_mask):
    """Official masked mean over all nonpadding tokens, then L2 normalization."""
    import numpy as np
    values = np.asarray(hidden, dtype=np.float64)
    mask = np.asarray(attention_mask)
    if (values.ndim != 3 or values.shape[2] != DIMENSION or mask.shape != values.shape[:2]
            or not np.isfinite(values).all() or not np.isin(mask, (0, 1)).all()
            or (mask.sum(axis=1) == 0).any()):
        raise ValueError("Invalid or empty encoder output/mask.")
    pooled = (values * mask[:, :, None]).sum(axis=1) / mask.sum(axis=1)[:, None]
    norms = np.linalg.norm(pooled, axis=1, keepdims=True)
    if not np.isfinite(pooled).all() or (norms <= 1e-12).any():
        raise ValueError("Encoder produced an invalid zero/nonfinite sentence vector.")
    return pooled / norms


def encode_packet(packet, profile_path, expected_sha256):
    """Return vectors in exact node order and full, nonhuman inference provenance.

    The caller owns a process time/RAM envelope. This function uses two CPU
    threads, one turn/chunk at a time, at most 20 turns and eight chunks per turn.
    """
    started = time.perf_counter()
    validate_packet(packet)
    profile, model_path, tokenizer_path, manifest_count = verify_profile(profile_path, expected_sha256)
    import numpy as np
    import onnxruntime as ort
    import tokenizers
    from tokenizers import Tokenizer
    versions = {"numpy": np.__version__, "onnxruntime": ort.__version__, "tokenizers": tokenizers.__version__}
    runtime = profile["runtime"]
    expected_versions = {"numpy": runtime["numpy_version"], "onnxruntime": runtime["version"],
                         "tokenizers": runtime["tokenizers_version"]}
    if versions != expected_versions:
        raise ValueError("Encoder runtime version differs from the reviewed profile; no fallback was used.")
    tokenizer = Tokenizer.from_file(str(tokenizer_path))
    tokenizer.no_truncation()
    tokenizer.no_padding()
    if tokenizer.token_to_id("[CLS]") != 101 or tokenizer.token_to_id("[SEP]") != 102:
        raise ValueError("Unexpected tokenizer special tokens.")
    # Tokenize and validate every turn before allocating an inference session.
    chunk_rows = [token_chunks(tokenizer, node["text"]) for node in packet["nodes"]]
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.log_severity_level = 3
    session = ort.InferenceSession(str(model_path), sess_options=options, providers=["CPUExecutionProvider"])
    if session.get_providers() != ["CPUExecutionProvider"]:
        raise ValueError("Unexpected inference provider; CPU-only execution is required.")
    if {v.name: v.type for v in session.get_inputs()} != {
            "input_ids": "tensor(int64)", "attention_mask": "tensor(int64)", "token_type_ids": "tensor(int64)"}:
        raise ValueError("Unexpected model input contract.")
    if [(v.name, v.type) for v in session.get_outputs()] != [("last_hidden_state", "tensor(float)")]:
        raise ValueError("Unexpected model output contract.")
    vectors, diagnostics = [], []
    for node, chunks in zip(packet["nodes"], chunk_rows):
        chunk_vectors, weights = [], []
        for chunk in chunks:
            ids = np.asarray([chunk["input_ids"]], dtype=np.int64)
            mask = np.ones_like(ids, dtype=np.int64)
            hidden = session.run(["last_hidden_state"], {"input_ids": ids, "attention_mask": mask,
                                                       "token_type_ids": np.zeros_like(ids)})[0]
            chunk_vectors.append(masked_mean_normalize(hidden, mask)[0])
            weights.append(chunk["payload_tokens"])
        vector = np.average(np.stack(chunk_vectors), axis=0, weights=weights)
        norm = np.linalg.norm(vector)
        if not np.isfinite(vector).all() or norm <= 1e-12:
            raise ValueError("Chunk aggregation produced an invalid sentence vector.")
        vectors.append((vector / norm).tolist())
        diagnostics.append(dict(node_id=node["id"], text_sha256=sha256(node["text"].encode("utf-8")).hexdigest(),
                                payload_tokens=sum(weights), chunks=chunks, truncated_tokens=0))
    del session
    return dict(vectors=vectors, provenance=dict(
        adapter=VERSION, repository=MODEL_REPOSITORY, revision=MODEL_REVISION,
        model_sha256=MODEL_SHA256, tokenizer_sha256=TOKENIZER_SHA256,
        profile_sha256=expected_sha256, manifest_files_verified=manifest_count,
        runtime_versions=versions, provider="CPUExecutionProvider", intra_op_threads=2, inter_op_threads=1,
        max_sequence_tokens=MAX_SEQUENCE_TOKENS, batch_size=1, dimension=DIMENSION,
        pooling="attention-masked mean then L2; payload-token-weighted chunk mean then L2",
        tokenization=diagnostics, input_fields=["node.text"], original_text_preserved=True,
        chunk_boundary_relations_not_guaranteed=True, training=False, network_calls=0,
        semantic_accuracy_measured=False, reviewer_kind="none", elapsed_seconds=time.perf_counter() - started))
