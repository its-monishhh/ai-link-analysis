import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.schemas import ErrorBody, ErrorResponse, LinkAnalysisRequest, LinkAnalysisResponse
from app.services.analysis_service import AnalysisFailed, analyze
from app.services.website_extractor import WebsiteFetchError, extract_website
from app.utils.validators import BlockedURLError, InvalidURLError, assert_public_url, normalize_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("link-analysis")

app = FastAPI(title="AI Website Link Analysis API", version="1.0.0",
              description="Analyses a website with an LLM: competitors, search queries, SEO, services, audience.")


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content=ErrorResponse(error=ErrorBody(code=code, message=message)).model_dump())


@app.exception_handler(RequestValidationError)
async def _validation_handler(_: Request, exc: RequestValidationError):
    return _err(422, "invalid_request", "Request body must be JSON like {\"url\": \"https://example.com\"}.")


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


@app.post("/link-analysis", response_model=LinkAnalysisResponse,
          responses={400: {"model": ErrorResponse}, 403: {"model": ErrorResponse},
                     502: {"model": ErrorResponse}, 504: {"model": ErrorResponse}})
async def link_analysis(req: LinkAnalysisRequest):
    settings = get_settings()
    try:
        url = normalize_url(req.url)
        await assert_public_url(url)
    except InvalidURLError as e:
        return _err(400, "invalid_url", str(e))
    except BlockedURLError as e:
        return _err(403, "blocked_url", str(e))

    try:
        content = await extract_website(url, settings)
    except WebsiteFetchError as e:
        status = {"timeout": 504, "access_restricted": 502}.get(e.code, 502)
        return _err(status, e.code, e.message)
    except InvalidURLError as e:
        return _err(400, "invalid_url", str(e))
    except BlockedURLError as e:
        return _err(403, "blocked_url", str(e))

    try:
        return await analyze(content, settings)
    except AnalysisFailed as e:
        log.error("analysis failed for %s: %s", url, e)
        return _err(502, "llm_failure", "The AI analysis failed. Please try again later.")
