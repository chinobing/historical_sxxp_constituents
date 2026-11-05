import os
from datetime import date, datetime
import pandas as pd
import requests
import io
import re

# Set working directory to the script's location
os.chdir(os.path.dirname(os.path.abspath(__file__)))


# ★ 国家 → 交易所后缀映射表
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
}


def add_ticker_suffix(ticker: str, country: str) -> str:
    """
    根据国家给 ticker 添加对应的交易所后缀。
    如果 ticker 已经有后缀（如 ASML.AS），则保持不变。
    """
    if not isinstance(ticker, str) or not ticker.strip():
        return ticker

    ticker = ticker.strip()

    # 已有后缀，直接返回
    if re.search(r'\.[A-Z]{1,3}$', ticker):
        return ticker

    suffix = COUNTRY_SUFFIX.get(country)
    if suffix:
        return ticker + suffix
    return ticker  # 未知国家，原样返回


def create_constituents(df):
    ticker_list = df['ticker'].tolist()
    res_string = ','.join(ticker_list)
    results_df = pd.DataFrame({
        'date': [date.today()],
        'tickers': [res_string],
    })
    return results_df


def diff_tickers(historical_df):
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

    # ── 读取历史数据 ────────────────────────────────────────────────
    hist_filename = 'stoxx_europe_600_historical_components.csv'

    if os.path.exists(hist_filename):
        hist_df = pd.read_csv(hist_filename)
    else:
        hist_df = pd.DataFrame(columns=['date', 'tickers'])

    # ── 抓取当前成分股 ──────────────────────────────────────────────
    url = 'https://en.wikipedia.org/wiki/STOXX_Europe_600'
    r = requests.get(url, headers=header)
    html_stream = io.StringIO(r.text)
    tables = pd.read_html(html_stream, header=0)

    # Index components 表通常最大（~600 行），且包含 ticker 和 country 列
    constituents = None
    for tbl in sorted(tables, key=lambda t: len(t), reverse=True):
        cols_lower = [str(c).lower().strip() for c in tbl.columns]
        if 'ticker' in cols_lower and 'country' in cols_lower:
            constituents = tbl
            break

    if constituents is None:
        raise RuntimeError("Could not locate the Index components table.")

    # 统一列名：小写 + 去除 [1] 脚注
    constituents.columns = [
        re.sub(r'\[.*\]', '', str(c)).strip().lower()
        for c in constituents.columns
    ]

    # 映射关键列名
    col_map = {}
    for c in constituents.columns:
        if 'ticker' in c:
            col_map[c] = 'ticker'
        elif 'country' in c:
            col_map[c] = 'country'
        elif 'company' in c:
            col_map[c] = 'company'
    constituents = constituents.rename(columns=col_map)

    # ★ 核心逻辑：根据国家给 ticker 添加后缀
    constituents['ticker'] = constituents.apply(
        lambda row: add_ticker_suffix(row.get('ticker'), row.get('country')),
        axis=1
    )

    # ── 保存当前成分股快照 ──────────────────────────────────────────
    constituents['date'] = date.today()
    constituents.to_csv('stoxx_europe_600_constituents.csv', index=False)

    # ── 仅保留 ticker + date 用于历史追踪 ───────────────────────────
    df_tickers = constituents[['ticker', 'date']].copy()
    df_tickers.sort_values(by='ticker', ascending=True, inplace=True)

    df = create_constituents(df_tickers)
    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
    final = pd.concat([hist_df, df], ignore_index=True)
    final = final.drop_duplicates(subset=['date', 'tickers'], keep='last')
    final.to_csv(hist_filename, index=False)

    # ── 计算新增/移除 ───────────────────────────────────────────────
    historical = pd.read_csv(hist_filename, index_col='date')
    historical['tickers'] = historical['tickers'].apply(lambda x: sorted(x.split(',')))
    historical = historical.sort_index()
    combined = diff_tickers(historical)
    combined.to_csv('stoxx_europe_600_changes.csv')

    # ── 更新 README ─────────────────────────────────────────────────
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