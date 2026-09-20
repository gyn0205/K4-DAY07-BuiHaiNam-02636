#!/usr/bin/env python3
"""
Script kiểm chứng 5 câu hỏi benchmark và lọc metadata trên kho tài liệu TMĐT thực tế (Phase 2 - K4-L3B).

Chạy bằng lệnh:
    python bench.py
"""

import csv
import math
from collections import Counter
from pathlib import Path
import os
import re
import sys
import time
import zlib
from typing import Any, Callable

# Cấu hình UTF-8 cho Windows PowerShell terminal
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Thêm thư mục gốc vào PYTHONPATH
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from src.models import Document
from src.chunking import RecursiveChunker
from src.store import EmbeddingStore
from src.agent import KnowledgeBaseAgent
from src.embeddings import _mock_embed


def parse_markdown_with_frontmatter(file_path: Path) -> tuple[dict[str, str], str]:
    """Trích xuất frontmatter YAML và phần nội dung văn bản."""
    text = file_path.read_text(encoding="utf-8")
    metadata: dict[str, str] = {}
    content = text

    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().splitlines():
                if ":" in line:
                    key, val = line.split(":", 1)
                    metadata[key.strip()] = val.strip().strip('"')
            content = parts[2].strip()

    if "doc_id" not in metadata:
        metadata["doc_id"] = file_path.stem
    metadata["source_file"] = str(file_path)
    return metadata, content


MERGE_CHUNKS = "--merge" in sys.argv  # chiến lược cá nhân: gộp các mảnh ngắn của RecursiveChunker


def merge_small_chunks(pieces: list[str], max_chars: int) -> list[str]:
    """Gộp các mảnh liên tiếp cho tới khi gần đạt max_chars để mỗi chunk giữ được ngữ cảnh."""
    merged: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 1 > max_chars:
            merged.append(current)
            current = ""
        current = f"{current}\n{piece}".strip()
    if current:
        merged.append(current)
    return merged


def load_and_chunk_corpus(data_dir: Path, chunk_size: int = 500) -> list[Document]:
    """Nạp toàn bộ tài liệu trong data/ecommerce và chia nhỏ thành các Document chunk."""
    chunker = RecursiveChunker(chunk_size=chunk_size)
    chunked_docs: list[Document] = []

    # Chỉ nạp các tài liệu khai báo trong sources.csv (bỏ qua file mẫu/tài liệu ngoài danh sách)
    with (data_dir / "sources.csv").open(encoding="utf-8", newline="") as manifest:
        md_files = [BASE_DIR / row["file_path"].replace("\\", "/") for row in csv.DictReader(manifest)]
    missing = [f for f in md_files if not f.exists()]
    if missing:
        raise FileNotFoundError(f"sources.csv trỏ tới file không tồn tại: {[str(f) for f in missing]}")
    print(f"[*] sources.csv khai báo {len(md_files)} file Markdown trong {data_dir}:")

    for f in md_files:
        meta, content = parse_markdown_with_frontmatter(f)
        chunks = chunker.chunk(content)
        if MERGE_CHUNKS:
            chunks = merge_small_chunks(chunks, chunk_size)
        print(f"    - [{meta.get('doc_id')}] ({len(content):,} ký tự) -> {len(chunks)} chunks | audience: {meta.get('audience', 'N/A')}")

        for idx, chunk_text in enumerate(chunks):
            chunk_meta = dict(meta)
            chunk_meta["chunk_index"] = idx
            chunked_docs.append(
                Document(
                    id=f"{meta['doc_id']}_c{idx}",
                    content=chunk_text,
                    metadata=chunk_meta,
                )
            )

    return chunked_docs


from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")


def make_llm_fn() -> tuple[Callable[[str], str], str]:
    """Khởi tạo hàm LLM: dùng Gemini nếu có API key, hoặc fallback thông báo."""
    def fallback_answer(prompt: str) -> str:
        lines = prompt.splitlines()
        context_lines = []
        is_context = False
        for line in lines:
            if line.startswith("Context:"):
                is_context = True
                continue
            if line.startswith("Question:"):
                is_context = False
                break
            if is_context and line.strip() and not line.strip().startswith("---"):
                cleaned = re.sub(r"^#{1,6}\s*", "", line.strip())
                if cleaned:
                    context_lines.append(cleaned)

        if context_lines:
            snippet = " ".join(context_lines[:5])
            if len(snippet) > 450:
                snippet = snippet[:450] + "..."
            return f"Dựa trên tài liệu quy định và chính sách của cửa hàng:\n\n{snippet}"
        return "Hiện chưa tìm thấy thông tin phù hợp trong cơ sở dữ liệu chính sách."

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if api_key and api_key.strip() and api_key != "your_gemini_api_key_here":
        try:
            from google import genai
            client = genai.Client(api_key=api_key.strip())
            # Ưu tiên các model flash-lite có quota free tier dồi dào
            preferred_model = os.getenv("GEMINI_LLM_MODEL", "gemini-3.5-flash-lite")
            candidate_models = [
                preferred_model,
                "gemini-3.5-flash-lite",
                "gemini-flash-lite-latest",
                "gemini-3.1-flash-lite",
                "gemini-3.5-flash",
                "gemini-3.6-flash",
            ]
            seen = set()
            candidate_models = [m for m in candidate_models if not (m in seen or seen.add(m))]

            def gemini_answer(prompt: str) -> str:
                system_prompt = (
                    "Bạn là chuyên viên tư vấn khách hàng cao cấp về chính sách thương mại điện tử và bảo hành. "
                    "Hãy đọc kỹ phần 'Context' được cung cấp và trả lời câu hỏi của người dùng một cách tự nhiên, "
                    "đầy đủ, chu đáo, có hồn và chuyên nghiệp bằng tiếng Việt. "
                    "Trích dẫn các con số, thời hạn, điều kiện bảo hành cụ thể được nêu trong Context. "
                    "Hãy định dạng câu trả lời rõ ràng với các gạch đầu dòng hoặc đoạn văn ngắn gọn, dễ đọc. "
                    "Tuyệt đối không để lại các ký tự thô hay markdown tiêu đề thừa thãi. "
                    "Nếu Context không có thông tin, hãy lịch sự thông báo cho khách hàng."
                )
                full_content = f"{system_prompt}\n\n{prompt}"
                
                for mdl in candidate_models:
                    try:
                        response = client.models.generate_content(
                            model=mdl,
                            contents=full_content,
                        )
                        if response.text:
                            return response.text.strip()
                    except Exception as ex:
                        err_str = str(ex).lower()
                        if "429" in err_str or "quota" in err_str or "not_found" in err_str:
                            time.sleep(1)
                            continue  # Thử model tiếp theo
                        else:
                            break

                # Fallback trích xuất thông minh từ context nếu tất cả API tạm hết quota
                return fallback_answer(prompt)

            return gemini_answer, f"Google Gemini ({preferred_model})"
        except Exception as err:
            print(f"[!] Lỗi khi kết nối Gemini API: {err}", file=sys.stderr)

    return fallback_answer, "Mock LLM Fallback"


def lexical_embed(text: str, dim: int = 2048) -> list[float]:
    """Hashed unigram+bigram TF, L2-normalised. Không có ngữ nghĩa nhưng bắt được trùng từ khóa."""
    tokens = re.findall(r"\w+", text.lower())
    grams = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
    vec = [0.0] * dim
    for gram, count in Counter(zlib.crc32(g.encode()) % dim for g in grams).items():
        vec[gram] = 1.0 + math.log(count)
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def make_embedder_fn(chunks: list[Document]) -> tuple[Callable[[str], list[float]], str]:
    """Khởi tạo hàm Embedding: dùng Gemini Semantic Embeddings với file cache."""
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if api_key and api_key.strip() and api_key != "your_gemini_api_key_here":
        try:
            import json
            import time
            from google import genai

            client = genai.Client(api_key=api_key.strip())
            emb_model = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
            cache_file = BASE_DIR / "data" / "ecommerce" / "gemini_embeddings_cache.json"
            cache: dict[str, list[float]] = {}

            if cache_file.exists():
                try:
                    cache = json.loads(cache_file.read_text(encoding="utf-8"))
                    print(f"[*] Đã tải {len(cache)} vector từ file cache: {cache_file.name}")
                except Exception:
                    cache = {}

            missing_docs = [d for d in chunks if d.content not in cache]
            if missing_docs:
                print(f"[*] Đang tạo Semantic Embeddings cho {len(missing_docs)} chunks mới bằng Google {emb_model}...")
                batch_size = 20
                for i in range(0, len(missing_docs), batch_size):
                    batch = missing_docs[i : i + batch_size]
                    texts = [d.content for d in batch]
                    try:
                        res = client.models.embed_content(model=emb_model, contents=texts)
                        for doc, emb in zip(batch, res.embeddings):
                            cache[doc.content] = [float(v) for v in emb.values]
                        print(f"    - Đã nhúng {min(i + batch_size, len(missing_docs))}/{len(missing_docs)} chunks...")
                        time.sleep(0.5)
                    except Exception as b_err:
                        print(f"[!] Lỗi batch ({b_err}), chuyển sang nhúng từng câu...", file=sys.stderr)
                        for d in batch:
                            try:
                                r = client.models.embed_content(model=emb_model, contents=d.content)
                                cache[d.content] = [float(v) for v in r.embeddings[0].values]
                                time.sleep(0.3)
                            except Exception:
                                pass
                try:
                    cache_file.write_text(json.dumps(cache), encoding="utf-8")
                except Exception:
                    pass

            def embed_fn(text: str) -> list[float]:
                if text in cache:
                    return cache[text]
                try:
                    r = client.models.embed_content(model=emb_model, contents=text)
                    vec = [float(v) for v in r.embeddings[0].values]
                    cache[text] = vec
                    return vec
                except Exception as q_err:
                    print(f"[!] Lỗi query embed ({q_err}), dùng hash fallback", file=sys.stderr)
                    return _mock_embed(text)

            return embed_fn, f"Google Gemini Embeddings ({emb_model} - 3072 dims)"
        except Exception as e:
            print(f"[!] Lỗi khởi tạo Gemini Embedder ({e}). Dùng MockEmbedder fallback.", file=sys.stderr)

    try:
        from src.embeddings import LOCAL_EMBEDDING_MODEL, LocalEmbedder

        local = LocalEmbedder(model_name=os.getenv("LOCAL_EMBEDDING_MODEL", LOCAL_EMBEDDING_MODEL))
        return local, f"Local ({local.model_name})"
    except Exception as e:  # sentence-transformers chưa cài hoặc không tải được model
        print(f"[!] Không dùng được LocalEmbedder ({e}).", file=sys.stderr)

    print("[!] Dùng LexicalEmbedder (từ khóa, offline) thay cho MockEmbedder vốn không có ngữ nghĩa.", file=sys.stderr)
    return lexical_embed, "Lexical hashed bag-of-words (unigram+bigram, offline)"


def main() -> int:
    data_dir = BASE_DIR / "data" / "ecommerce"
    if not data_dir.exists():
        print(f"[!] Thư mục {data_dir} không tồn tại. Hãy chạy crawler trước.", file=sys.stderr)
        return 1

    print("=" * 80)
    print(" BÀI LAB 07 (K4-L3B) — ĐÁNH GIÁ TRUY XUẤT VÀ LỌC METADATA TRÊN CORPUS THỰC TẾ")
    print("=" * 80)

    # 1. Nạp và Chunking với kích thước 1200 ký tự để bảo toàn trọn vẹn ngữ cảnh điều khoản
    chunks = load_and_chunk_corpus(data_dir, chunk_size=1200)
    print(f"\n[+] Tổng số chunk tạo ra: {len(chunks)}")

    # 2. Khởi tạo Embedder và LLM
    embed_fn, embed_name = make_embedder_fn(chunks)
    print(f"[+] Mô hình Embedding: {embed_name}")

    llm_fn, llm_name = make_llm_fn()
    print(f"[+] Mô hình LLM Generator: {llm_name}")

    store = EmbeddingStore(collection_name="ecommerce_bench", embedding_fn=embed_fn)
    store.add_documents(chunks)
    print(f"[+] Đã nạp thành công {store.get_collection_size()} chunks vào EmbeddingStore")

    agent = KnowledgeBaseAgent(store=store, llm_fn=llm_fn)

    # 3. Định nghĩa 5 câu hỏi benchmark chuẩn
    benchmarks = [
        {
            "id": 1,
            "key_facts": ["thanh toán 100%"],
            "query": "Khách hàng có được mở seal hộp iPhone kiểm tra trước khi thanh toán không?",
            "filter": None,
            "gold_answer": "Khách hàng phải thanh toán 100% trước khi mở seal hộp sản phẩm Apple; chỉ mở hộp kiểm tra sau khi đã thanh toán.",
            "target_doc": "cellphones-apple-unboxing",
        },
        {
            "id": 2,
            "key_facts": ["12 tháng", "pin chai", "phồng"],
            "query": "Thay pin Macbook tại Điện Thoại Vui được bảo hành bao lâu và bảo hành những lỗi gì?",
            "filter": None,
            "gold_answer": "Bảo hành 1 đổi 1 trong 12 tháng, bảo hành lỗi pin chai, báo ảo, sạc không vào, sập nguồn và cả trường hợp pin phồng.",
            "target_doc": "dienthoaivui-repair-warranty",
        },
        {
            "id": 3,
            "key_facts": ["đến 8%", "hóa đơn điện tử", "không xuất"],
            "query": "Doanh nghiệp mua số lượng lớn có chính sách chiết khấu và xuất hóa đơn VAT như thế nào?",
            "filter": {"audience": "seller"},
            "gold_answer": "Chính sách chiết khấu riêng theo ngành hàng đến 8%, mức giảm thêm tăng theo giá trị đơn (0-50 triệu, 50-100 triệu, 100-200 triệu, từ 200 triệu). CellphoneS dùng hóa đơn điện tử nên không xuất hóa đơn trước khi giao hàng; sẽ cung cấp Hợp đồng, Giấy đề nghị thanh toán để làm hồ sơ thanh toán.",
            "target_doc": "cellphones-b2b-seller",
        },
        {
            "id": 4,
            "key_facts": ["3 năm", "6 tháng", "đánh giá của TNC"],
            "query": "Điều kiện để linh kiện máy tính được đổi mới tại TNC Store là gì?",
            "filter": None,
            "gold_answer": "Đổi mới 100% trong 3 năm đầu với CPU/SSD/RAM và trong 6 tháng đầu với nguồn, tản nhiệt, mainboard, HDD, VGA; quyết định đổi trả dựa theo đánh giá của TNC Store (phần trăm xước, độ mới). Không bảo hành nếu mất tem/phiếu bảo hành, cháy nổ, rơi vỡ, móp, nứt, trầy xước.",
            "target_doc": "tnc-store-warranty",
        },
        {
            "id": 5,
            "key_facts": ["90%", "1 đổi 1 VIP", "tương đương"],
            "query": "Gói bảo hành mở rộng rơi vỡ vào nước tại CellphoneS có những quyền lợi gì?",
            "filter": None,
            "gold_answer": "Gói bảo hành rơi vỡ, rơi nước (12 tháng) tặng gói 1 đổi 1 VIP, không giới hạn số lần đổi máy, hỗ trợ tới 90% chi phí sửa chữa khi rơi vỡ/vào nước; nếu không sửa được, CellphoneS đổi sản phẩm cũ chất lượng tương đương.",
            "target_doc": "cellphones-extended-warranty",
        },
    ]

    print("\n" + "=" * 80)
    print(" TIẾN HÀNH CHẠY KIỂM CHỨNG 5 CÂU HỎI BENCHMARK")
    print("=" * 80)

    relevant_count = 0
    fact_ratio_sum = 0.0
    summary_rows: list[tuple[int, bool, str]] = []
    for item in benchmarks:
        qid = item["id"]
        q = item["query"]
        m_filter = item["filter"]
        gold = item["gold_answer"]
        target = item["target_doc"]

        print(f"\n--- [CÂU HỎI {qid}] ---")
        print(f"❓ Câu hỏi:       {q}")
        print(f"🎯 Metadata lọc:   {m_filter if m_filter else 'Không lọc (Toàn bộ kho)'}")
        print(f"⭐ Câu trả lời mẫu: {gold}")

        # Tìm kiếm với hoặc không với filter
        if m_filter:
            results = store.search_with_filter(q, top_k=3, metadata_filter=m_filter)
        else:
            results = store.search(q, top_k=3)

        if not results:
            print("❌ Không tìm thấy chunk nào phù hợp!")
            summary_rows.append((qid, False, "-"))
            continue

        top3_docs = [r["metadata"].get("doc_id") for r in results]
        is_relevant = target in top3_docs
        relevant_count += is_relevant
        top3_text = " ".join(r["content"] for r in results).lower()
        found = [f for f in item["key_facts"] if f.lower() in top3_text]
        fact_ratio_sum += len(found) / len(item["key_facts"])
        summary_rows.append((qid, is_relevant, f"top1={top3_docs[0]}, top3={top3_docs}, ý chính trong top-3: {len(found)}/{len(item['key_facts'])}"))
        print(f"  • Top-3 doc_id: {top3_docs} | chứa tài liệu đích ({target}): {'CÓ' if is_relevant else 'KHÔNG'}")
        print(f"  • Độ dài 3 chunk: {[len(r['content']) for r in results]} | ý chính của đáp án chuẩn có trong top-3: {len(found)}/{len(item['key_facts'])} {found}")

        top1 = results[0]
        meta = top1["metadata"]
        print(f"\n  [Top-1 Retrieval Result]")
        print(f"  • Chunk ID:     {top1['id']}")
        print(f"  • Điểm Score:   {top1['score']:.4f}")
        print(f"  • Thuộc file:   {meta.get('doc_id')} (Audience: {meta.get('audience')}, Category: {meta.get('category')})")
        print(f"  • Đoạn trích:   \"{top1['content'][:140].replace(chr(10), ' ')}...\"")

        # Câu trả lời từ Agent
        agent_resp = agent.answer(q, top_k=3)
        print(f"\n  🤖 Agent Answer: {agent_resp}")
        time.sleep(1.5)

    # 4. Thử nghiệm so sánh: Khi KHÔNG lọc vs CÓ lọc ở Câu hỏi 3
    print("\n" + "=" * 80)
    print(" MINH CHỨNG HIỆU QUẢ CỦA METADATA FILTERING (Câu 3: Bán sỉ Doanh nghiệp)")
    print("=" * 80)
    q3 = benchmarks[2]["query"]

    res_no_filter = store.search(q3, top_k=3)
    res_with_filter = store.search_with_filter(q3, top_k=3, metadata_filter={"audience": "seller"})

    print("\n1. KHI KHÔNG LỌC METADATA:")
    for i, r in enumerate(res_no_filter, 1):
        print(f"   Top-{i}: [{r['metadata'].get('doc_id')}] (audience={r['metadata'].get('audience')}) -> score={r['score']:.4f}")

    print("\n2. KHI CÓ LỌC METADATA (audience='seller'):")
    for i, r in enumerate(res_with_filter, 1):
        print(f"   Top-{i}: [{r['metadata'].get('doc_id')}] (audience={r['metadata'].get('audience')}) -> score={r['score']:.4f}")
    print("   ==> Đã loại bỏ hoàn toàn các tài liệu dành cho khách hàng cá nhân (buyer), chỉ giữ lại chính sách B2B của đối tác!")

    print("\n" + "=" * 80)
    print(f" CHUNKING: {'RecursiveChunker + gộp mảnh nhỏ (--merge)' if MERGE_CHUNKS else 'RecursiveChunker gốc (baseline)'} | {len(chunks)} chunk")
    print(f" TỔNG KẾT: {relevant_count}/{len(benchmarks)} câu có chunk của tài liệu đích trong top-3")
    print(f" Độ phủ ý chính của đáp án chuẩn trong top-3: {fact_ratio_sum:.2f}/{len(benchmarks)} câu")
    for qid, ok, info in summary_rows:
        print(f"   Q{qid}: {'CÓ' if ok else 'KHÔNG'} | {info}")
    print("\n" + "=" * 80)
    print(" HOÀN TẤT KIỂM CHỨNG 5 CÂU HỎI BENCHMARK VÀ LỌC METADATA")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())