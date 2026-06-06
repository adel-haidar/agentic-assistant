import json
import logging
from typing import Optional

from assistant.job.models import JobListing, MatchResult
from assistant.shared.base_llm_service import BaseLLMService

logger = logging.getLogger(__name__)

_CANDIDATE_PROFILE = """\
Name: Adel Haidar | Title: Senior Software Engineer (6+ years)
Location: Stuttgart, Germany | Citizenship: German
Work rights: Unrestricted EU + Switzerland (bilateral treaty); EEA FoM for Norway; CA/SG require sponsorship
Languages: Arabic (native), German (C1+), English (C1), French (B1/B2)
Education: BSc IT-Management HdWM Mannheim + MIT ReACT Certificate
Current salary: ~EUR 65,000 Stuttgart baseline

Core stack (MUST match):
  Java 21, Spring Boot, Spring Security, Spring Batch
  Apache Kafka (producer/consumer, transactions, DLQ)
  REST APIs, OpenAPI, Microservices
  PostgreSQL, JPA/Hibernate, Flyway
  Docker, Kubernetes, OpenShift
  GitLab CI/CD, JaCoCo, Cucumber/BDD
  Vue.js 3, TypeScript

AI/cloud stack (secondary):
  AWS Bedrock, Mistral AI, Python + FastAPI
  AWS Lambda, S3, EC2, ALB, SSM

Domain: Banking & fintech (Atruvia/FiduciaGAD, apoBank, Rabobank), payments, regulatory, EUDI Wallet/OpenID4VP

Active applications — DO NOT match:
  Mistral AI, Swisscom, PostFinance, LBBW, Capgemini CH, Adobe CH, Swissquote, TeamViewer"""

_FILTER_RULES = """\
Hard disqualifiers (auto-reject, no scoring):
  WRONG_COUNTRY         Not in CH, CA, NO, SG. Remote only if employer is in target country.
  CITIZENSHIP_RESTRICTED Swiss/Canadian/local citizens only.
  NO_SPONSORSHIP        Canada or Singapore role with no visa sponsorship mentioned.
  TECHNOLOGY_MISMATCH   C++, .NET, PHP, iOS native, pure Python ML — no Java/Spring overlap.
                        If Java is listed only as a 'bonus' or 'nice to have' and the primary
                        language is Python, C++, C#, Scala, or the role title is 'Quantitative
                        Developer / Quant Developer' → HARD REJECT with TECHNOLOGY_MISMATCH.
  ALREADY_APPLIED       Company is in the active applications list.
  JUNIOR_ROLE           <3 years expected, entry-level, graduate, or trainee.
  PURE_MANAGEMENT       CTO/VP/Head with no IC track.

Soft disqualifiers (include with note):
  EXPERIENCE_GAP        Role requires >8 years; candidate has 6.
  SALARY_BELOW          Below floor (CHF 110k / CAD 95k / NOK 900k / SGD 95k).
  RELOCATION_URGENT     Must be on-site within 30 days."""

_SCORING_MODEL = """\
Technical fit (0-35):
  35 = Java + Spring Boot + Kafka + cloud
  25 = Java + Spring Boot (missing cloud or Kafka)
  15 = Scala or Kotlin with Spring
   5 = Other backend with clear overlap

Domain fit (0-25):
  25 = Banking/fintech/payments explicitly required
  15 = Financial services broadly
  10 = General enterprise backend
   5 = Other domain

Location / work mode (0-20):
  20 = Switzerland
  15 = Norway
  10 = Canada (sponsorship confirmed)
   8 = Singapore (EP confirmed)
   5 = Remote-first with target country employer
  +5 bonus for Geneva/Lausanne + French listed as asset

Salary fit (0-10):
  10 = At or above target floor
   7 = Within 10% below target
   3 = 10-20% below target
   0 = >20% below or not disclosed

AI / growth signal (0-10):
  10 = LLM/GenAI/Agentic/Bedrock/RAG/embeddings in JD
   7 = "Building AI capabilities" in JD
   3 = Traditional stack, no AI signal
   0 = Legacy only (COBOL, mainframe)

Tiers: >=70 STRONG_MATCH | 50-69 GOOD_MATCH | 30-49 WEAK_MATCH | <30 REJECT"""

_JSON_SCHEMA = """\
{
  "disqualified": true | false,
  "disqualifier_code": "TECHNOLOGY_MISMATCH" | null,
  "rejection_reason": "string" | null,
  "score": 0-100 | null,
  "match_tier": "STRONG_MATCH" | "GOOD_MATCH" | "WEAK_MATCH" | "REJECT" | null,
  "tech_flags": ["Java", "Spring Boot", ...],
  "domain_flags": ["banking", "fintech", ...],
  "positive_flags": ["BANKING_DOMAIN", "KAFKA_REQUIRED", ...],
  "soft_flags": ["EXPERIENCE_GAP", ...],
  "ai_summary": "2-3 sentences on fit",
  "salary_min_local": 120000 | null,
  "salary_max_local": 150000 | null,
  "currency": "CHF" | "CAD" | "NOK" | "SGD" | null,
  "remote_type": "remote" | "hybrid" | "onsite" | "unknown"
}"""


def _build_prompt(listing: JobListing) -> str:
    return "\n".join([
        "You are a strict job matching agent. Evaluate the listing against the candidate profile.",
        "Apply hard disqualifiers first. If not disqualified, score on the 0-100 scale.",
        "Return ONLY valid JSON — no prose, no markdown fences, nothing outside the JSON.",
        "",
        "=== CANDIDATE PROFILE ===",
        _CANDIDATE_PROFILE,
        "",
        "=== FILTER RULES ===",
        _FILTER_RULES,
        "",
        "=== SCORING MODEL ===",
        _SCORING_MODEL,
        "",
        "=== REQUIRED RETURN FORMAT (strict JSON) ===",
        _JSON_SCHEMA,
        "",
        "=== JOB LISTING ===",
        f"Title:    {listing.title}",
        f"Company:  {listing.company}",
        f"Location: {listing.location}, {listing.country}",
        f"Platform: {listing.platform}",
        f"Salary:   {listing.salary_raw or 'not disclosed'}",
        "",
        "Description:",
        listing.description[:8000],
    ])


class JobScorer(BaseLLMService):
    def score(self, listing: JobListing) -> Optional[MatchResult]:
        try:
            raw = self._strip_markdown(
                self._invoke(_build_prompt(listing), max_tokens=2048)
            )
            data = json.loads(raw)
            return MatchResult(**data)
        except Exception:
            logger.exception(
                "Scoring failed for %r at %r", listing.title, listing.company
            )
            return None
