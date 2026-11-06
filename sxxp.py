import os
from datetime import date, datetime
import pandas as pd
import requests
import io
import re

# Set working directory to the script's location
os.chdir(os.path.dirname(os.path.abspath(__file__)))


# Mapping from country (as it appears on Wikipedia) to exchange suffix
COUNTRY_SUFFIX = {
    'United Kingdom':  '.L',
    'Germany':         '.DE',
    'France':          '.PA',
    'Netherlands':     '.AS',
    'Switzerland':     '.SW',
    'Spain':           '.MC',
    'Italy':           '.MI',
    'Denmark':         '.CO',
    'Sweden':          '.ST',
    'Norway':          '.OL',
    'Finland':         '.HE',
    'Belgium':         '.BR',
    'Austria':         '.VI',
    'Ireland':         '.IR',
    'Portugal':        '.LS',
    'Poland':          '.WA',
    'Luxembourg':      '.LU',
    'Greece':          '.AT',
}


def clean_ticker_base(ticker: str) -> str:
    """
    Clean the base part of a ticker symbol before adding a suffix.
    Handles several common issues found on Wikipedia tables:
      - Spaces in the symbol         → replace with '-'  (ATCO A → ATCO-A)
      - Trailing dot (no suffix)     → remove            (BA.     → BA)
      - Multiple dots in the middle  → collapse to one   (BA..     → BA.)
    Returns only the cleaned base (no country suffix attached yet).
    """
    if not isinstance(ticker, str) or not ticker.strip():
        return ticker

    base = ticker.strip()

    # 1. 去掉末尾多余的句点（如 "BA." → "BA"）
    base = base.rstrip('.')

    # 2. 把连续多个句点合并为一个（如 "BA.." → "BA."，配合上一步等于 "BA"）
    #    更一般地："A..B" → "A.B"
    base = re.sub(r'\.{2,}', '.', base)

    # 3. 把空格/制表符等空白替换为连字符（ATCO A → ATCO-A）
    base = re.sub(r'\s+', '-', base)

    return base


# Tickers in this list will NOT be cleaned (no space→'-', no trailing-dot removal).
# They will only get the country-specific suffix appended.
# Add any ticker here that should be left exactly as it appears on Wikipedia.
SKIP_CLEAN_TICKERS = [
    'MT',
]

def add_ticker_suffix(ticker: str, country: str) -> str:
    """
    Append the correct exchange suffix to a ticker based on the country.
    First cleans the base symbol, then adds the country-specific suffix.
    Examples:
      ATCO A  + Sweden   → ATCO-A.ST
      BA.     + UK        → BA.L
      III     + UK        → III.L
      ASML.AS + Netherlands → ASML.AS   (already has suffix, kept as-is)
      MT      + France    → MT          (in SKIP_CLEAN_TICKERS, untouched)
    """
    if not isinstance(ticker, str) or not ticker.strip():
        return ticker

    base = ticker.strip()

    # ── 0. Return as-is for tickers in the whitelist ──────────────────
    if base in SKIP_CLEAN_TICKERS:
        return base

    # ── 1. 先判断是否已有合法后缀（字母后缀）────────────────────────
    # 已有后缀：BA.L / ASML.AS 等
    if re.search(r'\.[A-Z]{1,3}$', base):
        # 只做清理：合并重复句点、去尾点、去空格
        # 拆成 base + 已有后缀，只清理 base 部分
        b, _, sfx = base.rpartition('.')
        if re.match(r'^[A-Z]{1,3}$', sfx):
            cleaned_base = clean_ticker_base(b)
            return f"{cleaned_base}.{sfx}"

    # ── 2. 没有后缀，先清理 base，再追加国家后缀 ─────────────────────
    cleaned = clean_ticker_base(base)
    suffix = COUNTRY_SUFFIX.get(country)
    if suffix:
        return cleaned + suffix

    return cleaned  # 未知国家，返回清理后的 ticker

def create_constituents(df):
    """Create a DataFrame with the current constituents as a comma-separated string."""
    ticker_list = df['ticker'].tolist()
    res_string = ','.join(ticker_list)

    results_df = pd.DataFrame({
        'date': [date.today()],
        'tickers': [res_string],
    })
    return results_df


def diff_tickers(historical_df):
    """Compare consecutive snapshots and return added/removed tickers."""
    added_tickers = {}
    removed_tickers = {}

    for i in range(1, len(historical_df.index)):
        prev_tickers = set(historical_df.iloc[i - 1].tickers)
        current_tickers = set(historical_df.iloc[i].tickers)

        added = current_tickers - prev_tickers
        if added:
            idx_val = historical_df.index[i]
            added_tickers[idx_val] = list(added)

        removed = prev_tickers - current_tickers
        if removed:
            idx_val = historical_df.index[i]
            removed_tickers[idx_val] = list(removed)

    at_only = pd.DataFrame(added_tickers.items(), columns=['date', 'added_tickers'])
    rt_only = pd.DataFrame(removed_tickers.items(), columns=['date', 'removed_tickers'])
    combined = pd.merge(at_only, rt_only, on=['date'], how='outer')
    combined = combined.sort_values('date', ascending=True)
    combined = combined.set_index('date')

    return combined


def main():
    header = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/50.0.2661.75 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest"
    }

    # ── Read historical data ────────────────────────────────────────────────
    hist_filename = 'stoxx_europe_600_historical_components.csv'

    if os.path.exists(hist_filename):
        hist_df = pd.read_csv(hist_filename)
    else:
        hist_df = pd.DataFrame(columns=['date', 'tickers'])

    # ── Fetch current constituents from Wikipedia ────────────────────────────
    url = 'https://en.wikipedia.org/wiki/STOXX_Europe_600'
    r = requests.get(url, headers=header)
    html_stream = io.StringIO(r.text)

    tables = pd.read_html(html_stream, header=0)

    # The "Index components" table is large (~600 rows). It is typically the
    # last (largest) table on the page, with columns: Company, Ticker, ISIN,
    # Country, GICS Sector / Industry.
    constituents = None
    for tbl in sorted(tables, key=lambda t: len(t), reverse=True):
        cols_lower = [str(c).lower().strip() for c in tbl.columns]
        if 'ticker' in cols_lower and 'country' in cols_lower:
            constituents = tbl
            break

    if constituents is None:
        raise RuntimeError("Could not locate the Index components table on the page.")

    # Normalize column names (lowercase + strip footnote brackets like [1])
    constituents.columns = [
        re.sub(r'\[.*\]', '', str(c)).strip().lower()
        for c in constituents.columns
    ]

    # Standardise column names (Wikipedia sometimes uses 'ticker symbol' etc.)
    col_map = {}
    for c in constituents.columns:
        if 'ticker' in c:
            col_map[c] = 'ticker'
        elif 'country' in c:
            col_map[c] = 'country'
        elif 'company' in c:
            col_map[c] = 'company'
    constituents = constituents.rename(columns=col_map)

    # ── Add exchange suffix to each ticker based on country ──────────────────
    constituents['ticker'] = constituents.apply(
        lambda row: add_ticker_suffix(row.get('ticker'), row.get('country')),
        axis=1
    )

    # ── Save a snapshot of the current components ────────────────────────────
    constituents['date'] = date.today()
    constituents.to_csv('stoxx_europe_600_constituents.csv', index=False)

    # ── Keep only ticker + date for the historical tracking file ─────────────
    df_tickers = constituents[['ticker', 'date']].copy()
    df_tickers.sort_values(by='ticker', ascending=True, inplace=True)

    df = create_constituents(df_tickers)
    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')

    final = pd.concat([hist_df, df], ignore_index=True)
    final = final.drop_duplicates(subset=['date', 'tickers'], keep='last')
    final.to_csv(hist_filename, index=False)

    # ── Compute additions / removals ─────────────────────────────────────────
    historical = pd.read_csv(hist_filename, index_col='date')
    historical['tickers'] = historical['tickers'].apply(lambda x: sorted(x.split(',')))
    historical = historical.sort_index()

    combined = diff_tickers(historical)
    combined.to_csv('stoxx_europe_600_changes.csv')

    # ── Rewrite README ───────────────────────────────────────────────────────
    if not combined.empty:
        changes_readme = combined.iloc[-50:].sort_index(ascending=False)
        markdown_table = changes_readme.to_markdown()
    else:
        markdown_table = "No historical variations recorded yet."

    yml_path = "README.md"
    if not os.path.exists(yml_path):
        with open(yml_path, "w", encoding="UTF-8") as f:
            f.write("\n" * 10)

    with open(yml_path, "r+", encoding="UTF-8") as f:
        list1 = f.readlines()

    current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    list1 = list1[:10]
    list1 += [f'STOXX Europe 600 Constituents Auto Renew at **{current_datetime}**']
    list1 += ['\n\n']
    list1 += [markdown_table]

    with open(yml_path, "w+", encoding="UTF-8") as f:
        f.writelines(list1)

    print(f"Done. {len(df_tickers)} constituents saved.")


if __name__ == '__main__':
    main()
