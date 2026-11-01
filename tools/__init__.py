from .base import DiscoveryTool
from .subfinder import SubfinderTool
from .assetfinder import AssetfinderTool
from .abuseipdb import AbuseIPDBTool
from .crtsh import CrtShTool
from .wayback import WaybackTool
from .gau import GauTool
from .dnsbrute import DnsBruteTool
from .dnsx_resolver import DnsxResolver
from .httpx_prober import HttpxProber
from .crawler import WaybackCrawler, KatanaCrawler

# ── discovery tools ──────────────────────────────────────────────────
ALL_TOOLS: list[DiscoveryTool] = [
    SubfinderTool(),
    AssetfinderTool(),
    AbuseIPDBTool(),
    CrtShTool(),
    WaybackTool(),
    GauTool(),
    DnsBruteTool(),
]

def get_tool(name: str) -> DiscoveryTool | None:
    return next((t for t in ALL_TOOLS if t.name == name), None)

def enabled_tools() -> list[DiscoveryTool]:
    return [t for t in ALL_TOOLS if t.is_available()]

def tools_status() -> list[dict]:
    return [
        {"name": t.name, "description": t.description, "available": t.is_available()}
        for t in ALL_TOOLS
    ]

# ── resolvers (DNS verification) ─────────────────────────────────────
RESOLVERS: list[DnsxResolver] = [
    DnsxResolver(),
]

def enabled_resolvers() -> list[DnsxResolver]:
    return [r for r in RESOLVERS if r.is_available()]

def resolvers_status() -> list[dict]:
    return [
        {"name": r.name, "description": r.description, "available": r.is_available()}
        for r in RESOLVERS
    ]

# ── probers (HTTP discovery) ─────────────────────────────────────────
PROBERS: list[HttpxProber] = [
    HttpxProber(),
]

def enabled_probers() -> list[HttpxProber]:
    return [p for p in PROBERS if p.is_available()]

def probers_status() -> list[dict]:
    return [
        {"name": p.name, "description": p.description, "available": p.is_available()}
        for p in PROBERS
    ]

# ── crawlers (URL discovery) ─────────────────────────────────────────
CRAWLERS = [
    WaybackCrawler(),
    KatanaCrawler(),
]

def enabled_crawlers():
    return [c for c in CRAWLERS if c.is_available()]