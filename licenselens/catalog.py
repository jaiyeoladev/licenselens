"""Microsoft 365 SKU catalogue: friendly names, indicative prices and licence overlaps.

Prices are INDICATIVE monthly per-user list prices in GBP (annual commitment, excluding VAT)
used only to estimate waste. Replace them with the prices on your own agreement in the
Pricing page — every saving the app shows is calculated from these numbers.
"""

from __future__ import annotations

SKU_NAMES: dict[str, str] = {
    "SPE_E3": "Microsoft 365 E3",
    "SPE_E5": "Microsoft 365 E5",
    "SPE_F1": "Microsoft 365 F3",
    "SPB": "Microsoft 365 Business Premium",
    "O365_BUSINESS_PREMIUM": "Microsoft 365 Business Standard",
    "O365_BUSINESS_ESSENTIALS": "Microsoft 365 Business Basic",
    "STANDARDPACK": "Office 365 E1",
    "ENTERPRISEPACK": "Office 365 E3",
    "ENTERPRISEPREMIUM": "Office 365 E5",
    "EXCHANGESTANDARD": "Exchange Online (Plan 1)",
    "EXCHANGEENTERPRISE": "Exchange Online (Plan 2)",
    "EMS": "Enterprise Mobility + Security E3",
    "EMSPREMIUM": "Enterprise Mobility + Security E5",
    "AAD_PREMIUM": "Microsoft Entra ID P1",
    "AAD_PREMIUM_P2": "Microsoft Entra ID P2",
    "POWER_BI_PRO": "Power BI Pro",
    "POWER_BI_STANDARD": "Power BI (free)",
    "VISIOCLIENT": "Visio Plan 2",
    "PROJECTPROFESSIONAL": "Project Plan 3",
    "PROJECTPREMIUM": "Project Plan 5",
    "Microsoft_365_Copilot": "Microsoft 365 Copilot",
    "FLOW_FREE": "Power Automate (free)",
    "TEAMS_EXPLORATORY": "Teams Exploratory",
    "WINDOWS_STORE": "Windows Store for Business",
    "STREAM": "Microsoft Stream",
}

# Indicative GBP per user per month. Edit on the Pricing page.
DEFAULT_PRICES: dict[str, float] = {
    "SPE_E3": 33.00,
    "SPE_E5": 52.20,
    "SPE_F1": 7.50,
    "SPB": 18.10,
    "O365_BUSINESS_PREMIUM": 10.30,
    "O365_BUSINESS_ESSENTIALS": 4.90,
    "STANDARDPACK": 8.20,
    "ENTERPRISEPACK": 20.60,
    "ENTERPRISEPREMIUM": 35.80,
    "EXCHANGESTANDARD": 3.30,
    "EXCHANGEENTERPRISE": 6.60,
    "EMS": 8.20,
    "EMSPREMIUM": 12.90,
    "AAD_PREMIUM": 5.10,
    "AAD_PREMIUM_P2": 7.60,
    "POWER_BI_PRO": 11.00,
    "VISIOCLIENT": 12.30,
    "PROJECTPROFESSIONAL": 24.70,
    "PROJECTPREMIUM": 45.30,
    "Microsoft_365_Copilot": 24.70,
}

# Free, viral and self-service SKUs that never represent spend.
FREE_SKUS = {
    "POWER_BI_STANDARD", "FLOW_FREE", "TEAMS_EXPLORATORY", "WINDOWS_STORE", "STREAM",
    "POWERAPPS_VIRAL", "CCIBOTS_PRIVPREV_VIRAL", "POWERAPPS_DEV", "MCOPSTNC",
    "RIGHTSMANAGEMENT_ADHOC", "FORMS_PRO", "Microsoft_Teams_Exploratory_Dept",
}

# If a user holds the key SKU, any SKU in its list is redundant (its features are included).
DEFAULT_SUPERSEDES: dict[str, list[str]] = {
    "SPE_E5": [
        "SPE_E3", "SPB", "ENTERPRISEPREMIUM", "ENTERPRISEPACK", "STANDARDPACK", "EMS", "EMSPREMIUM",
        "EXCHANGESTANDARD", "EXCHANGEENTERPRISE", "AAD_PREMIUM", "AAD_PREMIUM_P2", "POWER_BI_PRO",
        "O365_BUSINESS_PREMIUM", "O365_BUSINESS_ESSENTIALS",
    ],
    "SPE_E3": [
        "ENTERPRISEPACK", "STANDARDPACK", "EMS", "EXCHANGESTANDARD", "EXCHANGEENTERPRISE",
        "AAD_PREMIUM", "O365_BUSINESS_PREMIUM", "O365_BUSINESS_ESSENTIALS",
    ],
    "ENTERPRISEPREMIUM": [
        "ENTERPRISEPACK", "STANDARDPACK", "EXCHANGESTANDARD", "EXCHANGEENTERPRISE", "POWER_BI_PRO",
        "O365_BUSINESS_ESSENTIALS",
    ],
    "ENTERPRISEPACK": ["STANDARDPACK", "EXCHANGESTANDARD", "EXCHANGEENTERPRISE", "O365_BUSINESS_ESSENTIALS"],
    "SPB": ["O365_BUSINESS_PREMIUM", "O365_BUSINESS_ESSENTIALS", "EXCHANGESTANDARD", "AAD_PREMIUM"],
    "O365_BUSINESS_PREMIUM": ["O365_BUSINESS_ESSENTIALS", "EXCHANGESTANDARD"],
    "EMSPREMIUM": ["EMS", "AAD_PREMIUM", "AAD_PREMIUM_P2"],
    "EMS": ["AAD_PREMIUM"],
    "AAD_PREMIUM_P2": ["AAD_PREMIUM"],
    "EXCHANGEENTERPRISE": ["EXCHANGESTANDARD"],
    "PROJECTPREMIUM": ["PROJECTPROFESSIONAL"],
}


def sku_name(part_number: str) -> str:
    return SKU_NAMES.get(part_number, part_number.replace("_", " "))


def sku_price(part_number: str, prices: dict) -> float:
    if part_number in FREE_SKUS:
        return 0.0
    try:
        return float(prices.get(part_number, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0
