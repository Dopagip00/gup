"""Read-only acceptance of the independent public daily observation ranking.

This does not accept a formal buy recommendation or claim model accuracy. A
genuinely prepared static ranking may become visible after its release clock
without inventing a server publication timestamp.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path
import re
import urllib.parse
import urllib.request

from cloud_health import HealthError, TZ, _date, _instant, _last_completed, _trading_day

HERE = Path(__file__).resolve().parent
BASE_URL = "https://dopagip00.github.io/gup/"
MAX_ATTEMPTS, TIMEOUT_SECONDS = 2, 5
MAX_BYTES = {"manifest": 64 * 1024, "calendar": 128 * 1024, "daily-watchlist": 128 * 1024}
RESOURCE_URLS = {name: BASE_URL + "data/" + name + ".json" for name in MAX_BYTES}
PROTOCOL = "daily-observation-v1"
ARCHIVED_RELEASE_BASES = frozenset(("archived_preparation_not_recomputed", "prepared_cohort_frozen_at_release"))
SYMBOL = re.compile(r"sh(?:60[0135]|68[89])\d{3}")
PRICE_FIELDS = ("entry_low", "entry_high", "stop_loss", "take_profit", "max_position_pct")


def _allowed(url):
    parsed = urllib.parse.urlsplit(str(url))
    return (parsed.scheme == "https" and parsed.netloc == "dopagip00.github.io"
            and not parsed.query and not parsed.fragment
            and (str(url) in RESOURCE_URLS.values()
                 or re.fullmatch(re.escape(BASE_URL) + r"data/watchlists/\d{4}-\d{2}-\d{2}\.json", str(url)) is not None))


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = max_repeats = 1

    def redirect_request(self, request, fp, code, message, headers, newurl):
        if not _allowed(newurl):
            raise HealthError("redirect_rejected", "观察榜检查拒绝跳转到三个固定公开JSON之外。")
        return super().redirect_request(request, fp, code, message, headers, newurl)


def _download(url):
    if not _allowed(url):
        raise HealthError("url_rejected", "观察榜检查只允许本仓库三个固定HTTPS JSON。")
    name = next((name for name, known in RESOURCE_URLS.items() if known == url), "daily-watchlist")
    limit = MAX_BYTES[name]
    request = urllib.request.Request(url, headers={"User-Agent": "gup-watchlist-health/1",
        "Accept": "application/json", "Cache-Control": "no-cache", "Pragma": "no-cache"})
    opener = urllib.request.build_opener(_SameOriginRedirect())
    with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        if response.geturl() != url or not _allowed(response.geturl()):
            raise HealthError("response_url_mismatch", "观察榜检查返回地址不一致。")
        if response.status != 200:
            raise HealthError("http_status", f"{name}.json未返回HTTP 200。")
        length = response.headers.get("Content-Length")
        if length is not None:
            try:
                if not 0 <= int(length) <= limit:
                    raise HealthError("response_too_large", f"{name}.json超过检查大小限制。")
            except ValueError as exc:
                if isinstance(exc, HealthError):
                    raise
                raise HealthError("invalid_content_length", f"{name}.json长度声明无效。") from exc
        payload = response.read(limit + 1)
    if len(payload) > limit:
        raise HealthError("response_too_large", f"{name}.json超过检查大小限制。")
    return payload


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite JSON number")
    return number


def _load(name, loader, watchlist_url=None):
    value = loader(watchlist_url if name == "daily-watchlist" and watchlist_url else RESOURCE_URLS[name])
    if isinstance(value, dict):
        value = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    elif isinstance(value, str):
        value = value.encode("utf-8")
    if not isinstance(value, bytes):
        raise HealthError("invalid_response", f"{name}.json不是JSON对象响应。")
    if len(value) > MAX_BYTES[name]:
        raise HealthError("response_too_large", f"{name}.json超过检查大小限制。")
    try:
        result = json.loads(value.decode("utf-8-sig"), parse_float=_finite_float,
            parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise HealthError("invalid_json", f"{name}.json无法作为有限数值JSON解析。") from exc
    if not isinstance(result, dict):
        raise HealthError("invalid_json_object", f"{name}.json顶层必须为对象。")
    return result


def _score(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100
    except OverflowError:
        return False


def _target_session(calendars, moment, validated):
    today = moment.date()
    return today, _trading_day(calendars, today, validated)


def _validate(manifest, watchlist, calendars, moment, expected_version):
    if (manifest.get("repository") != "Dopagip00/gup"
            or manifest.get("privacy") != "public-market-research-only"
            or type(manifest.get("snapshot_schema_version")) is not int
            or manifest["snapshot_schema_version"] != 1):
        raise HealthError("snapshot_identity", "观察榜manifest不是指定仓库的兼容公开快照。")
    if manifest.get("app_version") != expected_version:
        raise HealthError("version_mismatch", "观察榜快照与待验收源码版本不同。")
    snapshot_time = _instant(manifest.get("generated_at"), "manifest.generated_at")
    if snapshot_time > moment:
        raise HealthError("future_snapshot", "快照生成时间晚于真实检查时间。")
    validated = {}
    day, trading = _target_session(calendars, moment, validated)
    if not trading:
        return {"status": "skipped", "trading_day": False, "date": day.isoformat(),
                "watchlist_ready": False, "has_top_pick": False, "released": False,
                "message": "官方交易日历休市，跳过当日观察榜要求。"}
    release = dt.datetime.combine(day, dt.time(9), TZ)
    expected_data = _last_completed(calendars, release, validated)
    released = moment >= release
    details = {"status": "error", "trading_day": True, "date": day.isoformat(),
               "expected_data_date": expected_data, "watchlist_ready": False,
               "has_top_pick": False, "released": released,
               "release_at": release.isoformat(), "snapshot_generated_at": snapshot_time.isoformat()}

    def reject(code, detail):
        raise HealthError(code, detail, details)

    if watchlist.get("protocol") != PROTOCOL or watchlist.get("date") != day.isoformat():
        reject("watchlist_identity", "观察榜协议或日期不是当前应验收的官方交易日。")
    if watchlist.get("status") != "watch_only":
        details["status"] = "insufficient_data"
        reject("watchlist_insufficient", "当日观察榜未形成，保留不足状态，不拼造优选首位。")
    if watchlist.get("data_date") != expected_data or watchlist.get("signal_date") != expected_data:
        reject("watchlist_data_date", "观察榜行情日不等于该交易日09:00以前的最近完整交易日。")
    if _instant(watchlist.get("release_at"), "daily-watchlist.release_at") != release:
        reject("watchlist_release_time", "观察榜计划揭榜时刻必须为所属交易日北京时间09:00。")
    generated = _instant(watchlist.get("generated_at"), "daily-watchlist.generated_at")
    if generated > snapshot_time or expected_data > _last_completed(calendars, generated, validated):
        reject("watchlist_future_evidence", "观察榜生成时间晚于快照，或使用生成时尚未完成的行情日。")
    prepared_value = watchlist.get("prepared_at")
    if prepared_value is not None:
        prepared = _instant(prepared_value, "daily-watchlist.prepared_at")
        if prepared > generated or prepared >= release:
            reject("watchlist_prepared_time", "实际准备时间晚于生成时刻，或不在揭榜前。")
        if watchlist.get("prepared_data_date") != expected_data:
            reject("watchlist_prepared_data", "预备榜原始行情日与当前榜单不一致。")
        if expected_data > _last_completed(calendars, prepared, validated):
            reject("watchlist_prepared_data", "预备时刻尚未完成所声明的原始行情日。")
    published_value = watchlist.get("published_at")
    timing = watchlist.get("publication_timing")
    basis = watchlist.get("release_basis")
    if basis is not None and basis not in ARCHIVED_RELEASE_BASES:
        reject("watchlist_release_basis", "观察榜声明了未知的发布证据协议。")
    if published_value is None:
        if basis is not None or generated >= release or timing != "prepared" or watchlist.get("late") is not False:
            reject("watchlist_publication_time", "没有服务器发布时间时，必须保留真实揭榜前预备状态。")
    else:
        published = _instant(published_value, "daily-watchlist.published_at")
        if basis in ARCHIVED_RELEASE_BASES:
            # A durable cohort keeps yesterday's genuine generation time when
            # today's server releases it; release is not a fresh computation.
            if (prepared_value is None or not generated < release <= published <= snapshot_time <= moment
                    or published.date() != day):
                reject("watchlist_publication_time", "归档揭榜须保留真实提前生成时刻，实际发布时间不能早于揭榜或晚于快照。")
            review = _instant(watchlist.get("review_at"), "daily-watchlist.review_at")
            current = watchlist.get("current_risk_verification")
            if not (published <= review <= snapshot_time) or review.date() != day or not isinstance(current, dict):
                reject("watchlist_archive_review", "归档揭榜及后续风险复核须在实际发布之后、快照之前，且保留当次复核证据。")
            cutoff = _instant(current.get("evidence_cutoff"), "current_risk_verification.evidence_cutoff")
            if cutoff > review:
                reject("watchlist_archive_review", "当次风险证据截止时刻不能晚于归档揭榜复核。")
        elif not (release <= published <= generated <= snapshot_time <= moment) or published.date() != day:
            reject("watchlist_publication_time", "服务器发布时间不在所属日09:00之后，或晚于生成/快照/真实时刻。")
        expected_timing = "on_time" if published < release + dt.timedelta(minutes=30) else "late"
        if timing != expected_timing or watchlist.get("late") is not (expected_timing == "late"):
            reject("watchlist_publication_timing", "榜单迟到标记与真实发布时间不一致。")
    if any(watchlist.get(key) is not False for key in ("model_validated", "eligible", "actionable")):
        reject("watchlist_actionable", "观察榜不得冒充模型已验证或可执行买入建议。")
    items = watchlist.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 10:
        reject("watchlist_items", "观察榜必须有1至10条真实且唯一的观察记录。")
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            reject("watchlist_item", "观察记录格式无效。")
        symbol = item.get("symbol")
        if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol) or symbol in seen:
            reject("watchlist_symbols", "观察榜证券代码无效或重复。")
        seen.add(symbol)
        if (not isinstance(item.get("name"), str) or not item["name"].strip()
                or item.get("asof") != expected_data or not _score(item.get("score"))):
            reject("watchlist_item", "观察记录缺少同日日期、名称或有限0至100分数。")
        if any(item.get(key) is not False for key in ("model_validated", "eligible", "actionable")):
            reject("watchlist_item_actionable", "观察记录不得声称已验证或可执行。")
        if any(key not in item or item[key] is not None for key in PRICE_FIELDS):
            reject("watchlist_price_fields", "观察记录的入场、止损、止盈和仓位字段必须显式为空。")
        components = item.get("components")
        if isinstance(components, dict):
            for key in ("technical", "financial"):
                if components.get(key) is not None and not _score(components[key]):
                    reject("watchlist_item_score", "技术或财务分数不在有限0至100范围内。")
    withdrawn = watchlist.get("withdrawn_symbols", [])
    alerts = watchlist.get("alerts", [])
    if (not isinstance(withdrawn, list) or len(withdrawn) > 10
            or any(not isinstance(s, str) or s not in seen for s in withdrawn)
            or len(set(withdrawn)) != len(withdrawn)
            or not isinstance(alerts, list) or len(alerts) > 10):
        reject("watchlist_withdrawals", "撤销标记必须对应原始榜单证券且不能重复。")
    withdrawn = set(withdrawn)
    for alert in alerts:
        if not isinstance(alert, dict) or alert.get("symbol") not in seen or alert.get("withdrawn") is not True:
            reject("watchlist_alerts", "负向警示必须明确撤销原始榜单证券。")
        withdrawn.add(alert["symbol"])
    first = items[0]
    top = watchlist.get("top_pick")
    if top != first and not (top is None and first["symbol"] in withdrawn):
        reject("watchlist_top_pick", "首位须保留原始第一条；撤销后不得按幸存证券递补。")
    details.update(generated_at=generated.isoformat(), published_at=published_value,
                   publication_timing=timing, count=len(items), data_date=expected_data,
                   has_top_pick=first["symbol"] not in withdrawn)
    if basis is not None:
        details.update(release_basis=basis, review_at=watchlist["review_at"])
    if not details["has_top_pick"]:
        details["status"] = "withdrawn"
        reject("watchlist_top_withdrawn", "原始首位已撤销，当前没有可继续观察的首位；不事后递补。")
    details.update(status="ready" if released else "prepared", watchlist_ready=released,
                   message="独立优选观察榜可揭榜；不代表正式合格买入。" if released
                   else "预备榜证据合格，09:00前保持隐藏；未声称服务器已发布。")
    return details


def check_watchlist(*, now=None, loader=None, expected_version=None, attempts=2):
    supplied_now = now is not None
    moment = now or dt.datetime.now(TZ)
    base = {"ok": False, "status": "error", "watchlist_ready": False, "has_top_pick": False,
            "released": False, "site": BASE_URL, "scope": "independent-daily-observation-only", "attempts": 0}
    if not isinstance(moment, dt.datetime) or moment.tzinfo is None:
        return {**base, "errors": [{"code": "invalid_now", "detail": "检查必须使用真实带时区时间。"}]}
    moment = moment.astimezone(TZ)
    base["checked_at"] = moment.isoformat()
    if type(attempts) is not int or not 1 <= attempts <= MAX_ATTEMPTS:
        return {**base, "errors": [{"code": "invalid_options", "detail": "观察榜检查最多两轮。"}]}
    try:
        if expected_version is None:
            expected_version = json.loads((HERE / "engine/version.json").read_text(encoding="utf-8"))["version"]
        if not isinstance(expected_version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", expected_version):
            raise ValueError("待验收源码版本无效")
    except (KeyError, OSError, ValueError) as exc:
        return {**base, "errors": [{"code": "source_version_unavailable", "detail": str(exc)}]}
    failures, details = [], {}
    for attempt in range(1, attempts + 1):
        try:
            watchlist_url = BASE_URL + "data/watchlists/" + moment.date().isoformat() + ".json"
            first = _load("manifest", loader or _download)
            calendars = _load("calendar", loader or _download)
            # Closed dates have no saved observation cohort or dated JSON.
            # Official calendar validation must precede that optional request.
            trading = _trading_day(calendars, moment.date(), {})
            watchlist = _load("daily-watchlist", loader or _download, watchlist_url) if trading else {}
            last = _load("manifest", loader or _download)
            if first != last:
                raise HealthError("mixed_deployment", "检查期间manifest改变，拒绝混合不同部署。")
            if not supplied_now:
                moment = dt.datetime.now(TZ)
                base["checked_at"] = moment.isoformat()
            details = _validate(first, watchlist, calendars, moment, expected_version)
            return {**base, **details, "ok": True, "attempts": attempt, "expected_version": expected_version,
                    "errors": [], "previous_attempt_errors": failures}
        except (HealthError, OSError, ValueError, TypeError, OverflowError, RecursionError) as exc:
            details = getattr(exc, "details", {})
            failures.append({"attempt": attempt, "code": getattr(exc, "code", "resource_unavailable"),
                             "detail": getattr(exc, "detail", str(exc))[:1000]})
    return {**base, **details, "attempts": attempts, "expected_version": expected_version, "errors": failures}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-version", help="默认读取engine/version.json")
    parser.add_argument("--attempts", type=int, choices=(1, 2), default=2)
    args = parser.parse_args(argv)
    result = check_watchlist(expected_version=args.expected_version, attempts=args.attempts)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
