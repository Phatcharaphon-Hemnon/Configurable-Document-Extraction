from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse

from app.core.config import get_settings
from app.schemas.documents import BatchCreateResponse, BatchStatusResponse, EvaluateRequest, EvaluateResponse, ExtractionResult
from app.services.extraction_service import DocumentExtractionService

router = APIRouter()
settings = get_settings()
service = DocumentExtractionService(settings=settings)


@router.get("/", response_class=HTMLResponse)
def home() -> str:
    supported = ", ".join(settings.supported_doc_type_list)
    return f"""
    <html>
        <head>
            <title>{settings.app_name}</title>
            <meta name="viewport" content="width=device-width, initial-scale=1" />
            <style>
                :root {{
                    color-scheme: dark;
                    --bg: #07111f;
                    --panel: rgba(10, 18, 33, 0.88);
                    --panel-border: rgba(148, 163, 184, 0.24);
                    --text: #e5eefc;
                    --muted: #9fb3ce;
                    --accent: #7dd3fc;
                    --accent-strong: #38bdf8;
                    --error: #fb7185;
                    --success: #34d399;
                }}
                * {{ box-sizing: border-box; }}
                body {{
                    margin: 0;
                    font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
                    color: var(--text);
                    background:
                        radial-gradient(circle at top left, rgba(56, 189, 248, 0.18), transparent 28%),
                        radial-gradient(circle at right 20%, rgba(125, 211, 252, 0.12), transparent 22%),
                        linear-gradient(180deg, #08101c 0%, #07111f 100%);
                    min-height: 100vh;
                }}
                .wrap {{ max-width: 1160px; margin: 0 auto; padding: 56px 20px 72px; }}
                .hero {{ display: grid; gap: 22px; grid-template-columns: 1.4fr 0.9fr; align-items: start; }}
                .panel {{
                    background: var(--panel);
                    border: 1px solid var(--panel-border);
                    border-radius: 24px;
                    box-shadow: 0 24px 80px rgba(0, 0, 0, 0.28);
                    backdrop-filter: blur(16px);
                }}
                .main {{ padding: 32px; }}
                .side {{ padding: 24px; }}
                h1 {{ margin: 0 0 12px; font-size: clamp(2rem, 4vw, 3.8rem); line-height: 1.02; }}
                .lede {{ margin: 0; color: var(--muted); font-size: 1.05rem; line-height: 1.7; max-width: 62ch; }}
                .chips {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 22px; }}
                .chip {{ border: 1px solid rgba(125, 211, 252, 0.25); color: var(--accent); border-radius: 999px; padding: 8px 12px; font-size: 0.92rem; background: rgba(8, 15, 28, 0.66); }}
                .form {{ display: grid; gap: 14px; margin-top: 28px; }}
                label {{ font-weight: 600; }}
                input[type="file"] {{
                    width: 100%;
                    padding: 16px;
                    border-radius: 16px;
                    border: 1px dashed rgba(148, 163, 184, 0.4);
                    background: rgba(7, 17, 31, 0.9);
                    color: var(--text);
                }}
                .actions {{ display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }}
                button {{
                    appearance: none;
                    border: 0;
                    border-radius: 14px;
                    padding: 14px 18px;
                    font-weight: 700;
                    color: #04111f;
                    background: linear-gradient(135deg, var(--accent), var(--accent-strong));
                    cursor: pointer;
                }}
                button:disabled {{ opacity: 0.6; cursor: not-allowed; }}
                .status {{ color: var(--muted); font-size: 0.95rem; }}
                .meta {{ display: grid; gap: 10px; margin: 0; }}
                .meta div {{ display: flex; justify-content: space-between; gap: 12px; color: var(--muted); }}
                .meta strong {{ color: var(--text); font-weight: 600; }}
                pre {{
                    margin: 0;
                    white-space: pre-wrap;
                    word-break: break-word;
                    background: #04101d;
                    border-radius: 18px;
                    border: 1px solid rgba(148, 163, 184, 0.14);
                    padding: 22px;
                    min-height: 340px;
                    overflow: auto;
                }}
                .result-head {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 12px; }}
                .result-head h2, .side h2 {{ margin: 0; font-size: 1.2rem; }}
                .hint {{ color: var(--muted); font-size: 0.95rem; line-height: 1.6; }}
                .ok {{ color: var(--success); }}
                .bad {{ color: var(--error); }}
                @media (max-width: 940px) {{
                    .hero {{ grid-template-columns: 1fr; }}
                }}
            </style>
        </head>
        <body>
            <div class="wrap">
                <div class="hero">
                    <section class="panel main">
                        <h1>{settings.app_name}</h1>
                        <p class="lede">Upload a file, run the document extraction flow, and view the resulting JSON directly in the browser. The backend still uses the same Router, extractor, validator, and judge pipeline.</p>
                        <div class="chips">
                            <span class="chip">Supported: {supported}</span>
                            <span class="chip">Upload to /extract</span>
                            <span class="chip">JSON output</span>
                        </div>

                        <form class="form" id="extract-form">
                            <div>
                                <label for="document-file">Select a document</label>
                                <div class="hint">Use invoice, purchase order, or delivery note files. Text-based files work best in this scaffold.</div>
                            </div>
                            <input id="document-file" name="file" type="file" required />
                            <div class="actions">
                                <button id="submit-button" type="submit">Extract JSON</button>
                                <span class="status" id="upload-status">Waiting for a file.</span>
                            </div>
                        </form>
                    </section>

                    <aside class="panel side">
                        <h2>What you get</h2>
                        <p class="hint">This UI submits your file to the FastAPI backend and displays the returned extraction result as formatted JSON. It is ready for your next step: replacing the stub extractors with real AI/RAG logic.</p>
                        <div class="meta">
                            <div><span>API docs</span><strong>/docs</strong></div>
                            <div><span>Extraction API</span><strong>/extract</strong></div>
                            <div><span>Batch API</span><strong>/extract/batch</strong></div>
                            <div><span>Judge API</span><strong>/evaluate</strong></div>
                        </div>
                    </aside>
                </div>

                <div style="margin-top: 22px;" class="panel main">
                    <div class="result-head">
                        <h2>Extraction result</h2>
                        <span class="status" id="result-label">No request yet</span>
                    </div>
                    <pre id="result-output">Upload a file to see the JSON result here.</pre>
                </div>
            </div>

            <script>
                const form = document.getElementById('extract-form');
                const fileInput = document.getElementById('document-file');
                const statusText = document.getElementById('upload-status');
                const resultOutput = document.getElementById('result-output');
                const resultLabel = document.getElementById('result-label');
                const submitButton = document.getElementById('submit-button');

                form.addEventListener('submit', async (event) => {{
                    event.preventDefault();

                    const file = fileInput.files && fileInput.files[0];
                    if (!file) {{
                        statusText.textContent = 'Please choose a file first.';
                        return;
                    }}

                    const formData = new FormData();
                    formData.append('file', file);

                    submitButton.disabled = true;
                    statusText.textContent = `Uploading ${{file.name}}...`;
                    resultLabel.textContent = 'Running extraction';

                    try {{
                        const response = await fetch('/extract', {{
                            method: 'POST',
                            body: formData,
                        }});

                        const payload = await response.json();

                        if (!response.ok) {{
                            throw new Error(payload.detail || 'Extraction failed');
                        }}

                        statusText.textContent = `Done. Extracted ${{file.name}}`;
                        resultLabel.textContent = 'Success';
                        resultLabel.className = 'status ok';
                        resultOutput.textContent = JSON.stringify(payload, null, 2);
                    }} catch (error) {{
                        statusText.textContent = 'Extraction failed.';
                        resultLabel.textContent = 'Error';
                        resultLabel.className = 'status bad';
                        resultOutput.textContent = JSON.stringify({{ error: String(error.message || error) }}, null, 2);
                    }} finally {{
                        submitButton.disabled = false;
                    }}
                }});
            </script>
        </body>
    </html>
    """


@router.post("/extract", response_model=ExtractionResult)
async def extract_document(file: UploadFile = File(...)) -> ExtractionResult:
    contents = await file.read()
    text = contents.decode("utf-8", errors="ignore")
    try:
        return service.extract(filename=file.filename or "uploaded-document", content_type=file.content_type, text=text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/templates")
def list_templates() -> dict[str, object]:
    return {
        "supported_doc_types": settings.supported_doc_type_list,
        "templates": service.list_templates(),
    }


@router.post("/extract/batch", response_model=BatchCreateResponse)
def create_batch() -> BatchCreateResponse:
    return service.create_batch()


@router.get("/jobs/{job_id}", response_model=BatchStatusResponse)
def get_job(job_id: str) -> BatchStatusResponse:
    from uuid import UUID

    try:
        parsed_job_id = UUID(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid job id") from exc

    result = service.get_batch_status(parsed_job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return result


@router.post("/evaluate", response_model=EvaluateResponse)
def evaluate(request: EvaluateRequest) -> EvaluateResponse:
    return service.evaluate(
        prediction=request.prediction,
        ground_truth=request.ground_truth,
        source_text=request.source_text,
    )
