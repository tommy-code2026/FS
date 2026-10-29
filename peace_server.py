import http.cookiejar
import html
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
SUMMARY_MODULES = ",".join(
    (
        "price",
        "summaryProfile",
        "summaryDetail",
        "defaultKeyStatistics",
        "financialData",
        "incomeStatementHistory",
        "cashflowStatementHistory",
        "balanceSheetHistory",
    )
)
ANNUAL_SERIES = {
    "annualTotalRevenue": "totalRevenue",
    "annualGrossProfit": "grossProfit",
    "annualOperatingIncome": "operatingIncome",
    "annualNetIncome": "netIncome",
    "annualDilutedEPS": "dilutedEPS",
    "annualBasicEPS": "basicEPS",
    "annualOperatingCashFlow": "operatingCashFlow",
    "annualFreeCashFlow": "freeCashFlow",
    "annualInvestingCashFlow": "investingCashFlow",
    "annualFinancingCashFlow": "financingCashFlow",
    "annualTotalAssets": "totalAssets",
    "annualLongTermDebt": "longTermDebt",
    "annualCurrentAssets": "totalCurrentAssets",
    "annualCurrentLiabilities": "totalCurrentLiabilities",
    "annualStockholdersEquity": "stockholdersEquity",
}


class YahooClient:
    def __init__(self):
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies)
        )
        self.crumb = self._get_crumb()

    def _open(self, url):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json,text/plain,*/*",
            },
        )
        with self.opener.open(request, timeout=25) as response:
            return response.read()

    def _get_crumb(self):
        try:
            self._open("https://fc.yahoo.com/")
        except urllib.error.HTTPError:
            pass

        errors = []
        for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
            try:
                crumb = self._open(
                    "https://" + host + "/v1/test/getcrumb"
                ).decode("utf-8").strip()
                if crumb and "<" not in crumb:
                    return crumb
                errors.append(host + " returned an invalid crumb")
            except (urllib.error.URLError, TimeoutError) as error:
                errors.append(host + ": " + str(error))
        raise RuntimeError("Yahoo Finance authentication failed: " + "; ".join(errors))

    def json(self, url):
        return json.loads(self._open(url).decode("utf-8"))

    def quote_summary(self, symbol):
        query = urllib.parse.urlencode(
            {"modules": SUMMARY_MODULES, "crumb": self.crumb}
        )
        path_symbol = urllib.parse.quote(symbol, safe="^.-=")
        payload = self.json(
            "https://query2.finance.yahoo.com/v10/finance/quoteSummary/"
            + path_symbol
            + "?"
            + query
        )
        result = (payload.get("quoteSummary") or {}).get("result") or []
        if not result:
            error = (payload.get("quoteSummary") or {}).get("error") or {}
            raise RuntimeError(error.get("description") or "Yahoo 找不到此股票代號。")
        return result[0]

    def annual_time_series(self, symbol):
        now = int(time.time())
        query = urllib.parse.urlencode(
            {
                "symbol": symbol,
                "type": ",".join(ANNUAL_SERIES),
                "period1": now - 15 * 365 * 24 * 60 * 60,
                "period2": now,
                "crumb": self.crumb,
            }
        )
        path_symbol = urllib.parse.quote(symbol, safe="^.-=")
        payload = self.json(
            "https://query1.finance.yahoo.com/ws/fundamentals-timeseries/"
            "v1/finance/timeseries/"
            + path_symbol
            + "?"
            + query
        )
        rows = {}
        result = (payload.get("timeseries") or {}).get("result") or []
        for series in result:
            series_types = (series.get("meta") or {}).get("type") or []
            if not series_types:
                continue
            series_name = series_types[0]
            field = ANNUAL_SERIES.get(series_name)
            if not field:
                continue
            for entry in series.get(series_name) or []:
                year = str(entry.get("asOfDate", ""))[:4]
                value = raw_value(entry.get("reportedValue"))
                if year and value is not None:
                    rows.setdefault(year, {"year": year})[field] = value
        return rows


def raw_value(value):
    if isinstance(value, dict):
        return value.get("raw")
    return value


def field_value(data, *names):
    for name in names:
        value = raw_value(data.get(name))
        if value is not None:
            return value
    return None


def records(module, *keys):
    for key in keys:
        value = module.get(key)
        if isinstance(value, list):
            return value
    return []


def chart_data(client, symbol, period="1y"):
    encoded = urllib.parse.quote(symbol, safe="^.-=")
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + encoded
        + "?"
        + urllib.parse.urlencode({"range": period, "interval": "1d"})
    )
    payload = client.json(url)
    chart = payload.get("chart") or {}
    result = (chart.get("result") or [None])[0]
    if not result:
        error = chart.get("error") or {}
        raise RuntimeError(error.get("description") or "Yahoo 無法取得行情。")

    timestamps = result.get("timestamp") or []
    quotes = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quotes.get("close") or []
    valid = [
        (timestamp, close)
        for timestamp, close in zip(timestamps, closes)
        if isinstance(close, (int, float))
    ]
    return result.get("meta") or {}, valid


def rsi(closes, window=14):
    if len(closes) <= window:
        return None
    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    gains = [max(change, 0) for change in changes[-window:]]
    losses = [max(-change, 0) for change in changes[-window:]]
    average_gain = sum(gains) / window
    average_loss = sum(losses) / window
    if average_loss == 0:
        return 100.0 if average_gain else 50.0
    return 100 - 100 / (1 + average_gain / average_loss)


def latest_chart(client, symbol):
    metadata, history = chart_data(client, symbol)
    closes = [close for _, close in history]
    return {
        "price": metadata.get("regularMarketPrice") or (closes[-1] if closes else None),
        "rsi": rsi(closes),
        "ma10": sum(closes[-10:]) / 10 if len(closes) >= 10 else None,
        "closes": closes,
    }


def semi_volatility(client):
    _, history = chart_data(client, "SOXX", "3mo")
    closes = [close for _, close in history]
    returns = [
        closes[index] / closes[index - 1] - 1
        for index in range(1, len(closes))
        if closes[index - 1]
    ][-30:]
    if len(returns) < 10:
        return None
    mean = sum(returns) / len(returns)
    daily_deviation = math.sqrt(
        sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    )
    return daily_deviation * math.sqrt(252) * 100


def external_json(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.cnn.com",
            "Referer": "https://www.cnn.com/markets/fear-and-greed/",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def fear_greed():
    payload = external_json(
        "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    )
    current = payload.get("fear_and_greed") or {}
    return {
        "score": current.get("score"),
        "rating": current.get("rating"),
    }


def sp500_pe():
    request = urllib.request.Request(
        "https://www.multpl.com/s-p-500-pe-ratio/table/by-month",
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        page = response.read().decode("utf-8", "replace")
    table = re.search(
        r'<table[^>]*id="datatable"[^>]*>(.*?)</table>',
        page,
        re.IGNORECASE | re.DOTALL,
    )
    if not table:
        raise RuntimeError("Multpl page did not contain the expected PE table.")
    rows = re.findall(
        r"<tr[^>]*>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>",
        table.group(1),
        re.IGNORECASE | re.DOTALL,
    )
    for _, value_cell in rows:
        text = html.unescape(re.sub(r"<[^>]+>", " ", value_cell))
        value = re.search(r"\d+(?:\.\d+)?", text)
        if value:
            return float(value.group(0))
    raise RuntimeError("Multpl page did not contain a numeric PE value.")


def annual_statements(summary, time_series):
    income = records(
        summary.get("incomeStatementHistory") or {},
        "incomeStatementHistory",
    )
    cashflow = records(
        summary.get("cashflowStatementHistory") or {},
        "cashflowStatements",
    )
    balance = records(
        summary.get("balanceSheetHistory") or {},
        "balanceSheetStatements",
    )
    years = {}
    for statement, keys in (
        (income, ("totalRevenue", "grossProfit", "operatingIncome", "netIncome", "dilutedEPS", "basicEPS", "interestExpense")),
        (cashflow, ("operatingCashFlow", "totalCashFromOperatingActivities", "freeCashFlow", "capitalExpenditures", "investingCashFlow", "totalCashflowsFromInvestingActivities", "financingCashFlow", "totalCashFromFinancingActivities")),
        (balance, ("totalAssets", "longTermDebt", "totalCurrentAssets", "totalCurrentLiabilities", "stockholdersEquity", "totalDebt", "cashAndCashEquivalents")),
    ):
        for row in statement:
            date = row.get("endDate") or row.get("maxAge")
            if isinstance(date, dict) and date.get("fmt"):
                year = str(date["fmt"])[:4]
            elif isinstance(date, dict) and isinstance(date.get("raw"), (int, float)):
                year = str(datetime.fromtimestamp(date["raw"], timezone.utc).year)
            else:
                year = ""
            if not year:
                continue
            item = years.setdefault(year, {"year": year})
            for key in keys:
                value = field_value(row, key)
                if value is not None:
                    item[key] = value
    for item in years.values():
        if "operatingCashFlow" not in item:
            item["operatingCashFlow"] = item.get("totalCashFromOperatingActivities")
        if "freeCashFlow" not in item and item.get("operatingCashFlow") is not None:
            capex = item.get("capitalExpenditures") or 0
            item["freeCashFlow"] = item["operatingCashFlow"] + capex
        if "investingCashFlow" not in item:
            item["investingCashFlow"] = item.get("totalCashflowsFromInvestingActivities")
        if "financingCashFlow" not in item:
            item["financingCashFlow"] = item.get("totalCashFromFinancingActivities")
        if "dilutedEPS" not in item and "basicEPS" in item:
            item["dilutedEPS"] = item["basicEPS"]
    for year, values in time_series.items():
        existing = years.setdefault(year, {"year": year})
        existing.update(values)
    return sorted(years.values(), key=lambda row: row["year"], reverse=True)


def prepare_stock(symbol, summary, quote_chart, markets, errors, time_series):
    price = summary.get("price") or {}
    details = summary.get("summaryDetail") or {}
    stats = summary.get("defaultKeyStatistics") or {}
    finance = summary.get("financialData") or {}
    annual = annual_statements(summary, time_series)
    latest = annual[0] if annual else {}

    actual_price = (
        field_value(price, "regularMarketPrice", "postMarketPrice")
        or field_value(finance, "currentPrice")
        or quote_chart.get("price")
    )
    pe = field_value(details, "trailingPE")
    forward_pe = field_value(details, "forwardPE")
    peg = field_value(stats, "pegRatio")
    revenue = latest.get("totalRevenue")
    gross_profit = latest.get("grossProfit")
    operating_income = latest.get("operatingIncome")
    net_income = latest.get("netIncome")
    operating_cash = latest.get("operatingCashFlow")
    free_cash = latest.get("freeCashFlow")
    equity = latest.get("stockholdersEquity")
    debt = latest.get("totalDebt")
    if debt is None:
        debt = field_value(finance, "totalDebt")
    if equity is None:
        equity = field_value(finance, "totalStockholderEquity")
    debt_equity = debt / equity if debt is not None and equity else None
    if debt_equity is None:
        debt_equity = field_value(finance, "debtToEquity")
        if debt_equity is not None:
            debt_equity /= 100
    current_ratio = (
        latest.get("totalCurrentAssets") / latest.get("totalCurrentLiabilities")
        if latest.get("totalCurrentAssets") is not None
        and latest.get("totalCurrentLiabilities")
        else field_value(finance, "currentRatio")
    )
    long_debt = latest.get("longTermDebt")
    long_debt_net_income = (
        long_debt / net_income
        if long_debt is not None and net_income and net_income > 0
        else None
    )
    eps = latest.get("dilutedEPS", latest.get("basicEPS"))
    interest_expense = latest.get("interestExpense")
    interest_coverage = (
        operating_income / abs(interest_expense)
        if operating_income is not None and interest_expense
        else None
    )
    cash_quality = (
        operating_cash / net_income
        if operating_cash is not None and net_income
        else None
    )
    roe_values = [
        row["netIncome"] / row["stockholdersEquity"]
        for row in annual[:5]
        if row.get("netIncome") is not None and row.get("stockholdersEquity")
    ]
    average_roe = sum(roe_values) / len(roe_values) if roe_values else field_value(finance, "returnOnEquity")
    asset_turnover = (
        revenue / latest["totalAssets"]
        if revenue is not None and latest.get("totalAssets")
        else None
    )
    gp_assets = (
        gross_profit / latest["totalAssets"]
        if gross_profit is not None and latest.get("totalAssets")
        else None
    )
    fcf_margin = free_cash / revenue if free_cash is not None and revenue else None

    return {
        "symbol": symbol,
        "name": field_value(price, "longName", "shortName") or symbol,
        "currency": field_value(price, "currency") or "USD",
        "price": actual_price,
        "description": field_value(summary.get("summaryProfile") or {}, "longBusinessSummary") or "",
        "valuation": {
            "trailingPE": pe,
            "forwardPE": forward_pe,
            "peg": peg,
            "sp500PE": markets.get("sp500_pe"),
            "marketCap": field_value(price, "marketCap"),
            "trailingEps": field_value(stats, "trailingEps"),
        },
        "market": {
            "fearGreed": markets.get("fear_greed"),
            "vix": markets.get("vix"),
            "vxn": markets.get("vxn"),
            "semiVolatility": markets.get("semi_volatility"),
            "rsi": quote_chart.get("rsi"),
            "ma10": quote_chart.get("ma10"),
        },
        "metrics": {
            "revenue": revenue,
            "grossProfit": gross_profit,
            "operatingIncome": operating_income,
            "eps": eps,
            "operatingCashFlow": operating_cash,
            "freeCashFlow": free_cash,
            "cashQuality": cash_quality,
            "debtEquity": debt_equity,
            "interestCoverage": interest_coverage,
            "currentRatio": current_ratio,
            "longDebtNetIncome": long_debt_net_income,
            "averageROE": average_roe,
            "assetTurnover": asset_turnover,
            "roic": None,
            "wacc": None,
            "gpAssets": gp_assets,
            "fcfMargin": fcf_margin,
        },
        "annual": annual,
        "errors": errors,
    }


class PeaceHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", "/index.html", "/peace.html"):
            self.path = "/index.html"
            return super().do_GET()
        if parsed.path == "/healthz":
            return self.send_json(200, {"status": "ok"})
        if parsed.path != "/api/stock":
            return self.send_error(404)

        symbol = urllib.parse.parse_qs(parsed.query).get("symbol", [""])[0].strip().upper()
        if not re.fullmatch(r"[-A-Z0-9^.=]{1,20}", symbol):
            return self.send_json(400, {"error": "請輸入有效的股票代號，例如 NVDA 或 2330.TW。"})

        try:
            yahoo = YahooClient()
            summary = yahoo.quote_summary(symbol)
            time_series = yahoo.annual_time_series(symbol)
            errors = {}
            with ThreadPoolExecutor(max_workers=5) as pool:
                stock_future = pool.submit(latest_chart, yahoo, symbol)
                futures = {
                    "vix": pool.submit(latest_chart, yahoo, "^VIX"),
                    "vxn": pool.submit(latest_chart, yahoo, "^VXN"),
                    "semi_volatility": pool.submit(semi_volatility, yahoo),
                    "fear_greed": pool.submit(fear_greed),
                    "sp500_pe": pool.submit(sp500_pe),
                }
                stock_chart = stock_future.result()
                markets = {}
                for key, future in futures.items():
                    try:
                        result = future.result()
                        if key in ("vix", "vxn"):
                            result = result.get("price")
                        markets[key] = result
                    except Exception as error:
                        markets[key] = None
                        errors[key] = str(error)
            data = prepare_stock(symbol, summary, stock_chart, markets, errors, time_series)
            self.send_json(200, data)
        except Exception as error:
            self.send_json(502, {"error": str(error)})

    def send_json(self, status, data):
        content = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8765"))
    server = ThreadingHTTPServer((host, port), PeaceHandler)
    print(f"PEACE 已啟動：http://{host}:{port}")
    print("按 Ctrl+C 停止伺服器。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
