# Báo Cáo Cá Nhân - Lab 7: Embedding & Vector Store

**Họ tên:** Bùi Hải Nam  
**Nhóm:** LaoGaKho  
**Ngày:** 20/9/2026

> Phần nhóm về tài liệu, chiến lược, benchmark và demo được nộp chung trong `REPORT_NHOM.md`.

## 1. Khởi động (Warm-up)

### Độ tương tự cosine

Độ tương tự cosine cao nghĩa là hai vector embedding có hướng gần nhau, vì vậy hai văn bản thường có nội dung hoặc ý nghĩa gần nhau. Giá trị càng gần 1 thì mức tương đồng theo embedding càng cao.

**Ví dụ tương đồng cao**

- Câu A: Chính sách đổi trả sản phẩm.
- Câu B: Quy định hoàn trả hàng hóa.
- Lý do: Cả hai đều nói về việc trả lại sản phẩm và chính sách hoàn trả.

**Ví dụ tương đồng thấp**

- Câu A: Chính sách đổi trả sản phẩm.
- Câu B: Cách trồng cây trong nhà.
- Lý do: Một câu nói về thương mại điện tử, câu còn lại nói về chăm sóc cây.

Cosine similarity tập trung vào hướng của vector thay vì độ lớn tuyệt đối. Điều này phù hợp với text embedding vì hai văn bản có thể có độ dài khác nhau nhưng vẫn cùng chủ đề.

### Bài toán chunking

Với `chunk_size=500`, `overlap=50`:

```text
step = 500 - 50 = 450
ceil((10000 - 50) / 450) = ceil(22.11) = 23
```

**Kết quả: 23 chunks.**

Với `overlap=100`:

```text
step = 500 - 100 = 400
ceil((10000 - 100) / 400) = ceil(24.75) = 25
```

Overlap lớn hơn giúp giữ ngữ cảnh ở ranh giới giữa hai chunk, nhưng làm tăng số chunk và chi phí xử lý.

## 2. Hướng tiếp cận của tôi

### `SentenceChunker.chunk`

Hàm dùng regex `r"(?<=[.!?])(?:\s+|\n+)` để tách sau dấu chấm, chấm than hoặc chấm hỏi khi có khoảng trắng hoặc xuống dòng phía sau. Các câu được làm sạch bằng `strip()`, loại bỏ phần tử rỗng và gom thành nhóm có tối đa `max_sentences_per_chunk` câu. Với đầu vào rỗng hoặc chỉ có khoảng trắng, hàm trả về danh sách rỗng.

### `RecursiveChunker.chunk` và `_split`

Thuật toán thử các separator theo thứ tự ưu tiên: đoạn văn, dòng, câu, khoảng trắng và cuối cùng là cắt theo ký tự. Base case là văn bản đã ngắn hơn hoặc bằng `chunk_size`; nếu không còn separator, hàm cắt cứng theo kích thước chunk để luôn kết thúc an toàn.

### `EmbeddingStore`

`add_documents` tạo embedding cho từng document và lưu record gồm ID, nội dung, metadata và embedding. `search` tạo embedding cho query, tính dot product với các embedding đã lưu, sắp xếp điểm giảm dần và trả về tối đa `top_k` kết quả.

`search_with_filter` lọc record theo metadata trước rồi mới tính điểm tương đồng trên tập ứng viên. Mỗi record lưu thêm `doc_id`, vì vậy `delete_document` có thể loại bỏ tất cả chunk thuộc cùng một tài liệu và trả về `True` nếu có record bị xóa.

### `KnowledgeBaseAgent.answer`

`answer` truy xuất các chunk liên quan từ `EmbeddingStore`, nối nội dung thành phần `Context`, rồi đặt context và câu hỏi vào prompt. Prompt yêu cầu LLM chỉ sử dụng context; sau đó hàm gọi `llm_fn` và trả về câu trả lời.

## 3. Hoàn thiện code

Đã hoàn thiện:

- `SentenceChunker`
- `RecursiveChunker`
- `compute_similarity`
- `ChunkingStrategyComparator`
- `EmbeddingStore`
- `KnowledgeBaseAgent`

### Kết quả kiểm thử

Lệnh chạy:

```powershell
python -m pytest tests\test_solution.py -v
```

Kết quả:

```text
42 passed in 0.09s
```

**Số lượng bài test vượt qua: 42 / 42.**

## 4. Dự đoán độ tương tự

Các giá trị dưới đây được chạy bằng `_mock_embed` của project. Đây là embedding giả lập xác định, dùng để kiểm thử pipeline, không phải mô hình ngữ nghĩa thực tế.

| Cặp | Câu A | Câu B | Dự đoán | Điểm thực tế | Đúng? |
|---|---|---|---|---:|---|
| 1 | Chính sách đổi trả sản phẩm | Quy định hoàn trả hàng hóa | cao | -0.1778 | Không |
| 2 | Chính sách đổi trả sản phẩm | Cách trồng cây trong nhà | thấp | -0.2132 | Có |
| 3 | Bảo hành điện thoại | Sửa lỗi máy tính | thấp | 0.0939 | Có |
| 4 | Người mua yêu cầu hoàn tiền | Seller can ship the order | thấp | -0.0293 | Có |
| 5 | Thời hạn đổi trả | Thời tiết hôm nay | thấp | 0.0671 | Có |

Kết quả cặp 1 thấp hơn dự đoán dù hai câu có ý nghĩa gần nhau. Điều này cho thấy `_mock_embed` chỉ phù hợp để kiểm tra tính đúng đắn của phép tính và luồng chương trình; muốn đánh giá ý nghĩa cần dùng embedding model thật như local, OpenAI hoặc Gemini.

## 5. Kết quả truy xuất của tôi

**Thiết lập đo** (`python bench.py` và `python bench.py --merge`, kết quả lưu ở `ket_qua_benchmark.txt` và `ket_qua_benchmark_merge.txt`):

- Corpus: 6 tài liệu trong `data/ecommerce/sources.csv` (5 `buyer`, 1 `seller`), crawl bằng `scripts/fetch_public_pages.py`.
- Embedding: **LexicalEmbedder** (băm unigram + bigram, chạy offline). Đây **không phải** mô hình ngữ nghĩa: `sentence-transformers` đã cài nhưng mạng quá chậm để tải model `paraphrase-multilingual-MiniLM-L12-v2`, còn `MockEmbedder` chỉ sinh nhiễu nên không dùng. Kết quả bên dưới vì vậy phản ánh khả năng khớp từ khóa, chưa phải chất lượng ngữ nghĩa thật.
- LLM của Agent: `Mock LLM Fallback` (không có `GEMINI_API_KEY`). Nó chỉ chép lại vài dòng đầu của context chứ không suy luận, nên cột "Câu trả lời của Agent" **không** dùng để chấm đúng/sai; tôi đánh giá bằng việc top-3 chunk có chứa ý chính của gold answer hay không.
- Chiến lược cá nhân: `RecursiveChunker(chunk_size=1200)` **gộp các mảnh ngắn** (`--merge`), so với `RecursiveChunker` gốc làm baseline.
- 5 câu hỏi và gold answer lấy nhóm thống nhất trong `bench.py`. Tôi đã đối chiếu lại với nguồn đã crawl và sửa gold answer của Q3, Q4, Q5 cho khớp nguồn (bản cũ có số liệu không có trong tài liệu như "30 ngày" của TNC).

### Kết quả với chiến lược cá nhân (gộp chunk, 62 chunk, độ dài ~1.100 ký tự)

| # | Câu hỏi | Top-1 chunk (tài liệu) | Score | Relevant? | Ý chính của gold answer có trong top-3 |
|---|---|---|---:|---|---|
| 1 | Khách hàng có được mở seal hộp iPhone kiểm tra trước khi thanh toán không? | cellphones-shipping-policy (top-2: cellphones-apple-unboxing) | 0.2908 | Có (tài liệu đích cellphones-apple-unboxing nằm ở top-2, không phải top-1) | 1/1 |
| 2 | Thay pin Macbook tại Điện Thoại Vui được bảo hành bao lâu và bảo hành những lỗi gì? | dienthoaivui-repair-warranty | 0.3233 | Có | 3/3 (12 tháng, pin chai, pin phồng) |
| 3 | Doanh nghiệp mua số lượng lớn có chính sách chiết khấu và xuất hóa đơn VAT như thế nào? (lọc `audience=seller`) | cellphones-b2b-seller | 0.2558 | Có (một phần) | 1/3 (chỉ có "đến 8%", thiếu phần hóa đơn điện tử) |
| 4 | Điều kiện để linh kiện máy tính được đổi mới tại TNC Store là gì? | tnc-store-warranty | 0.2587 | Có | 3/3 (3 năm, 6 tháng, đánh giá của TNC) |
| 5 | Gói bảo hành mở rộng rơi vỡ vào nước tại CellphoneS có những quyền lợi gì? | cellphones-extended-warranty | 0.3343 | Có | 3/3 (90%, 1 đổi 1 VIP, đổi máy tương đương) |

**Số câu hỏi có chunk liên quan trong top-3:** 5 / 5 (xét theo tài liệu đích). Xét chặt hơn, theo việc top-3 chứa **đủ** ý chính của gold answer: 4 / 5 câu (Q1, Q2, Q4, Q5), riêng Q3 chỉ được 1/3 ý. Lưu ý Q1 chỉ kiểm 1 ý ("thanh toán 100%") nên bằng chứng yếu hơn các câu còn lại.

### So sánh với baseline (RecursiveChunker gốc, không gộp)

| Chỉ số | Baseline | Gộp chunk (cá nhân) |
|---|---:|---:|
| Số chunk | 1695 | 62 |
| Độ dài 3 chunk top-3 | 18–137 ký tự | 918–1200 ký tự |
| Câu có tài liệu đích trong top-3 | 5 / 5 | 5 / 5 |
| Độ phủ ý chính của gold answer (tổng trên 5 câu) | 1,33 | 4,33 |

**Nhận xét.** Chỉ số "tài liệu đích nằm trong top-3" không phân biệt được hai chiến lược (cùng 5/5), nhưng chỉ số ý chính thì chênh rất lớn. Nguyên nhân: `RecursiveChunker` tách theo từng đoạn và không gộp lại, nên trang web đã crawl (nhiều dòng menu, tiêu đề ngắn) bị vỡ thành các mảnh 18–137 ký tự như "Linh kiện máy tính..." hoặc "Apple mới & Máy ảnh...". Các mảnh này khớp từ khóa với câu hỏi nhưng không chứa đáp án. Gộp các mảnh liền kề tới 1200 ký tự giữ được ngữ cảnh và đưa đáp án vào đúng chunk.

**Phân tích lỗi Q3.** Ở cả hai chiến lược, phần "hóa đơn điện tử, không xuất hóa đơn trước khi giao hàng" nằm trong mục hỏi đáp ở cuối trang B2B, cách xa phần bảng chiết khấu. Câu hỏi ghép hai chủ đề (chiết khấu và VAT) nên top-3 chỉ kéo về các chunk về chiết khấu. Hướng cải thiện: tách câu hỏi thành hai truy vấn, hoặc chia chunk theo cặp hỏi–đáp.

**Hiệu quả của metadata filter (Q3).** Khi không lọc, top-3 của baseline có 1 chunk thuộc `cellphones-shipping-policy` (`audience=buyer`); khi lọc `audience=seller` chỉ còn chunk của `cellphones-b2b-seller`. Với chiến lược gộp chunk, top-3 không lọc đã toàn là tài liệu seller nên bộ lọc không tạo khác biệt. Do đó bằng chứng cho giá trị của bộ lọc còn yếu, và cần đo lại bằng embedding ngữ nghĩa.

**Hạn chế cần nêu rõ.**

- Chưa đo với embedding ngữ nghĩa (local/Gemini/OpenAI) và chưa có LLM thật, nên chưa đánh giá được chất lượng câu trả lời của Agent.
- Corpus chỉ có 1 tài liệu `seller`, nên bộ lọc `audience` có ít việc để làm.
- Ý chính của gold answer được kiểm bằng khớp chuỗi, có thể bỏ sót cách diễn đạt khác.

**Điều học được từ thành viên khác / nhóm khác:**

[Điền sau buổi so sánh và demo nhóm — phần này chỉ điền được sau khi các thành viên khác chạy xong chiến lược của họ.]

## Tự đánh giá

| Tiêu chí | Điểm tự đánh giá |
|---|---:|
| Khởi động | 5 / 5 |
| Hướng tiếp cận | 10 / 10 |
| Hoàn thiện code - tests | 30 / 30 |
| Dự đoán độ tương tự | 5 / 5 |
| Kết quả truy xuất | 7 / 10 |
| **Tổng** | **57 / 60** |

Điểm truy xuất tự chấm 7/10 vì top-3 chứa tài liệu đích ở 5/5 câu, nhưng chỉ 4/5 câu đủ ý chính (Q3 thiếu phần hóa đơn), chưa có Agent dùng LLM thật để xác nhận câu trả lời, và chưa chạy embedding ngữ nghĩa. Đây là điểm tự đánh giá tạm thời, sẽ điều chỉnh nếu chạy lại được với mô hình thật.
