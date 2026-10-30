import http.cookiejar
import html
import json
import math
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import lru_cache
from html.parser import HTMLParser
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_ALLOWED_ORIGINS = {
    "https://tommy-code2026.github.io",
    "http://localhost:8765",
    "http://127.0.0.1:8765",
}
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
    "annualInventory": "inventory",
    "annualAccountsReceivable": "accountsReceivable",
}


class YahooClient:
    def __init__(self):
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies)
        )
        self.crumb = None
        self.crumb_error = None

    def _ensure_crumb(self):
        if self.crumb_error is not None:
            raise RuntimeError(str(self.crumb_error)) from self.crumb_error
        if self.crumb is None:
            try:
                self.crumb = self._get_crumb()
            except Exception as error:
                self.crumb_error = error
                raise
        return self.crumb

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
            {"modules": SUMMARY_MODULES, "crumb": self._ensure_crumb()}
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
                "crumb": self._ensure_crumb(),
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


class NasdaqClient:
    base_url = "https://api.nasdaq.com/api/quote/"

    def get(self, symbol, endpoint, **params):
        query = urllib.parse.urlencode(params)
        url = (
            self.base_url
            + urllib.parse.quote(symbol, safe=".-")
            + "/"
            + endpoint
            + ("?" + query if query else "")
        )
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://www.nasdaq.com",
                "Referer": "https://www.nasdaq.com/",
            },
        )
        with urllib.request.urlopen(request, timeout=25) as response:
            payload = json.loads(response.read().decode("utf-8"))
        status = payload.get("status") or {}
        if status.get("rCode") not in (None, 200):
            messages = status.get("bCodeMessage") or []
            detail = "; ".join(
                item.get("errorMessage", "")
                for item in messages
                if isinstance(item, dict)
            )
            raise RuntimeError(detail or "Nasdaq 未提供此股票資料。")
        data = payload.get("data")
        if not data:
            raise RuntimeError("Nasdaq 未提供此股票資料。")
        return data

    @staticmethod
    def numeric(value):
        if value is None:
            return None
        match = re.search(r"-?[\d,]+(?:\.\d+)?", str(value))
        return float(match.group(0).replace(",", "")) if match else None

    def quote(self, symbol):
        info = self.get(symbol, "info", assetclass="stocks")
        summary = self.get(symbol, "summary", assetclass="stocks")
        primary = info.get("primaryData") or {}
        stats = summary.get("summaryData") or {}
        return {
            "price": {
                "longName": info.get("companyName") or symbol,
                "currency": "USD",
                "regularMarketPrice": self.numeric(
                    primary.get("lastSalePrice")
                    or (info.get("secondaryData") or {}).get("lastSalePrice")
                ),
                "marketCap": self.numeric(
                    (stats.get("MarketCap") or {}).get("value")
                ),
            },
            "summaryProfile": {},
            "summaryDetail": {
                "trailingPE": self.numeric(
                    (stats.get("PERatio") or {}).get("value")
                ),
            },
            "defaultKeyStatistics": {},
            "financialData": {},
        }

    def chart(self, symbol, days=370):
        start = datetime.now(timezone.utc).date().toordinal() - days
        start_date = datetime.fromordinal(start).strftime("%Y-%m-%d")
        data = self.get(
            symbol,
            "historical",
            assetclass="stocks",
            fromdate=start_date,
            limit=500,
        )
        rows = (data.get("tradesTable") or {}).get("rows") or []
        history = []
        for row in rows:
            try:
                date = datetime.strptime(row["date"], "%m/%d/%Y").replace(
                    tzinfo=timezone.utc
                )
                close = self.numeric(row.get("close"))
                if close is not None:
                    history.append((int(date.timestamp()), close))
            except (KeyError, ValueError):
                continue
        history.sort()
        if not history:
            raise RuntimeError("Nasdaq 未提供可用的歷史收盤價。")
        closes = [close for _, close in history]
        return {
            "price": closes[-1],
            "rsi": rsi(closes),
            "ma10": sum(closes[-10:]) / 10 if len(closes) >= 10 else None,
            "closes": closes,
        }


@lru_cache(maxsize=1)
def sec_tickers():
    user_agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if not user_agent or "@" not in user_agent:
        raise RuntimeError(
            "SEC EDGAR 需要在 Render 設定含聯絡 Email 的 SEC_USER_AGENT。"
        )
    request = urllib.request.Request(
        "https://www.sec.gov/files/company_tickers.json",
        headers={"User-Agent": user_agent, "Accept-Encoding": "identity"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


class SecClient:
    QUALITATIVE_TERMS = {
        "intangible": r"patent|trademark|brand|intellectual property|license|proprietary",
        "cost": r"manufacturing cost|unit cost|economies of scale|cost advantage|supplier cost",
        "network": r"network effect|network effects|ecosystem|active users|marketplace",
        "switching": r"switching cost|customer retention|customer churn|migration cost|renewal rate|qualification|certification|customer contract",
        "scale": r"barriers to entry|new entrants|market share|competitive position|competition",
        "regulation": r"regulatory|government regulation|legal proceedings|government action",
        "inflation": r"inflation|pricing|price increases|input costs|cost of materials",
        "technology": r"technological change|technology risk|obsolescence|cybersecurity|research and development",
        "key_person": r"succession|key personnel|executive officers|chief executive officer|founder",
    }

    FACT_TAGS = {
        "totalRevenue": (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
        ),
        "grossProfit": ("GrossProfit",),
        "operatingIncome": ("OperatingIncomeLoss",),
        "netIncome": ("NetIncomeLoss",),
        "dilutedEPS": ("EarningsPerShareDiluted",),
        "operatingCashFlow": ("NetCashProvidedByUsedInOperatingActivities",),
        "capitalExpenditures": (
            "PaymentsToAcquireProductiveAssets",
            "PaymentsToAcquirePropertyPlantAndEquipment",
        ),
        "investingCashFlow": ("NetCashProvidedByUsedInInvestingActivities",),
        "financingCashFlow": ("NetCashProvidedByUsedInFinancingActivities",),
        "totalAssets": ("Assets",),
        "cashAndCashEquivalents": (
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        ),
        "inventory": ("InventoryNet",),
        "accountsReceivable": (
            "AccountsReceivableNetCurrent",
            "AccountsNotesAndLoansReceivableNetCurrent",
        ),
        "longTermDebt": ("LongTermDebtNoncurrent", "LongTermDebt"),
        "totalCurrentAssets": ("AssetsCurrent",),
        "totalCurrentLiabilities": ("LiabilitiesCurrent",),
        "stockholdersEquity": ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
        "interestExpense": ("InterestExpenseNonOperating", "InterestAndDebtExpense"),
    }

    def get_facts(self, ticker):
        company = self.get_company(ticker)
        cik = int(company["cik_str"])
        user_agent = os.environ["SEC_USER_AGENT"].strip()
        return company, self.cached_company_facts(cik, user_agent)

    @staticmethod
    def get_company(ticker):
        company = next(
            (item for item in sec_tickers().values()
             if str(item.get("ticker", "")).casefold() == ticker.casefold()),
            None,
        )
        if not company:
            raise RuntimeError("SEC EDGAR 未收錄此股票代號。")
        user_agent = os.environ["SEC_USER_AGENT"].strip()
        if not user_agent:
            raise RuntimeError("SEC EDGAR 需要設定含聯絡 Email 的 SEC_USER_AGENT。")
        return company

    @staticmethod
    @lru_cache(maxsize=32)
    def cached_company_facts(cik, user_agent):
        request = urllib.request.Request(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json",
            headers={
                "User-Agent": user_agent,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload.get("facts", {}).get("us-gaap", {})

    @staticmethod
    @lru_cache(maxsize=32)
    def cached_submissions(cik, user_agent):
        request = urllib.request.Request(
            f"https://data.sec.gov/submissions/CIK{cik:010d}.json",
            headers={
                "User-Agent": user_agent,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    @lru_cache(maxsize=8)
    def cached_filing_text(url, user_agent):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Encoding": "identity",
            },
        )
        with urllib.request.urlopen(request, timeout=25) as response:
            parser = _SECTextParser()
            parser.feed(response.read().decode("utf-8", errors="replace"))
        return " ".join(" ".join(parser.parts).split())

    @classmethod
    def qualitative_evidence(cls, ticker):
        company = cls.get_company(ticker)
        cik = int(company["cik_str"])
        user_agent = os.environ["SEC_USER_AGENT"].strip()
        payload = cls.cached_submissions(cik, user_agent)
        recent = (payload.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        filing_dates = recent.get("filingDate") or []
        accessions = recent.get("accessionNumber") or []
        documents = recent.get("primaryDocument") or []
        selected = {}
        for form, filed_at, accession, document in zip(
            forms, filing_dates, accessions, documents
        ):
            if form in ("10-K", "10-K/A", "20-F", "40-F"):
                category = "annual"
            elif form == "10-Q":
                category = "quarterly"
            elif form in ("8-K", "8-K/A"):
                category = "event"
            elif form == "DEF 14A":
                category = "proxy"
            else:
                continue
            if category not in selected:
                selected[category] = (form, filed_at, accession, document)
        if not selected:
            raise RuntimeError("SEC EDGAR 近期申報中沒有可用的年報、季報或委託書。")

        evidence = {}
        for form, filed_at, accession, document in selected.values():
            safe_document = os.path.basename(document)
            accession_path = accession.replace("-", "")
            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik}/"
                f"{accession_path}/{urllib.parse.quote(safe_document)}"
            )
            text = cls.cached_filing_text(filing_url, user_agent)
            for topic, terms in cls.QUALITATIVE_TERMS.items():
                match = re.search(terms, text, re.IGNORECASE)
                if not match:
                    continue
                start = max(0, match.start() - 150)
                end = min(len(text), match.end() + 210)
                evidence.setdefault(topic, []).append({
                    "source": "SEC EDGAR",
                    "title": f"{company.get('title') or ticker} {form}",
                    "filedAt": filed_at,
                    "url": filing_url,
                    "excerpt": text[start:end],
                })
        return evidence, f"SEC EDGAR 已抓取 {len(selected)} 份申報文件。"

    @staticmethod
    def annual_entries(unit_rows):
        selected = {}
        for entry in unit_rows:
            if entry.get("form") not in ("10-K", "20-F", "40-F"):
                continue
            start = entry.get("start")
            end = entry.get("end")
            if not end:
                continue
            if start:
                try:
                    duration = (
                        datetime.strptime(end, "%Y-%m-%d")
                        - datetime.strptime(start, "%Y-%m-%d")
                    ).days
                except ValueError:
                    continue
                if not 300 <= duration <= 400:
                    continue
            year = end[:4]
            filed = entry.get("filed", "")
            prior = selected.get(year)
            if prior is None or filed > prior.get("filed", ""):
                selected[year] = entry
        return selected

    def annual_statements(self, ticker):
        company, facts = self.get_facts(ticker)
        years = {}
        for output_field, tag_names in self.FACT_TAGS.items():
            selected_entries = {}
            for tag_name in tag_names:
                fact = facts.get(tag_name)
                if not fact:
                    continue
                units = fact.get("units") or {}
                if output_field == "dilutedEPS":
                    entries = next(
                        (
                            rows
                            for unit_name, rows in units.items()
                            if unit_name.replace(" ", "").casefold() == "usd/shares"
                        ),
                        [],
                    )
                else:
                    entries = units.get("USD", [])
                for year, entry in self.annual_entries(entries).items():
                    previous = selected_entries.get(year)
                    if previous is None or entry.get("filed", "") > previous.get("filed", ""):
                        selected_entries[year] = entry
            for year, entry in selected_entries.items():
                years.setdefault(year, {"year": year})[output_field] = entry["val"]

        for row in years.values():
            capex = row.get("capitalExpenditures")
            operating_cash = row.get("operatingCashFlow")
            if operating_cash is not None and capex is not None:
                row["freeCashFlow"] = operating_cash - abs(capex)
        if not years:
            raise RuntimeError(f"SEC EDGAR 沒有 {ticker} 可用的年度財報。")
        annual = sorted(years.values(), key=lambda item: item["year"], reverse=True)
        summary = {
            "price": {
                "longName": company.get("title") or ticker,
                "currency": "USD",
            },
            "summaryProfile": {},
            "summaryDetail": {},
            "defaultKeyStatistics": {},
            "financialData": {},
        }
        return summary, {row["year"]: row for row in annual}


class _SECTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden_depth += 1
        elif tag in ("br", "div", "p", "tr", "td", "th", "li") and not self.hidden_depth:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.hidden_depth:
            self.hidden_depth -= 1
        elif tag in ("div", "p", "tr", "td", "th", "li") and not self.hidden_depth:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden_depth:
            self.parts.append(data)


class MopsClient:
    BASE_URL = "https://mopsov.twse.com.tw/mops/web/ajax_t05st01"
    TOPIC_TERMS = {
        "intangible": r"專利|商標|智慧財產|研發成果|技術授權|著作權|品牌",
        "cost": r"毛利率|營業利益|資本支出|產能利用率|單位成本|營運成本|製造成本|成本優勢",
        "network": r"網路效應|平台|生態系|活躍用戶|開發者|多邊市場",
        "switching": r"轉換成本|遷移成本|客戶留存|續約率|認證|主要契約|設計導入|停機風險|第二供應來源",
        "scale": r"產能|資本支出|市場占有率|市場份額|競爭者|進入障礙|擴產",
        "regulation": r"裁罰|行政處分|違規|訴訟|法律程序|主管機關調查|反壟斷|司法調查|罰鍰|出口管制|監管",
        "inflation": r"通膨|通脹|售價|調價|價格調整|原物料價格|毛利率|漲價|降價",
        "technology": r"研發|專利|替代技術|技術風險|產品世代|技術淘汰|資安|技術演進",
        "key_person": r"董事長異動|董事異動|總經理異動|經理人異動|接班|經營團隊|高階主管|持股轉讓|持股質押|股權質押",
    }

    @staticmethod
    def _ssl_context():
        context = ssl.create_default_context()
        strict_flag = getattr(ssl, "VERIFY_X509_STRICT", 0)
        if strict_flag:
            context.verify_flags &= ~strict_flag
        return context

    @staticmethod
    def _request(url, data=None):
        body = urllib.parse.urlencode(data).encode("ascii") if data else None
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "User-Agent": USER_AGENT,
                "Referer": "https://mopsov.twse.com.tw/mops/web/t05st01",
                "Accept": "text/html,application/xhtml+xml",
                **({"Content-Type": "application/x-www-form-urlencoded"} if body else {}),
            },
        )
        with urllib.request.urlopen(
            request, timeout=20, context=MopsClient._ssl_context()
        ) as response:
            content = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
        return content.decode(charset, errors="replace")

    @staticmethod
    def _plain_text(markup):
        parser = _SECTextParser()
        parser.feed(markup)
        return " ".join(" ".join(parser.parts).split())

    @classmethod
    def qualitative_evidence(cls, symbol):
        match = re.fullmatch(r"(\d{4,6})\.(TW|TWO)", symbol, re.IGNORECASE)
        if not match:
            raise RuntimeError("MOPS 僅支援 .TW 或 .TWO 台股代號。")
        code = match.group(1)
        market_type = "otc" if match.group(2).upper() == "TWO" else "sii"
        current_roc_year = datetime.now().year - 1911
        records = []
        listing_url = ""
        for roc_year in (current_roc_year, current_roc_year - 1):
            query = urllib.parse.urlencode({
                "encodeURIComponent": "1",
                "step": "1",
                "firstin": "1",
                "off": "1",
                "co_id": code,
                "year": str(roc_year),
            })
            listing_url = f"{cls.BASE_URL}?{query}"
            markup = cls._request(listing_url)
            for row in re.findall(r"<tr\b[^>]*>.*?</tr>", markup, re.IGNORECASE | re.DOTALL):
                onclick = re.search(r"onclick=(['\"])(.*?)\1", row, re.IGNORECASE | re.DOTALL)
                title_match = re.search(r"<pre\b[^>]*>(.*?)</pre>", row, re.IGNORECASE | re.DOTALL)
                if not onclick or not title_match:
                    continue
                fields = {}
                for field in ("seq_no", "spoke_time", "spoke_date", "co_id", "TYPEK"):
                    value = re.search(
                        rf"{field}\.value=['\"]([^'\"]*)['\"]",
                        onclick.group(2), re.IGNORECASE,
                    )
                    if value:
                        fields[field] = value.group(1)
                title = cls._plain_text(title_match.group(1))
                if all(key in fields for key in ("seq_no", "spoke_time", "spoke_date", "co_id")):
                    records.append((roc_year, fields, title))
            if records:
                break
        if not records:
            raise RuntimeError("MOPS 未回傳此公司可用的重大訊息。")

        topic_pattern = re.compile(
            "|".join(f"(?:{terms})" for terms in cls.TOPIC_TERMS.values()),
            re.IGNORECASE,
        )
        relevant_records = [record for record in records if topic_pattern.search(record[2])]
        records = (relevant_records or records)[:8]
        evidence = {}
        fetched = 0
        for roc_year, fields, title in records:
            detail_params = {
                "encodeURIComponent": "1",
                "firstin": "true",
                "TYPEK": fields.get("TYPEK", market_type),
                "year": str(roc_year),
                "month": "all",
                "co_id": fields["co_id"],
                "spoke_date": fields["spoke_date"],
                "spoke_time": fields["spoke_time"],
                "seq_no": fields["seq_no"],
                "step": "2",
                "off": "1",
            }
            detail_url = f"{cls.BASE_URL}?{urllib.parse.urlencode(detail_params)}"
            try:
                detail = cls._plain_text(cls._request(detail_url))
            except Exception:
                continue
            fetched += 1
            roc_date = fields["spoke_date"]
            filed_at = ""
            if re.fullmatch(r"\d{8}", roc_date):
                filed_at = f"{roc_date[:4]}-{roc_date[4:6]}-{roc_date[6:8]}"
            searchable = f"{title} {detail}"
            for topic, terms in cls.TOPIC_TERMS.items():
                topic_match = re.search(terms, searchable, re.IGNORECASE)
                if topic_match:
                    detail_match = re.search(terms, detail, re.IGNORECASE)
                    if detail_match:
                        start = max(0, detail_match.start() - 150)
                        end = min(len(detail), detail_match.end() + 210)
                        excerpt = detail[start:end]
                    else:
                        excerpt = title or detail[:360]
                    evidence.setdefault(topic, []).append({
                        "source": "MOPS 公開資訊觀測站",
                        "title": title or "重大訊息",
                        "filedAt": filed_at,
                        "url": detail_url,
                        "excerpt": excerpt,
                    })
        if not fetched:
            raise RuntimeError("MOPS 公告清單有資料，但未能取得單筆公告內容。")
        return evidence, f"MOPS 已抓取 {fetched} 則重大訊息原文；申報內容由公司提供，需交叉驗證。"

    @staticmethod
    @lru_cache(maxsize=32)
    def cached_company_facts(cik, user_agent):
        request = urllib.request.Request(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json",
            headers={
                "User-Agent": user_agent,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload.get("facts", {}).get("us-gaap", {})

    @staticmethod
    def annual_entries(unit_rows):
        selected = {}
        for entry in unit_rows:
            if entry.get("form") not in ("10-K", "20-F", "40-F"):
                continue
            start = entry.get("start")
            end = entry.get("end")
            if not end:
                continue
            if start:
                try:
                    duration = (
                        datetime.strptime(end, "%Y-%m-%d")
                        - datetime.strptime(start, "%Y-%m-%d")
                    ).days
                except ValueError:
                    continue
                if not 300 <= duration <= 400:
                    continue
            year = end[:4]
            filed = entry.get("filed", "")
            prior = selected.get(year)
            if prior is None or filed > prior.get("filed", ""):
                selected[year] = entry
        return selected

    def annual_statements(self, ticker):
        company, facts = self.get_facts(ticker)
        years = {}
        for output_field, tag_names in self.FACT_TAGS.items():
            selected_entries = {}
            for tag_name in tag_names:
                fact = facts.get(tag_name)
                if not fact:
                    continue
                units = fact.get("units") or {}
                if output_field == "dilutedEPS":
                    entries = next(
                        (
                            rows
                            for unit_name, rows in units.items()
                            if unit_name.replace(" ", "").casefold()
                            == "usd/shares"
                        ),
                        [],
                    )
                else:
                    entries = units.get("USD", [])
                for year, entry in self.annual_entries(entries).items():
                    previous = selected_entries.get(year)
                    if previous is None or entry.get("filed", "") > previous.get("filed", ""):
                        selected_entries[year] = entry
            for year, entry in selected_entries.items():
                years.setdefault(year, {"year": year})[output_field] = entry["val"]

        for row in years.values():
            capex = row.get("capitalExpenditures")
            operating_cash = row.get("operatingCashFlow")
            if operating_cash is not None and capex is not None:
                row["freeCashFlow"] = operating_cash - abs(capex)
            debt = row.pop("longTermDebt", None)
            if debt is not None:
                row["longTermDebt"] = debt
        if not years:
            raise RuntimeError(f"SEC EDGAR 沒有 {ticker} 可用的年度財報。")
        annual = sorted(years.values(), key=lambda item: item["year"], reverse=True)
        summary = {
            "price": {
                "longName": company.get("title") or ticker,
                "currency": "USD",
            },
            "summaryProfile": {},
            "summaryDetail": {},
            "defaultKeyStatistics": {},
            "financialData": {},
        }
        return summary, {row["year"]: row for row in annual}


class FMPClient:
    def __init__(self, api_key):
        self.api_key = api_key

    def get(self, endpoint, **params):
        params["apikey"] = self.api_key
        url = (
            "https://financialmodelingprep.com/api/v3/"
            + endpoint
            + "?"
            + urllib.parse.urlencode(params)
        )
        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=25) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if isinstance(payload, dict) and (
            payload.get("Error Message") or payload.get("error")
        ):
            raise RuntimeError(
                payload.get("Error Message") or payload.get("error")
            )
        return payload

    def stock_data(self, symbol):
        encoded_symbol = urllib.parse.quote(symbol, safe="^.-=")
        profile = self.get("profile/" + encoded_symbol)
        quote = self.get("quote/" + encoded_symbol)
        if not profile or not quote:
            raise RuntimeError("FMP 找不到此股票代號，或此 API key 無權存取該資料。")

        profile = profile[0]
        quote = quote[0]
        ratios = self.get("ratios/" + encoded_symbol, limit=1)
        key_metrics = self.get("key-metrics/" + encoded_symbol, limit=1)
        income = self.get("income-statement/" + encoded_symbol, limit=5)
        cashflow = self.get("cash-flow-statement/" + encoded_symbol, limit=5)
        balance = self.get("balance-sheet-statement/" + encoded_symbol, limit=5)

        ratio = ratios[0] if ratios else {}
        key_metric = key_metrics[0] if key_metrics else {}
        series_by_year = {}
        mappings = (
            (income, {
                "revenue": "totalRevenue",
                "grossProfit": "grossProfit",
                "operatingIncome": "operatingIncome",
                "netIncome": "netIncome",
                "epsdiluted": "dilutedEPS",
                "eps": "basicEPS",
                "interestExpense": "interestExpense",
            }),
            (cashflow, {
                "operatingCashFlow": "operatingCashFlow",
                "freeCashFlow": "freeCashFlow",
                "netCashUsedForInvestingActivites": "investingCashFlow",
                "netCashUsedForInvestingActivities": "investingCashFlow",
                "netCashProvidedByInvestingActivities": "investingCashFlow",
                "netCashUsedProvidedByFinancingActivities": "financingCashFlow",
                "netCashProvidedByFinancingActivities": "financingCashFlow",
            }),
            (balance, {
                "totalAssets": "totalAssets",
                "cashAndCashEquivalents": "cashAndCashEquivalents",
                "inventory": "inventory",
                "netReceivables": "accountsReceivable",
                "longTermDebt": "longTermDebt",
                "totalCurrentAssets": "totalCurrentAssets",
                "totalCurrentLiabilities": "totalCurrentLiabilities",
                "totalStockholdersEquity": "stockholdersEquity",
                "totalDebt": "totalDebt",
            }),
        )
        for statements, fields in mappings:
            for statement in statements or []:
                year = str(statement.get("date", ""))[:4]
                if not year:
                    continue
                annual = series_by_year.setdefault(year, {"year": year})
                for source_field, target_field in fields.items():
                    value = statement.get(source_field)
                    if value is not None:
                        annual[target_field] = value

        history = self.get(
            "historical-price-full/" + encoded_symbol,
            serietype="line",
        )
        prices = (history.get("historical") or []) if isinstance(history, dict) else []
        chart_history = [
            (int(time.mktime(time.strptime(item["date"], "%Y-%m-%d"))), item["close"])
            for item in prices
            if item.get("date") and isinstance(item.get("close"), (int, float))
        ]
        chart_history.sort()
        closes = [close for _, close in chart_history]
        currency = profile.get("currency") or quote.get("currency") or "USD"

        summary = {
            "price": {
                "longName": profile.get("companyName") or quote.get("name") or symbol,
                "currency": currency,
                "regularMarketPrice": quote.get("price"),
                "marketCap": quote.get("marketCap"),
            },
            "summaryProfile": {
                "longBusinessSummary": profile.get("description") or "",
            },
            "summaryDetail": {
                "trailingPE": quote.get("pe") or ratio.get("priceEarningsRatio"),
                "forwardPE": key_metric.get("forwardPE"),
            },
            "defaultKeyStatistics": {
                "pegRatio": ratio.get("priceEarningsToGrowthRatio"),
                "trailingEps": quote.get("eps"),
            },
            "financialData": {
                "totalDebt": quote.get("totalDebt"),
                "debtToEquity": (
                    ratio["debtEquityRatio"] * 100
                    if ratio.get("debtEquityRatio") is not None
                    else None
                ),
                "currentRatio": ratio.get("currentRatio"),
                "returnOnEquity": ratio.get("returnOnEquity"),
            },
        }
        chart = {
            "price": quote.get("price") or (closes[-1] if closes else None),
            "rsi": rsi(closes[-251:]) if len(closes) > 15 else None,
            "ma10": sum(closes[-10:]) / 10 if len(closes) >= 10 else None,
            "closes": closes,
        }
        return summary, series_by_year, chart


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


def merge_summary_fallback(summary, fallback):
    for key, value in fallback.items():
        existing = summary.get(key)
        if not isinstance(value, dict):
            if existing is None:
                summary[key] = value
            continue
        if not isinstance(existing, dict):
            summary[key] = value
            continue
        for field, field_data in value.items():
            if existing.get(field) is None:
                existing[field] = field_data


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
    try:
        _, history = chart_data(client, "SOXX", "3mo")
        closes = [close for _, close in history]
        provider = "Yahoo Finance"
    except Exception as yahoo_error:
        try:
            closes = NasdaqClient().chart("SOXX", days=100)["closes"]
            provider = "Nasdaq"
        except Exception as nasdaq_error:
            raise RuntimeError(
                f"Yahoo Finance: {yahoo_error}; Nasdaq: {nasdaq_error}"
            ) from nasdaq_error
    returns = [
        closes[index] / closes[index - 1] - 1
        for index in range(1, len(closes))
        if closes[index - 1]
    ][-30:]
    if len(returns) < 10:
        return None, provider
    mean = sum(returns) / len(returns)
    daily_deviation = math.sqrt(
        sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    )
    return daily_deviation * math.sqrt(252) * 100, provider


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


@lru_cache(maxsize=2)
def cboe_index_chart(symbol):
    if symbol not in ("^VIX", "^VXN"):
        raise ValueError("Unsupported CBOE index.")
    index = symbol[1:]
    request = urllib.request.Request(
        f"https://cdn.cboe.com/api/global/us_indices/daily_prices/{index}_History.csv",
        headers={"User-Agent": USER_AGENT, "Accept": "text/csv,*/*"},
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        rows = response.read().decode("utf-8-sig").splitlines()
    history = []
    for row in rows[1:]:
        fields = row.split(",")
        if len(fields) < 5:
            continue
        try:
            date = datetime.strptime(fields[0], "%m/%d/%Y").replace(
                tzinfo=timezone.utc
            )
            history.append((int(date.timestamp()), float(fields[4])))
        except ValueError:
            continue
    if not history:
        raise RuntimeError(f"CBOE 沒有 {index} 的歷史資料。")
    history.sort()
    closes = [close for _, close in history]
    return {
        "price": closes[-1],
        "rsi": rsi(closes[-251:]),
        "ma10": sum(closes[-10:]) / 10 if len(closes) >= 10 else None,
        "closes": closes,
    }


def volatility_index(symbol):
    try:
        return latest_chart(YahooClient(), symbol), "Yahoo Finance"
    except Exception as yahoo_error:
        try:
            return cboe_index_chart(symbol), "CBOE"
        except Exception as cboe_error:
            raise RuntimeError(
                f"Yahoo Finance: {yahoo_error}; CBOE: {cboe_error}"
            ) from cboe_error


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


@lru_cache(maxsize=1)
def twse_pe_table():
    request = urllib.request.Request(
        "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        rows = json.loads(response.read().decode("utf-8"))
    return {str(row.get("Code", "")): row for row in rows if row.get("Code")}


def twse_valuation(symbol):
    if not symbol.endswith(".TW"):
        return None
    code = symbol[:-3]
    if not re.fullmatch(r"\d{4,6}", code):
        return None
    row = twse_pe_table().get(code)
    if not row:
        return None
    try:
        pe = float(row.get("PEratio", ""))
    except (TypeError, ValueError):
        pe = None
    roc_date = str(row.get("Date", ""))
    pe_date = None
    if re.fullmatch(r"\d{7}", roc_date):
        pe_date = f"{int(roc_date[:3]) + 1911}-{roc_date[3:5]}-{roc_date[5:7]}"
    return {"pe": pe, "date": pe_date}


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
        (balance, ("totalAssets", "longTermDebt", "totalCurrentAssets", "totalCurrentLiabilities", "stockholdersEquity", "totalDebt", "cashAndCashEquivalents", "inventory", "accountsReceivable")),
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


def prepare_stock(symbol, summary, quote_chart, markets, errors, time_series, field_sources):
    price = summary.get("price") or {}
    details = summary.get("summaryDetail") or {}
    stats = summary.get("defaultKeyStatistics") or {}
    finance = summary.get("financialData") or {}
    annual = annual_statements(summary, time_series)
    for row in annual:
        origins = field_sources.setdefault(row["year"], {})
        for field, value in row.items():
            if field not in ("year", "fieldSources") and value is not None:
                origins.setdefault(field, "Yahoo Finance")
        row["fieldSources"] = origins
    latest = annual[0] if annual else {}

    actual_price = (
        field_value(price, "regularMarketPrice", "postMarketPrice")
        or field_value(finance, "currentPrice")
        or quote_chart.get("price")
    )
    twse_pe = markets.get("twse_valuation") or {}
    pe = twse_pe.get("pe") or field_value(details, "trailingPE")
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
            "trailingPEDate": twse_pe.get("date"),
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
            errors = {}
            sources = []
            summary = {}
            description_source = ""
            time_series = {}
            stock_chart = None
            yahoo = YahooClient()
            try:
                summary = yahoo.quote_summary(symbol)
                sources.append("Yahoo Finance")
                if field_value(summary.get("summaryProfile") or {}, "longBusinessSummary"):
                    description_source = "Yahoo Finance"
            except Exception as error:
                errors["yahoo_quote"] = str(error)
            try:
                time_series = yahoo.annual_time_series(symbol)
                if time_series:
                    sources.append("Yahoo Finance 財報")
            except Exception as error:
                errors["yahoo_financials"] = str(error)
            try:
                stock_chart = latest_chart(yahoo, symbol)
                if "Yahoo Finance" not in sources:
                    sources.append("Yahoo Finance 行情")
            except Exception as error:
                errors["yahoo_chart"] = str(error)

            if not summary.get("price", {}).get("regularMarketPrice"):
                try:
                    nasdaq_summary = NasdaqClient().quote(symbol)
                    merge_summary_fallback(summary, nasdaq_summary)
                    sources.append("Nasdaq")
                except Exception as error:
                    errors["nasdaq_quote"] = str(error)

            if stock_chart is None:
                try:
                    stock_chart = NasdaqClient().chart(symbol)
                    sources.append("Nasdaq 歷史行情")
                except Exception as error:
                    errors["nasdaq_chart"] = str(error)

            recent_years = sorted(
                time_series.values(),
                key=lambda row: row["year"],
                reverse=True,
            )[:5]
            field_sources = {
                year: {
                    field: "Yahoo Finance"
                    for field, value in row.items()
                    if field != "year" and value is not None
                }
                for year, row in time_series.items()
            }
            sec_fields = (
                "totalRevenue",
                "grossProfit",
                "operatingIncome",
                "netIncome",
                "dilutedEPS",
                "operatingCashFlow",
                "freeCashFlow",
                "investingCashFlow",
                "financingCashFlow",
                "totalAssets",
                "cashAndCashEquivalents",
                "inventory",
                "accountsReceivable",
                "longTermDebt",
                "totalCurrentAssets",
                "totalCurrentLiabilities",
                "stockholdersEquity",
                "interestExpense",
            )
            needs_sec = len(recent_years) < 5 or any(
                any(row.get(field) is None for field in sec_fields)
                for row in recent_years
            )
            if needs_sec:
                try:
                    sec_summary, sec_series = SecClient().annual_statements(symbol)
                    if not summary.get("price", {}).get("longName"):
                        summary["price"] = sec_summary["price"]
                    for year, values in sec_series.items():
                        target = time_series.setdefault(year, {"year": year})
                        origins = field_sources.setdefault(year, {})
                        for field, value in values.items():
                            if field != "year" and value is not None:
                                target[field] = value
                                origins[field] = "SEC EDGAR"
                    sources.append("SEC EDGAR")
                except Exception as error:
                    errors["sec_edgar"] = str(error)

            api_key = os.environ.get("FMP_API_KEY", "").strip()
            recent_years = sorted(
                time_series.values(),
                key=lambda row: row["year"],
                reverse=True,
            )[:5]
            needs_fmp = len(recent_years) < 5 or any(
                any(row.get(field) is None for field in sec_fields)
                for row in recent_years
            )
            if api_key and (
                not summary.get("price", {}).get("regularMarketPrice")
                or not time_series
                or stock_chart is None
                or needs_fmp
            ):
                try:
                    fmp_summary, fmp_series, fmp_chart = FMPClient(api_key).stock_data(symbol)
                    fmp_description = field_value(
                        fmp_summary.get("summaryProfile") or {}, "longBusinessSummary"
                    )
                    if not description_source and fmp_description:
                        description_source = "Financial Modeling Prep"
                    merge_summary_fallback(summary, fmp_summary)
                    for year, values in fmp_series.items():
                        target = time_series.setdefault(year, {"year": year})
                        origins = field_sources.setdefault(year, {})
                        for field, value in values.items():
                            if field != "year" and value is not None and target.get(field) is None:
                                target[field] = value
                                origins[field] = "Financial Modeling Prep"
                    if stock_chart is None:
                        stock_chart = fmp_chart
                    sources.append("Financial Modeling Prep")
                except Exception as error:
                    errors["fmp"] = str(error)

            if not summary or stock_chart is None:
                failure_details = "; ".join(
                    f"{provider}: {message}" for provider, message in errors.items()
                )
                raise RuntimeError(
                    "可用免費來源均未能完整取得公司行情，"
                    "請確認代號是否受 Nasdaq/SEC 支援或稍後重試。"
                    + (f" 詳細錯誤：{failure_details}" if failure_details else "")
                )

            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = {
                    "vix": pool.submit(volatility_index, "^VIX"),
                    "vxn": pool.submit(volatility_index, "^VXN"),
                    "semi_volatility": pool.submit(semi_volatility, YahooClient()),
                    "fear_greed": pool.submit(fear_greed),
                    "sp500_pe": pool.submit(sp500_pe),
                }
                if symbol.endswith(".TW"):
                    futures["twse_valuation"] = pool.submit(twse_valuation, symbol)
                markets = {}
                for key, future in futures.items():
                    try:
                        result = future.result()
                        if key in ("vix", "vxn"):
                            chart, provider = result
                            result = chart.get("price")
                            sources.append(f"{provider} {key.upper()}")
                        elif key == "semi_volatility":
                            result, provider = result
                            sources.append(f"{provider} SOXX 波動率")
                        elif key == "twse_valuation" and result:
                            sources.append("TWSE OpenAPI 本益比")
                        markets[key] = result
                    except Exception as error:
                        markets[key] = None
                        errors[key] = str(error)
            data = prepare_stock(
                symbol, summary, stock_chart, markets, errors, time_series, field_sources
            )
            qualitative_evidence = {}
            if symbol.upper().endswith((".TW", ".TWO")):
                try:
                    qualitative_evidence, qualitative_status = (
                        MopsClient.qualitative_evidence(symbol)
                    )
                except Exception as error:
                    qualitative_status = f"MOPS 質化來源不可用：{error}"
                    errors["mops_qualitative"] = str(error)
            else:
                try:
                    qualitative_evidence, qualitative_status = (
                        SecClient.qualitative_evidence(symbol)
                    )
                except Exception as error:
                    qualitative_status = f"SEC 質化來源不可用：{error}"
                    errors["sec_qualitative"] = str(error)
            data["qualitativeEvidence"] = qualitative_evidence
            data["qualitativeSourceStatus"] = qualitative_status
            data["descriptionSource"] = description_source
            data["dataSource"] = "、".join(dict.fromkeys(sources)) or "其他來源"
            data["providers"] = sources
            data["providerNotice"] = (
                f"本次使用資料來源：{data['dataSource']}。各來源依欄位自動備援，"
                "可能因代號、方案或服務狀態而無法提供全部資料。"
            )
            self.send_json(200, data)
        except Exception as error:
            self.send_json(502, {"error": str(error)})

    def allowed_origins(self):
        configured = os.environ.get("ALLOWED_ORIGINS")
        if configured is None:
            return DEFAULT_ALLOWED_ORIGINS
        return {origin.strip() for origin in configured.split(",") if origin.strip()}

    def send_json(self, status, data):
        content = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        origin = self.headers.get("Origin")
        if urllib.parse.urlparse(self.path).path == "/api/stock":
            self.send_header("Vary", "Origin")
            if origin in self.allowed_origins():
                self.send_header("Access-Control-Allow-Origin", origin)
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
