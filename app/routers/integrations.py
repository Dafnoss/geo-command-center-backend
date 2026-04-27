"""
Read-only Google connectors for Search Console + GA4.

The app stores OAuth refresh credentials server-side after the user completes
Google consent. Sync is explicit; no Google data is read until /sync is called.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from sqlalchemy.orm import Session

from app import models, schemas
from app.config import settings
from app.database import get_db
from app.visibility import split_csv


router = APIRouter(prefix="/integrations/google", tags=["integrations"])

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
]


def _setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(models.Setting).filter_by(setting_key=key).one_or_none()
    return row.setting_value if row and row.setting_value else default


def _set_setting(db: Session, key: str, value: str, notes: str = "") -> None:
    row = db.query(models.Setting).filter_by(setting_key=key).one_or_none()
    if not row:
        row = models.Setting(setting_key=key, setting_value=value, notes=notes)
        db.add(row)
    else:
        row.setting_value = value
        if notes:
            row.notes = notes


def _configured() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def _client_config() -> dict[str, Any]:
    if not _configured():
        raise HTTPException(409, "Google OAuth is not configured. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET on Render.")
    return {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.google_redirect_uri],
        }
    }


def _flow(state: str | None = None) -> Flow:
    flow = Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        state=state,
        redirect_uri=settings.google_redirect_uri,
    )
    return flow


def _accounts(db: Session) -> list[models.ConnectorAccount]:
    return (
        db.query(models.ConnectorAccount)
        .filter_by(provider="google")
        .filter(models.ConnectorAccount.status == "connected")
        .all()
    )


def _account(db: Session) -> models.ConnectorAccount | None:
    return _accounts(db)[0] if _accounts(db) else None


def _credentials_for_account(db: Session, account: models.ConnectorAccount) -> Credentials:
    if not account or not account.token_json:
        raise HTTPException(409, "Google is not connected.")
    creds = Credentials.from_authorized_user_info(account.token_json, scopes=SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        account.token_json = json.loads(creds.to_json())
        account.updated_at = datetime.utcnow()
        db.commit()
    if not creds.valid:
        raise HTTPException(409, f"Google token is not valid for {account.account_label}. Reconnect Google.")
    return creds


def _authed_request(creds: Credentials, method: str, url: str, body: dict | None = None) -> dict:
    creds.before_request(Request(), method, url, {})
    import requests

    headers = {"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"}
    if method == "GET":
        res = requests.get(url, headers=headers, timeout=45)
    else:
        res = requests.post(url, headers=headers, json=body or {}, timeout=60)
    if res.status_code >= 400:
        raise HTTPException(res.status_code, f"Google API error: {res.text[:500]}")
    return res.json()


def _owned_domains(db: Session) -> list[str]:
    raw = "ocsial.com,tuball.com,industries.tuball.com," + _setting(db, "owned_domains", "")
    out = []
    for d in split_csv(raw):
        clean = d.lower().replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0]
        if clean and clean not in out:
            out.append(clean)
    return out


def _wanted_sites(db: Session, available: list[str]) -> list[str]:
    configured = split_csv(_setting(db, "google_search_console_sites", ""))
    if configured:
        return [s for s in configured if s in available or s.startswith(("http://", "https://", "sc-domain:"))]
    owned = _owned_domains(db)
    selected = []
    for site in available:
        low = site.lower()
        if any(d in low for d in owned):
            selected.append(site)
    return selected or available[:2]


def _list_sites(creds: Credentials) -> list[str]:
    data = _authed_request(creds, "GET", "https://www.googleapis.com/webmasters/v3/sites")
    return [s.get("siteUrl", "") for s in data.get("siteEntry", []) if s.get("siteUrl")]


def _sync_search_console(db: Session, start: date, end: date) -> tuple[list[str], int]:
    db.query(models.GoogleSearchMetric).delete()
    all_sites: list[str] = []
    rows_inserted = 0
    for account in _accounts(db):
        creds = _credentials_for_account(db, account)
        available = _list_sites(creds)
        sites = _wanted_sites(db, available)
        for site in sites:
            if site not in all_sites:
                all_sites.append(site)
            url = "https://www.googleapis.com/webmasters/v3/sites/" + quote(site, safe="") + "/searchAnalytics/query"
            body = {
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "dimensions": ["query", "page"],
                "rowLimit": 500,
                "startRow": 0,
            }
            data = _authed_request(creds, "POST", url, body)
            for row in data.get("rows", []):
                keys = row.get("keys") or ["", ""]
                db.add(models.GoogleSearchMetric(
                    metric_id=f"GSC-{uuid.uuid4().hex[:12]}",
                    site_url=site,
                    date_start=start,
                    date_end=end,
                    query=keys[0] if len(keys) > 0 else "",
                    page=keys[1] if len(keys) > 1 else "",
                    clicks=int(row.get("clicks") or 0),
                    impressions=int(row.get("impressions") or 0),
                    ctr=float(row.get("ctr") or 0),
                    avg_position=float(row.get("position") or 0),
                ))
                rows_inserted += 1
    _set_setting(db, "google_search_console_sites", ",".join(all_sites), "GSC site URLs synced.")
    return all_sites, rows_inserted


def _sync_analytics(db: Session, start: date, end: date) -> tuple[str, int, list[str]]:
    property_id = _setting(db, "google_ga4_property_id", "").strip()
    if not property_id:
        return "", 0, ["GA4 skipped: set google_ga4_property_id in Settings."]
    prop = property_id.replace("properties/", "")
    url = f"https://analyticsdata.googleapis.com/v1beta/properties/{prop}:runReport"
    body = {
        "dateRanges": [{"startDate": start.isoformat(), "endDate": end.isoformat()}],
        "dimensions": [{"name": "pagePath"}, {"name": "pageTitle"}],
        "metrics": [{"name": "activeUsers"}, {"name": "sessions"}, {"name": "conversions"}],
        "limit": 500,
    }
    db.query(models.GoogleAnalyticsMetric).delete()
    data = None
    errors: list[str] = []
    for account in _accounts(db):
        creds = _credentials_for_account(db, account)
        try:
            data = _authed_request(creds, "POST", url, body)
            break
        except HTTPException as exc:
            errors.append(f"{account.account_label}: {exc.detail}")
    if data is None:
        return prop, 0, ["GA4 sync failed for all connected Google accounts. " + " | ".join(errors)[:500]]
    inserted = 0
    for row in data.get("rows", []):
        dims = [v.get("value", "") for v in row.get("dimensionValues", [])]
        mets = [v.get("value", "0") for v in row.get("metricValues", [])]
        db.add(models.GoogleAnalyticsMetric(
            metric_id=f"GA4-{uuid.uuid4().hex[:12]}",
            property_id=prop,
            date_start=start,
            date_end=end,
            page_path=dims[0] if len(dims) > 0 else "",
            page_title=dims[1] if len(dims) > 1 else "",
            active_users=int(float(mets[0])) if len(mets) > 0 else 0,
            sessions=int(float(mets[1])) if len(mets) > 1 else 0,
            conversions=float(mets[2]) if len(mets) > 2 else 0.0,
        ))
        inserted += 1
    return prop, inserted, []


@router.get("/status", response_model=schemas.GoogleConnectorStatus)
def google_status(db: Session = Depends(get_db)):
    accounts = _accounts(db)
    last_sync = max((a.last_sync_at for a in accounts if a.last_sync_at), default=None)
    return schemas.GoogleConnectorStatus(
        configured=_configured(),
        connected=bool(accounts),
        status="connected" if accounts else "disconnected",
        account_label=", ".join(a.account_label for a in accounts),
        scopes=SCOPES if accounts else [],
        last_sync_at=last_sync,
        search_console_sites=split_csv(_setting(db, "google_search_console_sites", "")),
        ga4_property_id=_setting(db, "google_ga4_property_id", ""),
        search_rows=db.query(models.GoogleSearchMetric).count(),
        analytics_rows=db.query(models.GoogleAnalyticsMetric).count(),
    )


@router.get("/auth-url", response_model=schemas.GoogleAuthUrlOut)
def google_auth_url(db: Session = Depends(get_db)):
    state = uuid.uuid4().hex
    _set_setting(db, "google_oauth_state", state, "Latest Google OAuth state token.")
    db.commit()
    authorization_url, _ = _flow(state=state).authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent select_account",
    )
    return schemas.GoogleAuthUrlOut(authorization_url=authorization_url)


@router.get("/oauth/callback")
def google_callback(code: str = "", state: str = "", db: Session = Depends(get_db)):
    expected = _setting(db, "google_oauth_state", "")
    if not code or not state or state != expected:
        raise HTTPException(400, "Invalid OAuth callback state.")
    flow = _flow(state=state)
    flow.fetch_token(code=code)
    creds = flow.credentials
    token_json = json.loads(creds.to_json())
    info = _authed_request(creds, "GET", "https://www.googleapis.com/oauth2/v2/userinfo")
    email = (info.get("email") or f"google-{uuid.uuid4().hex[:8]}").lower()
    account = db.query(models.ConnectorAccount).filter_by(connector_id=f"google:{email}").one_or_none()
    if not account:
        account = models.ConnectorAccount(
            connector_id=f"google:{email}",
            provider="google",
            account_label=email,
        )
        db.add(account)
    account.account_label = email
    account.scopes = SCOPES
    account.token_json = token_json
    account.status = "connected"
    account.updated_at = datetime.utcnow()
    db.commit()
    return HTMLResponse("""
      <html><body style="font-family: system-ui; padding: 32px;">
      <h2>Google connected</h2>
      <p>You can close this tab and return to GEO Command Center.</p>
      </body></html>
    """)


@router.post("/disconnect")
def google_disconnect(db: Session = Depends(get_db)):
    for account in _accounts(db):
        account.status = "disconnected"
        account.token_json = {}
        account.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.post("/sync", response_model=schemas.GoogleSyncOut)
def google_sync(db: Session = Depends(get_db)):
    end = date.today() - timedelta(days=2)
    start = end - timedelta(days=27)
    warnings: list[str] = []
    sites, search_rows = _sync_search_console(db, start, end)
    _, analytics_rows, ga_warnings = _sync_analytics(db, start, end)
    warnings.extend(ga_warnings)
    for account in _accounts(db):
        account.last_sync_at = datetime.utcnow()
        account.updated_at = datetime.utcnow()
    db.commit()
    return schemas.GoogleSyncOut(
        ok=True,
        search_console_sites=sites,
        search_rows=search_rows,
        analytics_rows=analytics_rows,
        warnings=warnings,
    )


@router.get("/search-metrics", response_model=list[schemas.GoogleSearchMetricOut])
def list_search_metrics(limit: int = 100, db: Session = Depends(get_db)):
    return (
        db.query(models.GoogleSearchMetric)
        .order_by(models.GoogleSearchMetric.impressions.desc())
        .limit(min(limit, 500))
        .all()
    )


@router.get("/analytics-metrics", response_model=list[schemas.GoogleAnalyticsMetricOut])
def list_analytics_metrics(limit: int = 100, db: Session = Depends(get_db)):
    return (
        db.query(models.GoogleAnalyticsMetric)
        .order_by(models.GoogleAnalyticsMetric.sessions.desc())
        .limit(min(limit, 500))
        .all()
    )
