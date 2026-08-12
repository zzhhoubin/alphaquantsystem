"""
诊断 QMT 可用资金同步为 0 的原因。

用法:
    D:/quant/venv/Scripts/python.exe alphaQuantSystem/tools/diag_qmt_cash.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alphaQuantSystem.core import EventEngine
from alphaQuantSystem.gateway.qmt_gateway import (
    ACCOUNT_REAL,
    ACCOUNT_SIMULATED,
    QMT_PATH_REAL,
    QMT_PATH_SIMULATED,
    QmtGateway,
)
from alphaQuantSystem.services.account import AccountService
from alphaQuantSystem.services.position import PositionService


def _asset_fields(asset) -> dict:
    if asset is None:
        return {}
    out = {}
    for name in dir(asset):
        if name.startswith("_"):
            continue
        try:
            val = getattr(asset, name)
        except Exception:
            continue
        if callable(val):
            continue
        out[name] = val
    return out


def _probe_account(label: str, is_real: bool, wait_sec: float = 3.0) -> None:
    print(f"\n{'=' * 60}")
    print(f"探测: {label} | is_real={is_real}")
    print(f"  path={QMT_PATH_REAL if is_real else QMT_PATH_SIMULATED}")
    print(f"  account={ACCOUNT_REAL if is_real else ACCOUNT_SIMULATED}")
    print("=" * 60)

    ee = EventEngine(sync_mode=True)
    gw = QmtGateway(ee, is_real=is_real)
    gw.connect()

    print(f"  connected={gw._connected}")
    print(f"  ready={gw._is_ready()}")

    snap_before = gw.get_account_snapshot()
    print(f"  get_account_snapshot (immediate): cash={snap_before.get('cash'):,.2f} "
          f"total={snap_before.get('total_value'):,.2f}")

    if gw._is_ready():
        try:
            gw.query_account()
            print(f"  query_account() async sent, wait {wait_sec}s for callback...")
        except TypeError as e:
            print(f"  query_account() skipped: {e}")
        deadline = time.time() + wait_sec
        while time.time() < deadline:
            evt = ee.poll()
            if evt is not None:
                ee.dispatch(evt)
            time.sleep(0.05)

        snap_after = gw.get_account_snapshot()
        print(f"  get_account_snapshot (after wait): cash={snap_after.get('cash'):,.2f} "
              f"total={snap_after.get('total_value'):,.2f}")
        print(f"  _account_cache={gw._account_cache}")

        trader = gw._xt_trader
        acc = gw._acc
        if trader and acc:
            fn = getattr(trader, "query_stock_asset", None)
            if callable(fn):
                try:
                    asset = fn(acc)
                    print(f"  query_stock_asset sync return: {type(asset)}")
                    if asset is not None:
                        fields = _asset_fields(asset)
                        for k, v in sorted(fields.items()):
                            print(f"    {k} = {v}")
                except Exception as e:
                    print(f"  query_stock_asset sync ERROR: {e}")

        positions = gw.get_all_position_snapshots()
        print(f"  positions count={len(positions)}")
        for sym, p in list(positions.items())[:5]:
            print(f"    {sym}: vol={p.get('volume')} avail={p.get('available')}")

        acc_svc = AccountService(initial_cash=1_000_000)
        pos_svc = PositionService()
        acc_svc.set_cash(float(snap_after.get("cash", 0) or 0))
        pos_svc.sync_from_broker(positions)
        print(f"  AccountService.available={acc_svc.available:,.2f}")
        print(f"  AccountService.total_value(pos)={acc_svc.total_value(pos_svc.total_market_value()):,.2f}")
    else:
        print("  [SKIP] QMT 未就绪，无法查询资金")

    gw.disconnect()


def main() -> None:
    try:
        from xtquant import xttrader  # noqa: F401
        print("xtquant: OK")
    except ImportError as e:
        print(f"xtquant: MISSING ({e})")
        sys.exit(1)

    print(f"Python: {sys.executable}")
    print(f"ACCOUNT_SIMULATED={ACCOUNT_SIMULATED}")
    print(f"ACCOUNT_REAL={ACCOUNT_REAL}")
    print(f"main.py live uses is_live=True -> account {ACCOUNT_REAL}")

    _probe_account("模拟账号 (is_live=False)", is_real=False)
    _probe_account("实盘账号 (is_live=True, 与 main.py live 一致)", is_real=True)

    print("\n" + "=" * 60)
    print("诊断完成。若 is_live=True 的 cash=0 而 is_live=False 有资金，")
    print("说明 main.py 查错了账号，应改 use_qmt(is_live=False) 或修改 ACCOUNT_REAL。")
    print("若两者 cash 均为 0 但 query_stock_asset 字段有值，则是字段映射问题。")
    print("=" * 60)


if __name__ == "__main__":
    main()
