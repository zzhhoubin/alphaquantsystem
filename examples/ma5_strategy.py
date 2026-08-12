from __future__ import annotations

import os

# 必须在 import matplotlib / backtrader 之前设置；PyCharm sitecustomize 常抢先加载 GUI 后端
os.environ["MPLBACKEND"] = "Agg"

import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt
plt.switch_backend("Agg")

import backtrader as bt
import pandas as pd

# ===================== 手续费参数设置 =====================
COMMISSION_RATE = 0.5 / 10000  # 单边万分之0.5手续费
SLIPPAGE = 0.0  # 无滑点

# ===================== 数据参数 =====================
SYMBOL = "159509.SZ"
# 通达信导出的 1 分钟线文本
TDX_EXPORT_PATH = Path(r"C:\new_tdx\T0002\export\SZ#159509.txt")
# 过滤掉每日 09:31–10:30（含）的分钟 bar
FILTER_TIME_START = 931   # 0931
FILTER_TIME_END = 1030    # 1030
# 通达信: EXP1:EXPMA(CLOSE,M1);  其中 EXPMA ≡ EMA，alpha=2/(M1+1)
M1 = 5
# 导出指定交易日分钟 K + EXPMA（截断前/后）
EXPORT_KLINE_DATE = "2026-08-07"


def trunc3(x: float) -> float:
    """截断到 3 位小数（不四舍五入）。例: 2.8917 / 2.8911 → 2.891。"""
    if x != x:  # NaN
        return x
    # 正数加微小偏移，避免 2.891*1000 因浮点变成 2890.999...
    if x >= 0:
        return math.trunc(x * 1000 + 1e-9) / 1000.0
    return math.trunc(x * 1000 - 1e-9) / 1000.0


class TdxEXPMA(bt.Indicator):
    """
    通达信 EXPMA / EMA：
        Y = (2*X + (N-1)*Y') / (N+1)
      即 alpha = 2/(N+1)，首根以收盘价为种子（与 ewm(span=N, adjust=False) 一致）。

    注意：不要用 bt.ind.EMA——其种子为前 N 根 SMA，与通达信有偏差。
    """

    lines = ("expma",)
    params = (("period", 5),)
    plotinfo = dict(subplot=False)

    def __init__(self):
        self.addminperiod(1)
        self.alpha = 2.0 / (self.p.period + 1)
        self.alpha1 = 1.0 - self.alpha

    def next(self):
        if len(self) == 1:
            self.lines.expma[0] = self.data[0]
        else:
            self.lines.expma[0] = (
                self.alpha * self.data[0] + self.alpha1 * self.lines.expma[-1]
            )


def export_day_kline_expma(
    bars: pd.DataFrame,
    trade_date: str = EXPORT_KLINE_DATE,
    m1: int = M1,
    path: Path | None = None,
) -> Path:
    """
    导出指定日期的分钟 K 线，以及 EXPMA 截断前/后数值。

    EXPMA 在全量序列上用通达信公式计算后截取当日（跨日预热）：
        Y = (2*C + (M1-1)*Y') / (M1+1)，首根种子=首根收盘价。
    """
    if path is None:
        day_tag = trade_date.replace("-", "")
        path = Path(__file__).with_name(
            f"ma5_{SYMBOL.replace('.', '_')}_{day_tag}_kline_expma.xlsx"
        )
    path = Path(path)

    full = bars.sort_index().copy()
    # 通达信 EXPMA ≡ pandas ewm(span=M1, adjust=False)
    full["EXPMA_原始"] = full["close"].ewm(span=m1, adjust=False).mean()
    full["EXPMA_截断3位"] = full["EXPMA_原始"].map(trunc3)

    day = pd.Timestamp(trade_date).date()
    out = full.loc[full.index.date == day].copy()
    if out.empty:
        raise RuntimeError(f"无 {trade_date} 的分钟 K 线可导出")

    export_df = out.reset_index()
    # index 列名可能是 datetime
    if "datetime" not in export_df.columns:
        export_df = export_df.rename(columns={export_df.columns[0]: "datetime"})
    export_df["日期"] = export_df["datetime"].dt.strftime("%Y-%m-%d")
    export_df["时间"] = export_df["datetime"].dt.strftime("%H:%M")
    export_df = export_df[
        [
            "日期",
            "时间",
            "datetime",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "EXPMA_原始",
            "EXPMA_截断3位",
        ]
    ].rename(
        columns={
            "open": "开盘",
            "high": "最高",
            "low": "最低",
            "close": "收盘",
            "volume": "成交量",
            "datetime": "日期时间",
        }
    )

    try:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            export_df.to_excel(writer, sheet_name=trade_date, index=False)
    except ImportError:
        csv_path = path.with_suffix(".csv")
        export_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        print(f"未安装 openpyxl，已导出 CSV: {csv_path}")
        return csv_path

    print(
        f"已导出 {trade_date} 分钟K+EXPMA: {path} （{len(export_df)} 根，"
        f"M1={m1}）"
    )
    return path


class Ma5MinuteStrategy(bt.Strategy):
    """
    1 分钟 EXPMA 拐点策略（EXP1:EXPMA(CLOSE,M1)）：
    - 无仓且 EXP1[0] > EXP1[-1] → 全仓买入
    - 有仓且 EXP1[0] < EXP1[-1] → 清仓
    - EXP1 相等时不操作
    """

    params = dict(
        commission_rate=COMMISSION_RATE,
        lot_size=100,  # A 股 ETF 最小交易单位 100 股
        symbol=SYMBOL,
        m1=M1,
    )

    def __init__(self):
        # EXP1:EXPMA(CLOSE,M1) — 通达信指数平均（非 bt.ind.EMA 的 SMA 种子）
        self.exp1 = TdxEXPMA(self.data.close, period=self.p.m1)
        self.order = None
        self.trade_count = 0
        self.fills: list[dict] = []
        self.round_trips: list[dict] = []
        self._entry_size = 0.0
        self._entry_price = 0.0
        self._entry_value = 0.0

    def notify_order(self, order):
        # 必须清空挂单引用，否则首次下单后 next() 会永久 return，后续信号全部丢失
        if order.status == order.Completed:
            dt = bt.num2date(order.executed.dt)
            side = "买入" if order.isbuy() else "卖出"
            size = abs(float(order.executed.size))
            price = float(order.executed.price)
            amount = size * price
            commission = float(order.executed.comm)
            if order.isbuy():
                self._entry_size = size
                self._entry_price = price
                self._entry_value = amount
            self.fills.append(
                {
                    "成交序号": len(self.fills) + 1,
                    "成交时间": dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "标的": self.p.symbol,
                    "方向": side,
                    "成交数量": size,
                    "成交价格": price,
                    "成交金额": amount,
                    "手续费": commission,
                    "成交后现金": float(self.broker.getcash()),
                    "成交后总资产": float(self.broker.getvalue()),
                    "持仓数量": float(self.position.size),
                    "EXP1": trunc3(float(self.exp1[0])),
                }
            )
        if order.status in (
            order.Completed,
            order.Canceled,
            order.Margin,
            order.Rejected,
        ):
            self.order = None

    def notify_trade(self, trade):
        if not trade.isclosed:
            return
        self.trade_count += 1
        entry_dt = bt.num2date(trade.dtopen)
        exit_dt = bt.num2date(trade.dtclose)
        pnl = float(trade.pnl)
        pnlcomm = float(trade.pnlcomm)
        entry_price = self._entry_price or float(trade.price)
        entry_size = self._entry_size
        entry_value = self._entry_value or (entry_size * entry_price)
        exit_price = 0.0
        for fill in reversed(self.fills):
            if fill["方向"] == "卖出":
                exit_price = float(fill["成交价格"])
                break
        self.round_trips.append(
            {
                "回合序号": self.trade_count,
                "标的": self.p.symbol,
                "开仓时间": entry_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "平仓时间": exit_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "开仓均价": entry_price,
                "平仓均价": exit_price,
                "持仓数量": entry_size,
                "毛利润": pnl,
                "净利润(扣费)": pnlcomm,
                "手续费合计": pnl - pnlcomm,
                "收益率%": (pnlcomm / entry_value * 100.0) if entry_value else 0.0,
            }
        )

    def _full_buy_size(self, price: float) -> int:
        """按可用现金全仓，预留单边佣金，并向下取整到 lot_size。"""
        if price <= 0:
            return 0
        cash = self.broker.getcash()
        max_notional = cash / (1.0 + self.p.commission_rate)
        size = int(max_notional / price)
        lot = int(self.p.lot_size)
        if lot > 1:
            size = (size // lot) * lot
        return max(size, 0)

    def next(self):
        if self.order:
            return

        # 比较前截断到 3 位小数：2.8917 与 2.8911 均视为 2.891，相等则不交易
        exp1_current = trunc3(float(self.exp1[0]))
        exp1_prev = trunc3(float(self.exp1[-1]))
        price = float(self.data.close[0])

        if not self.position:
            if exp1_current > exp1_prev:
                size = self._full_buy_size(price)
                if size > 0:
                    self.order = self.buy(size=size)
        else:
            if exp1_current < exp1_prev:
                self.order = self.sell(size=self.position.size)


def analysis_result(cerebro, strategy, start_cash: float) -> None:
    """输出回测摘要；夏普/回撤来自独立 Analyzer，而非 PyFolio。"""
    sharpe_an = strategy.analyzers.sharpe.get_analysis()
    dd_an = strategy.analyzers.drawdown.get_analysis()
    sharpe = sharpe_an.get("sharperatio")
    max_dd = dd_an.get("max", {}).get("drawdown")
    final_value = cerebro.broker.getvalue()
    total_return = (final_value - start_cash) / start_cash

    print("=" * 60)
    print(f"总交易次数: {strategy.trade_count}")
    print(f"初始资金: {start_cash:.0f}元")
    print(f"最终总资产: {final_value:.2f}元")
    print(f"总收益率: {total_return:.2%}")
    print(
        "年化夏普比率:",
        "N/A" if sharpe is None else round(float(sharpe), 4),
    )
    print(
        "最大回撤:",
        "N/A" if max_dd is None else f"{round(float(max_dd), 4)} %",
    )
    print("=" * 60)


def export_trades_excel(strategy: Ma5MinuteStrategy, path: Path) -> Path:
    """导出成交明细 + 完整开平仓回合到 Excel。"""
    path = Path(path)
    fills_df = pd.DataFrame(strategy.fills)
    trips_df = pd.DataFrame(strategy.round_trips)
    if fills_df.empty:
        fills_df = pd.DataFrame(
            columns=[
                "成交序号", "成交时间", "标的", "方向", "成交数量", "成交价格",
                "成交金额", "手续费", "成交后现金", "成交后总资产", "持仓数量", "EXP1",
            ]
        )
    if trips_df.empty:
        trips_df = pd.DataFrame(
            columns=[
                "回合序号", "标的", "开仓时间", "平仓时间", "开仓均价", "平仓均价",
                "持仓数量", "毛利润", "净利润(扣费)", "手续费合计", "收益率%",
            ]
        )
    try:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            fills_df.to_excel(writer, sheet_name="成交明细", index=False)
            trips_df.to_excel(writer, sheet_name="开平仓回合", index=False)
    except ImportError:
        fills_csv = path.with_name(path.stem + "_成交明细.csv")
        trips_csv = path.with_name(path.stem + "_开平仓回合.csv")
        fills_df.to_csv(fills_csv, index=False, encoding="utf-8-sig")
        trips_df.to_csv(trips_csv, index=False, encoding="utf-8-sig")
        print(f"未安装 openpyxl，已导出 CSV: {fills_csv} / {trips_csv}")
        return fills_csv
    print(f"交易明细已导出: {path} （成交 {len(fills_df)} 笔，回合 {len(trips_df)} 个）")
    return path


def load_minute_bars(path: Path = TDX_EXPORT_PATH) -> pd.DataFrame:
    """
    读取通达信导出的 1 分钟线 txt，并去掉每日 09:31–10:30 的记录。

    文件格式（GBK）::
        标题行
        日期\\t时间\\t开盘\\t最高\\t最低\\t收盘\\t成交量\\t成交额
        2026/03/09\\t0931\\t...
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"通达信导出文件不存在: {path}")

    raw = pd.read_csv(
        path,
        sep="\t",
        header=1,
        encoding="gbk",
        dtype=str,
        engine="python",
    )
    raw = raw.dropna(how="all")
    first_col = raw.columns[0]
    raw = raw[~raw[first_col].astype(str).str.startswith("#")]

    if raw.shape[1] < 7:
        raise ValueError(f"导出文件列数不足: {path} cols={list(raw.columns)}")
    df = pd.DataFrame(
        {
            "date": raw.iloc[:, 0].astype(str).str.strip(),
            "time": raw.iloc[:, 1].astype(str).str.strip(),
            "open": raw.iloc[:, 2],
            "high": raw.iloc[:, 3],
            "low": raw.iloc[:, 4],
            "close": raw.iloc[:, 5],
            "volume": raw.iloc[:, 6],
        }
    )
    df["time_hhmm"] = pd.to_numeric(df["time"], errors="coerce")
    before = len(df)
    df = df[
        (df["time_hhmm"].notna())
        & ~(
            (df["time_hhmm"] >= FILTER_TIME_START)
            & (df["time_hhmm"] <= FILTER_TIME_END)
        )
    ].copy()
    dropped = before - len(df)

    df["datetime"] = pd.to_datetime(
        df["date"] + " " + df["time"].str.zfill(4),
        format="%Y/%m/%d %H%M",
        errors="coerce",
    )
    df = df.dropna(subset=["datetime"]).sort_values("datetime")
    df = df.set_index("datetime")[["open", "high", "low", "close", "volume"]]
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    if df.empty:
        raise RuntimeError(f"本地文件清洗后无可用 K 线: {path}")
    print(
        f"已过滤 09:31-10:30 共 {dropped} 根，剩余 {len(df)} 根 "
        f"({df.index.min()} ~ {df.index.max()})"
    )
    return df


if __name__ == "__main__":
    cerebro = bt.Cerebro()
    cerebro.addstrategy(Ma5MinuteStrategy)

    df = load_minute_bars(TDX_EXPORT_PATH)
    print(f"已加载本地文件 {TDX_EXPORT_PATH.name} → {SYMBOL} 1分钟K线 {len(df)} 根")
    export_day_kline_expma(df, trade_date=EXPORT_KLINE_DATE, m1=M1)
    data = bt.feeds.PandasData(dataname=df)
    cerebro.adddata(data)

    start_cash = 100000
    cerebro.broker.setcash(start_cash)
    cerebro.broker.setcommission(commission=COMMISSION_RATE)
    cerebro.broker.set_slippage_perc(SLIPPAGE)
    cerebro.broker.set_coc(True)

    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe", timeframe=bt.TimeFrame.Days)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")

    print(f"回测初始资金:{cerebro.broker.getvalue():.2f}元")
    strategy = cerebro.run()[0]
    analysis_result(cerebro, strategy, start_cash)

    excel_path = Path(__file__).with_name(f"ma5_{SYMBOL.replace('.', '_')}_trades.xlsx")
    export_trades_excel(strategy, excel_path)

    try:
        matplotlib.use("Agg", force=True)
        plt.switch_backend("Agg")
        figs = cerebro.plot(style="candlestick", iplot=False)
        if figs and figs[0]:
            out = Path(__file__).with_name(f"ma5_{SYMBOL.replace('.', '_')}_plot.png")
            figs[0][0].savefig(out, dpi=150, bbox_inches="tight")
            print(f"回测图已保存: {out}")
    except Exception as exc:
        print(f"跳过绘图（当前环境无可用 GUI 后端）: {exc}")
