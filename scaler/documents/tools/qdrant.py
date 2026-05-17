"""
Qdrant Cloud client — hybrid search (dense + sparse) collection management.

Dense vector:  Mistral `mistral-embed` via Mistral API (1024 dims, cosine)
Sparse vector: BM25 over hashed token term-frequency, scored by Qdrant's IDF
               modifier (no external model needed).

Hybrid search uses Reciprocal Rank Fusion (RRF) to combine both result sets,
giving better recall than either alone — dense catches semantic matches,
sparse catches exact keyword/name/number matches.
"""
import hashlib
import logging
import re
import uuid
from collections import Counter

from django.conf import settings
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

logger = logging.getLogger(__name__)

VECTOR_SIZE = 1024         # mistral-embed output dimension
DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"

_TOKEN_RE = re.compile(r"\b\w+\b", flags=re.UNICODE)

_client: QdrantClient | None = None
_embed_client: OpenAI | None = None
_indexes_ensured: bool = False


def _get_client() -> QdrantClient:
    global _client  # noqa: PLW0603
    if _client is None:
        if not settings.QDRANT_URL:
            raise ValueError("QDRANT_URL is not configured")
        _client = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY or None)
    return _client


def _get_embed_client() -> OpenAI:
    """Mistral exposes an OpenAI-compatible /v1/embeddings endpoint."""
    global _embed_client  # noqa: PLW0603
    if _embed_client is None:
        if not settings.MISTRAL_API_KEY:
            raise ValueError("MISTRAL_API_KEY is not configured")
        _embed_client = OpenAI(
            api_key=settings.MISTRAL_API_KEY,
            base_url=settings.MISTRAL_BASE_URL,
        )
    return _embed_client


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Generate dense embeddings for a batch of texts via Mistral."""
    if not texts:
        return []
    response = _get_embed_client().embeddings.create(
        model=settings.MISTRAL_EMBEDDINGS_MODEL,
        input=texts,
    )
    return [item.embedding for item in response.data]


def _hash_token(token: str) -> int:
    # Stable 31-bit hash so the int fits Qdrant's sparse-index id range.
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, "big") & 0x7FFFFFFF


def _to_sparse_vectors(texts: list[str]) -> list[qmodels.SparseVector]:
    """Tokenize, lowercase, hash to ints, and emit (id, term-frequency) pairs.

    Qdrant's IDF modifier (configured on the sparse-vector field) turns these
    raw term frequencies into BM25 scores at query time.
    """
    vectors: list[qmodels.SparseVector] = []
    for text in texts:
        counts: Counter[int] = Counter(
            _hash_token(t) for t in _TOKEN_RE.findall(text.lower())
        )
        if not counts:
            vectors.append(qmodels.SparseVector(indices=[], values=[]))
            continue
        indices, values = zip(*counts.items(), strict=True)
        vectors.append(
            qmodels.SparseVector(
                indices=list(indices),
                values=[float(v) for v in values],
            )
        )
    return vectors


def ensure_collection() -> None:
    """Create the Qdrant collection with dense + sparse config if it does not exist.

    Also ensures keyword payload indexes on `user_id` and `document_id` so that
    filtered searches do not fail with "Index required but not found".
    """
    client = _get_client()
    collection_name = settings.QDRANT_COLLECTION_NAME

    existing = {c.name for c in client.get_collections().collections}
    if collection_name not in existing:
        client.create_collection(
            collection_name=collection_name,
            vectors_config={
                DENSE_VECTOR_NAME: qmodels.VectorParams(
                    size=VECTOR_SIZE,
                    distance=qmodels.Distance.COSINE,
                ),
            },
            sparse_vectors_config={
                SPARSE_VECTOR_NAME: qmodels.SparseVectorParams(
                    modifier=qmodels.Modifier.IDF,
                ),
            },
        )
        logger.info("Created Qdrant collection '%s' (dense + sparse)", collection_name)

    _ensure_payload_indexes(client, collection_name)


def _ensure_payload_indexes(client: QdrantClient, collection_name: str) -> None:
    """Create keyword payload indexes for fields used in search filters.

    Cached per-process via `_indexes_ensured` so we don't hit Qdrant on every
    call. Idempotent on Qdrant's side, but errors (e.g. index already exists)
    are logged and swallowed so startup is resilient.
    """
    global _indexes_ensured  # noqa: PLW0603
    if _indexes_ensured:
        return
    for field_name in ("user_id", "document_id"):
        try:
            client.create_payload_index(
                collection_name=collection_name,
                field_name=field_name,
                field_schema=qmodels.PayloadSchemaType.KEYWORD,
            )
            logger.info("Ensured payload index on '%s' for '%s'", field_name, collection_name)
        except Exception as exc:  # noqa: BLE001 — index may already exist; log and continue
            logger.debug("Payload index for '%s' not (re)created: %s", field_name, exc)
    _indexes_ensured = True


def upsert_chunks(
    chunks: list[dict],
    dense_embeddings: list[list[float]],
    document_id: str,
    user_id: str,
) -> list[str]:
    """
    Upsert semantic chunks with dense (BAAI/bge-base-en-v1.5) and sparse (BM25) vectors.

    Returns a list of Qdrant point ID strings in the same order as chunks.
    """
    if len(chunks) != len(dense_embeddings):
        raise ValueError(f"chunks ({len(chunks)}) and embeddings ({len(dense_embeddings)}) count mismatch")

    ensure_collection()
    client = _get_client()
    collection_name = settings.QDRANT_COLLECTION_NAME

    texts = [chunk.get("text", "") for chunk in chunks]
    sparse_vectors = _to_sparse_vectors(texts)

    points = []
    point_ids = []
    for chunk, dense_vec, sparse_vec in zip(chunks, dense_embeddings, sparse_vectors, strict=True):
        point_id = str(uuid.uuid4())
        point_ids.append(point_id)
        payload = {
            "document_id": document_id,
            "user_id": user_id,
            "chunk_id": chunk.get("chunk_id", ""),
            "section": chunk.get("section", ""),
            "text": chunk.get("text", ""),
            "page_refs": chunk.get("page_refs", []),
            "images": chunk.get("images", []),
            "has_table": chunk.get("has_table", False),
            "has_signature": chunk.get("has_signature", False),
            "document_type": chunk.get("document_type", ""),
            "chunking_strategy": chunk.get("chunking_strategy", ""),
            **{k: v for k, v in chunk.get("metadata", {}).items()},
        }
        points.append(
            qmodels.PointStruct(
                id=point_id,
                vector={DENSE_VECTOR_NAME: dense_vec, SPARSE_VECTOR_NAME: sparse_vec},
                payload=payload,
            )
        )

    client.upsert(collection_name=collection_name, points=points)
    logger.info("Upserted %d chunks to Qdrant '%s' (dense + sparse)", len(points), collection_name)
    return point_ids


def search_chunks(
    query_vector: list[float],
    query_text: str,
    user_id: str,
    document_id: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """
    Hybrid search: dense cosine similarity + BM25 sparse, fused with RRF.

    Prefetches 2× limit from each branch before fusion so the final ranking
    has enough candidates to rerank.
    """
    client = _get_client()
    collection_name = settings.QDRANT_COLLECTION_NAME
    _ensure_payload_indexes(client, collection_name)

    must_conditions = [qmodels.FieldCondition(key="user_id", match=qmodels.MatchValue(value=user_id))]
    if document_id:
        must_conditions.append(
            qmodels.FieldCondition(key="document_id", match=qmodels.MatchValue(value=document_id))
        )
    query_filter = qmodels.Filter(must=must_conditions)

    query_sparse = _to_sparse_vectors([query_text])[0]

    results = client.query_points(
        collection_name=collection_name,
        prefetch=[
            qmodels.Prefetch(
                query=query_vector,
                using=DENSE_VECTOR_NAME,
                filter=query_filter,
                limit=limit * 2,
            ),
            qmodels.Prefetch(
                query=query_sparse,
                using=SPARSE_VECTOR_NAME,
                filter=query_filter,
                limit=limit * 2,
            ),
        ],
        query=qmodels.FusionQuery(fusion=qmodels.Fusion.RRF),
        limit=limit,
        with_payload=True,
    )

    return [
        {"score": hit.score, "chunk_id": hit.payload.get("chunk_id"), **hit.payload}
        for hit in results.points
    ]
