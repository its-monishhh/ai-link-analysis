from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class LinkAnalysisRequest(BaseModel):
    url: str = Field(..., examples=["https://example.com"])


class Brand(BaseModel):
    name: str
    category: str
    summary: str


class Competitor(BaseModel):
    name: str
    website: Optional[str] = None
    strengths: List[str] = []
    weaknesses: List[str] = []
    source: str = "ai_generated_suggestion"  # not verified by live research


class ComparisonRow(BaseModel):
    comparison: str
    your_brand: str
    competitor: str


class CompetitorAnalysis(BaseModel):
    brand_overview: str
    main_category: str = ""
    main_products_services: List[str] = []
    competitors: List[Competitor] = []
    comparison_table: List[ComparisonRow] = []
    strengths: List[str] = []           # brand's competitive advantages
    opportunities: List[str] = []       # positioning opportunities
    note: str = "Competitors are AI-generated suggestions, not verified market research."


class SearchQuery(BaseModel):
    query: str
    platforms: List[Literal["Google", "Facebook", "Instagram", "LinkedIn", "X"]]
    intent: str
    business_relevance: str = ""
    target_audience: str = ""
    # Deliberately no search-volume field: no data source is integrated.


class SEOImprovements(BaseModel):
    primary_keyword: str
    secondary_keywords: List[str] = []
    long_tail_keywords: List[str] = []
    business_keywords: List[str] = []
    recommended_title: str
    title_structure: str = ""
    meta_description: str
    supporting_content: List[str] = []


class Service(BaseModel):
    name: str
    description: str
    target_industry: str = ""
    target_customer: str = ""
    main_benefit: str = ""
    source: Literal["website", "inferred"] = "website"


class AudienceSegment(BaseModel):
    audience: str
    business_type: str = ""
    requirement: str = ""


class TargetAudience(BaseModel):
    primary_audience: List[str] = []
    secondary_audience: List[str] = []
    industries: List[str] = []
    business_types: List[str] = []
    customer_profiles: List[str] = []
    geographic_focus: str = "Not specified on website"
    customer_needs: List[str] = []
    customer_interests: List[str] = []
    recommended_segments: List[AudienceSegment] = []


class Analysis(BaseModel):
    competitor_analysis: CompetitorAnalysis
    target_search_queries: List[SearchQuery]
    seo_improvements: SEOImprovements
    top_services: List[Service]
    target_audience: TargetAudience


class LLMOutput(BaseModel):
    """What the LLM must return (validated before responding)."""
    brand: Brand
    analysis: Analysis


class Meta(BaseModel):
    model: str
    cached: bool = False
    pages_analyzed: int = 1
    content_chars: int = 0
    elapsed_seconds: float = 0.0
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


class LinkAnalysisResponse(BaseModel):
    success: bool = True
    website: str
    brand: Brand
    analysis: Analysis
    meta: Meta


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    success: bool = False
    error: ErrorBody
