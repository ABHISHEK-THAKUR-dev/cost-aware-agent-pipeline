"""
Request/response validation. This is the first line of input-validation defense —
see docs/rule.md Security Rules > Input validation. FastAPI rejects anything that doesn't
match these before handler code runs.
"""
from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    context_documents: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Optional short reference strings (not files — use /v1/upload for files)",
    )


class TokenReport(BaseModel):
    total_input_tokens: int
    total_output_tokens: int
    per_step: dict[str, dict[str, int]]


class StepTrace(BaseModel):
    step: str
    status: str
    latency_ms: float


class QueryResponse(BaseModel):
    trace_id: str
    answer: str
    token_report: TokenReport
    steps: list[StepTrace]


class HealthResponse(BaseModel):
    status: str
    environment: str


class UploadResponse(BaseModel):
    trace_id: str
    stored_name: str
    original_filename: str
    size_bytes: int
    status: str
