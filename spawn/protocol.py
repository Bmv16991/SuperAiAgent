"""
SuperAiAgent Spawn Protocol — Open Source Spec
Defines how a domain agent is created from the Foundation.
"""
DOMAIN_PROFILES = {
    "forex-trading":   {"kb_tags": ["trading","forex","gold","MT5","technical-analysis"],  "reference": "ForexAI (F:\\ForexAIARTs)"},
    "crypto-trading":  {"kb_tags": ["crypto","blockchain","DeFi","trading","exchange"],    "reference": "CryptoAgent (F:\\CryptoAgent) — spawned 2026-07-06"},
    "fivem-scripting": {"kb_tags": ["fivem","lua","gta","roleplay","server"],              "reference": "F:\\FiveMTrainer"},
    "ui-design":       {"kb_tags": ["react","vue","css","frontend","design-system"],       "reference": None},
}

def spawn_plan(domain: str, output_dir: str) -> dict:
    if domain not in DOMAIN_PROFILES:
        return {"error": f"Unknown domain. Available: {list(DOMAIN_PROFILES.keys())}"}
    profile = DOMAIN_PROFILES[domain]
    return {
        "domain": domain,
        "kb_source": "gh_knowledge (24,850 chunks) + gh_patterns (441 patterns)",
        "kb_filter_tags": profile["kb_tags"],
        "kb_target_collection": f"{domain.replace('-','_')}_kb",
        "output_dir": output_dir,
        "reference_impl": profile["reference"],
        "steps": [
            "1. Query gh_patterns for domain tags → filter top-500 relevant patterns",
            "2. Create domain Qdrant collection",
            "3. Upsert filtered patterns into domain collection",
            "4. Configure 4 domain agents + moderator",
            "5. Generate domain .env template",
            "6. Output SPAWN_MANIFEST.md",
        ],
    }
