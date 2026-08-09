"""HTTP route modules."""

from app.api.routes import (
    admin,
    admin_paper_dashboard,
    admin_settings,
    admin_simulations,
    admin_strategies,
    app_market_candles,
    app_paper_session_detail,
    app_paper_session_performance,
    app_paper_sessions,
    assets,
    health,
    paper_trading,
    public,
    system,
)

__all__ = [
    "admin",
    "admin_paper_dashboard",
    "admin_settings",
    "admin_simulations",
    "admin_strategies",
    "app_market_candles",
    "app_paper_session_detail",
    "app_paper_session_performance",
    "app_paper_sessions",
    "assets",
    "health",
    "paper_trading",
    "public",
    "system",
]
