"""Read-only acceptance of the public Pages research JSON snapshot.

This verifies the four named public JSON resources and completed-session data
freshness, not every mobile network, the HTML UI, real-time quotes, model accuracy
or a guaranteed publication SLA. Morning report readiness remains independent.
No account, token, data mutation, order or report-generation interface is used.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

from engine.market_data import CalendarUnavailable, _validate_calendar

HERE = Path(__file__).resolve().parent
TZ = dt.timezone(dt.timedelta(hours=8))
BASE_URL = "https://dopagip00.github.io/gup/"
SITE_URL = BASE_URL
TIMEOUT_SECONDS = 5
MAX_ATTEMPTS = 2
MAX_BYTES = {"manifest": 64 * 1024, "daily-plan": 1024 * 1024,
             "calendar": 128 * 1024, "system": 32 * 1024}
RESOURCE_URLS = {name: BASE_URL + "data/" + name + ".json" for name in MAX_BYTES}


class HealthError(ValueError):
    def __init__(self, code, detail, details=None):
        super().__init__(detail)
        self.code, self.detail = code, detail
        self.details = details or {}


def _allowed(url):
    parsed = urllib.parse.urlsplit(str(url))
    return (parsed.scheme == "https" and parsed.netloc == "dopagip00.github.io"
            and not parsed.query and not parsed.fragment
            and str(url) in RESOURCE_URLS.values())


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 1
    max_repeats = 1

    def redirect_request(self, request, fp, code, message, headers, newurl):
        if not _allowed(newurl):
            raise HealthError("redirect_rejected", "公开检查拒绝跳转到四个固定JSON资源之外。")
        return super().redirect_request(request, fp, code, message, headers, newurl)


def _download(url):
    if not _allowed(url):
        raise HealthError("url_rejected", "公开检查只允许固定仓库的四个HTTPS JSON资源。")
    name = next(name for name, candidate in RESOURCE_URLS.items() if candidate == url)
    limit = MAX_BYTES[name]
    request = urllib.request.Request(url, headers={
        "User-Agent": "gup-public-snapshot-health/1", "Accept": "application/json",
        "Cache-Control": "no-cache", "Pragma": "no-cache"})
    opener = urllib.request.build_opener(_SameOriginRedirect())
    with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        if not _allowed(response.geturl()) or response.geturl() != url:
            raise HealthError("response_url_mismatch", "返回资源地址与所检查的公开JSON不一致。")
        if response.status != 200:
            raise HealthError("http_status", f"{name}.json 未返回HTTP 200。")
        length = response.headers.get("Content-Length")
        if length is not None:
            try:
                if int(length) < 0 or int(length) > limit:
                    raise HealthError("response_too_large", f"{name}.json 超过检查大小限制。")
            except ValueError as exc:
                if isinstance(exc, HealthError):
                    raise
                raise HealthError("invalid_content_length", f"{name}.json 长度声明无效。") from exc
        value = response.read(limit + 1)
    if len(value) > limit:
        raise HealthError("response_too_large", f"{name}.json 超过检查大小限制。")
    return value


def _load(name, loader):
    value = loader(RESOURCE_URLS[name])
    if isinstance(value, dict):
        value = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    elif isinstance(value, str):
        value = value.encode("utf-8")
    if not isinstance(value, bytes):
        raise HealthError("invalid_response", f"{name}.json 不是JSON对象响应。")
    if len(value) > MAX_BYTES[name]:
        raise HealthError("response_too_large", f"{name}.json 超过检查大小限制。")
    try:
        result = json.loads(value.decode("utf-8-sig"), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except (UnicodeError, ValueError) as exc:
        raise HealthError("invalid_json", f"{name}.json 无法作为有限数值JSON解析。") from exc
    if not isinstance(result, dict):
        raise HealthError("invalid_json_object", f"{name}.json 顶层必须为对象。")
    return result


def _instant(value, label):
    try:
        result = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("missing timezone")
    except (TypeError, ValueError) as exc:
        raise HealthError("invalid_timestamp", f"{label} 缺少真实带时区时间。") from exc
    return result.astimezone(TZ)


def _date(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise HealthError("invalid_date", f"{label} 日期格式无效。")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise HealthError("invalid_date", f"{label} 日期无效。") from exc


def _calendar(raw, day, validated):
    if day.year not in validated:
        value = raw.get(str(day.year))
        try:
            validated[day.year] = _validate_calendar(value, day.year)
        except (CalendarUnavailable, ValueError) as exc:
            raise HealthError("calendar_unverified", f"{day.year}年官方交易日历不能核验：{exc}") from exc
    return validated[day.year]


def _trading_day(raw, day, validated):
    calendar = _calendar(raw, day, validated)
    return day.weekday() < 5 and day.isoformat() not in calendar["holidays"]


def _last_completed(raw, moment, validated):
    day = moment.date()
    if moment.time().replace(tzinfo=None) < dt.time(15, 20):
        day -= dt.timedelta(days=1)
    for _ in range(40):
        if _trading_day(raw, day, validated):
            return day.isoformat()
        day -= dt.timedelta(days=1)
    raise HealthError("calendar_no_completed_session", "40天内未找到可核验的完整交易日。")


def _validate(manifest, plan, calendars, system, moment, require_morning, expected_version):
    if manifest.get("repository") != "Dopagip00/gup" or manifest.get("privacy") != "public-market-research-only":
        raise HealthError("snapshot_identity", "manifest不是指定仓库的公开研究快照。")
    if type(manifest.get("snapshot_schema_version")) is not int or manifest["snapshot_schema_version"] != 1:
        raise HealthError("snapshot_schema", "manifest快照协议版本缺失或不兼容。")
    if type(manifest.get("stock_files")) is not int or manifest["stock_files"] < 1:
        raise HealthError("snapshot_empty", "manifest未声明任何股票研究详情。")
    version = system.get("version")
    if not isinstance(version, dict) or system.get("hosted") is not True or version.get("project_id") != "huyan-ashare":
        raise HealthError("system_identity", "system不是已托管的沪研研究项目。")
    app_version = manifest.get("app_version")
    if (not isinstance(app_version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", app_version)
            or app_version != version.get("version") or app_version != expected_version):
        raise HealthError("version_mismatch", "manifest、system与待验收源码版本不一致。")
    if (type(manifest.get("data_schema_version")) is not int or manifest["data_schema_version"] != 1
            or type(version.get("data_schema_version")) is not int
            or manifest["data_schema_version"] != version.get("data_schema_version")):
        raise HealthError("data_schema", "公开研究数据版本缺失或不兼容。")
    generated = _instant(manifest.get("generated_at"), "manifest.generated_at")
    if generated > moment:
        raise HealthError("future_snapshot", "快照生成时间晚于真实检查时间。")
    data_date = _date(manifest.get("data_date"), "manifest.data_date")
    validated = {}
    trading = _trading_day(calendars, moment.date(), validated)
    latest = _last_completed(calendars, moment, validated)
    generated_latest = _last_completed(calendars, generated, validated)
    if (not _trading_day(calendars, data_date, validated)
            or data_date.isoformat() > min(latest, generated_latest)):
        raise HealthError("invalid_data_session", "行情日不是已完成的官方交易日，或晚于快照当时可用日期。")
    if plan.get("mode") != "manual_research" or plan.get("timezone") != "Asia/Shanghai":
        raise HealthError("plan_identity", "日报研究模式或时区不符合既有协议。")
    if "buy" not in plan or (plan["buy"] is not None and not isinstance(plan["buy"], dict)):
        raise HealthError("plan_buy_shape", "日报买入研究字段缺失或格式无效；null是有效的无合格推荐状态。")
    anchor = dt.datetime.combine(moment.date(), dt.time(9), TZ)
    expected_morning = _last_completed(calendars, anchor, validated) if trading else latest
    # Daily bars may lag the closing bell. The previous session is the minimum
    # acceptable data date until 16:20; genuinely completed newer data is allowed.
    # Afterwards an old but readable snapshot is a failed freshness check.
    before_close_deadline = trading and moment.time().replace(tzinfo=None) < dt.time(16, 20)
    expected = expected_morning if before_close_deadline else latest
    data_ready = data_date.isoformat() >= expected
    awaiting_close = (data_ready and before_close_deadline and latest > data_date.isoformat())
    data_status = "await_close_publication" if awaiting_close else "ready" if data_ready else "stale"
    morning_ready = False
    morning_errors = []
    if trading:
        conditions = [
            (plan.get("status") == "published", "morning_not_published", "当天日报未发布。"),
            (plan.get("date") == moment.date().isoformat(), "morning_wrong_date", "日报日期不是今天。"),
            (plan.get("morning_ready") is True, "morning_not_ready", "当天08/09研究与来源核验尚未齐备。"),
            (plan.get("late") is False, "morning_late", "当天日报为迟到恢复或未声明非迟到状态。"),
            (plan.get("data_date") == expected_morning,
             "morning_data_date", "晨报行情日未与09:00时可用的官方最近完整交易日一致。"),
        ]
        for passed, code, detail in conditions:
            if not passed:
                morning_errors.append({"code": code, "detail": detail})
        try:
            published = _instant(plan.get("published_at"), "daily-plan.published_at")
            cutoff = anchor.replace(hour=9, minute=30)
            if not (anchor <= published < cutoff and published <= generated <= moment):
                raise HealthError("morning_time", "日报实际发布时间不在今天09:00至09:29，或晚于快照/真实检查时间。")
        except HealthError as exc:
            morning_errors.append({"code": exc.code, "detail": exc.detail})
        morning_ready = not morning_errors
    details = {"status": "healthy" if trading else "skipped", "version": app_version,
            "generated_at": generated.isoformat(), "data_date": data_date.isoformat(),
            "expected_data_date": expected, "latest_completed_session": latest,
            "snapshot_readable": True, "data_ready": data_ready, "data_status": data_status,
            "expected_morning_data_date": expected_morning,
            "data_fresh_for_morning": data_date.isoformat() == expected_morning,
            "close_publication_deadline": "16:20 Asia/Shanghai",
            "trading_day": trading,
            "morning": {"required": require_morning, "ready": morning_ready,
                        "status": "ready" if morning_ready else "unready" if trading else "closed",
                        "issues": morning_errors},
            "message": ("四个公开JSON快照可读且一致，等待16:20收盘更新验收。" if awaiting_close
                        else "四个公开JSON快照可读且一致，行情日期符合当前完整交易日要求。" if trading
                        else "官方交易日历休市，行情日期符合最近完整交易日，跳过晨报要求。")}
    if not data_ready:
        details.update(status="stale", message="公开快照可读，但完整交易日行情尚未更新。")
        raise HealthError("stale_market_data",
                          f"公开行情仍为{data_date.isoformat()}，当前应至少更新至{expected}；快照可读不代表行情新鲜。",
                          details)
    if require_morning and trading and morning_errors:
        raise HealthError("morning_unready", "；".join(item["detail"] for item in morning_errors),
                          {**details, "status": "error", "message": "快照可读且行情日期合格，但盘前日报未通过核验。"})
    return details


def check_site(*, now=None, loader=None, require_morning=False, expected_version=None, attempts=2):
    """Return JSON-serializable health, loading only four fixed public resources.

    A test loader receives each complete fixed URL and returns dict, UTF-8 bytes
    or text. Each attempt reads manifest before and after the other resources.
    All checks require completed-session freshness after the 16:20 close-data
    deadline. ``require_morning`` separately requests the immutable morning
    report acceptance; ordinary checks do not demand a buy signal.
    """
    supplied_now = now is not None
    moment = now or dt.datetime.now(TZ)
    if not isinstance(moment, dt.datetime) or moment.tzinfo is None:
        return {"ok": False, "status": "error", "attempts": 0, "site": SITE_URL,
                "errors": [{"code": "invalid_now", "detail": "检查必须使用真实带时区时间。"}]}
    moment = moment.astimezone(TZ)
    base = {"ok": False, "status": "error", "checked_at": moment.isoformat(),
            "site": SITE_URL, "scope": "four-public-json-resources-only", "attempts": 0,
            "snapshot_readable": False, "data_ready": False, "data_status": "unverified",
            "require_morning": bool(require_morning)}
    if type(attempts) is not int or not 1 <= attempts <= MAX_ATTEMPTS or type(require_morning) is not bool:
        return {**base, "errors": [{"code": "invalid_options", "detail": "检查最多两轮，晨报要求必须为显式布尔值。"}]}
    try:
        if expected_version is None:
            expected_version = json.loads((HERE / "engine/version.json").read_text(encoding="utf-8"))["version"]
        if not isinstance(expected_version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", expected_version):
            raise HealthError("invalid_expected_version", "待验收源码版本无效。")
    except (KeyError, OSError, ValueError) as exc:
        return {**base, "errors": [{"code": getattr(exc, "code", "source_version_unavailable"), "detail": str(exc)}]}
    failures = []
    details = {}
    for attempt in range(1, attempts + 1):
        try:
            first = _load("manifest", loader or _download)
            plan = _load("daily-plan", loader or _download)
            calendars = _load("calendar", loader or _download)
            system = _load("system", loader or _download)
            last = _load("manifest", loader or _download)
            if first != last:
                raise HealthError("mixed_deployment", "检查期间manifest发生变化，拒绝将不同部署的文件混读为验收通过。")
            if not supplied_now:
                moment = dt.datetime.now(TZ)
                base["checked_at"] = moment.isoformat()
            details = _validate(first, plan, calendars, system, moment, require_morning, expected_version)
            return {**base, **details, "ok": True, "attempts": attempt,
                    "expected_version": expected_version, "errors": [], "previous_attempt_errors": failures}
        except (HealthError, OSError, ValueError, TypeError) as exc:
            details = getattr(exc, "details", {})
            failures.append({"attempt": attempt, "code": getattr(exc, "code", "resource_unavailable"),
                             "detail": getattr(exc, "detail", str(exc))[:1000]})
    return {**base, **details, "attempts": attempts, "expected_version": expected_version, "errors": failures}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-morning", action="store_true", help="交易日严格核验真实09:00至09:29晨报；休市跳过")
    parser.add_argument("--expected-version", help="待验收版本；默认读取engine/version.json")
    parser.add_argument("--attempts", type=int, choices=(1, 2), default=2)
    args = parser.parse_args(argv)
    result = check_site(require_morning=args.require_morning, expected_version=args.expected_version, attempts=args.attempts)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
