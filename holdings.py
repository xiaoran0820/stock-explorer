"""我的持仓：保存自己记录的买入记录（本机 JSON 文件），并校验提交的内容。"""
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from pathlib import Path
import json
import re
import threading
import uuid

SHANGHAI = timezone(timedelta(hours=8))
EARLIEST = date(1990, 1, 1)
MAX_RECORDS = 300


class HoldingFileError(Exception):
    """持仓文件读不出来（例如被手动改坏）。这时不能覆盖它，以免丢失已有记录。"""


def optional_number(value, label):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label}要填数字")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label}要填数字") from None
    if not isfinite(number):
        raise ValueError(f"{label}要填数字")
    return number


def parse_holding(payload, today=None):
    """校验前端提交的买入记录，返回整理好的字段；不合法时抛出带中文说明的 ValueError。"""
    today = today or datetime.now(SHANGHAI).date()
    if not isinstance(payload, dict):
        raise ValueError("提交的内容格式不对")
    code = str(payload.get("code") or "").strip()
    if not re.fullmatch(r"(60|68|00|30)[0-9]{4}", code):
        raise ValueError("请选择沪深 A 股（6 位代码，60、68、00、30 开头）")
    raw_date = str(payload.get("buy_date") or "").strip()
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw_date):
            raise ValueError
        buy_date = date.fromisoformat(raw_date)
    except ValueError:
        raise ValueError("请填写有效的买入日期（YYYY-MM-DD）") from None
    if buy_date > today:
        raise ValueError("买入日期不能晚于今天")
    if buy_date < EARLIEST:
        raise ValueError("买入日期不能早于 1990-01-01")
    price = optional_number(payload.get("buy_price"), "买入价")
    if price is not None and not 0 < price < 100_000:
        raise ValueError("买入价要大于 0")
    shares = optional_number(payload.get("shares"), "股数")
    if shares is not None and (shares != int(shares) or not 0 < shares <= 1_000_000_000):
        raise ValueError("股数要填正整数")
    name = re.sub(r"\s+", " ", str(payload.get("name") or "")).strip()[:20]
    return {"code": code, "name": name, "buy_date": buy_date.isoformat(), "buy_price": price,
            "shares": int(shares) if shares is not None else None}


class HoldingStore:
    """买入记录保存在一个 JSON 文件里；读写都加锁，写入先写临时文件再替换，避免写到一半损坏。"""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def load(self):
        with self.lock:
            return self._read()

    def add(self, record):
        with self.lock:
            records = self._read()
            if len(records) >= MAX_RECORDS:
                raise ValueError(f"最多记录 {MAX_RECORDS} 条，请先删除不需要的记录")
            entry = {"id": uuid.uuid4().hex, **record,
                     "created_at": datetime.now(SHANGHAI).isoformat(timespec="seconds")}
            self._write([*records, entry])
            return entry

    def remove(self, record_id):
        with self.lock:
            records = self._read()
            kept = [record for record in records if record.get("id") != record_id]
            if len(kept) == len(records):
                return False
            self._write(kept)
            return True

    def _read(self):
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HoldingFileError(f"持仓文件读取失败（{self.path}）") from exc
        records = data.get("records") if isinstance(data, dict) else None
        if not isinstance(records, list):
            raise HoldingFileError(f"持仓文件格式不对（{self.path}）")
        return [record for record in records if isinstance(record, dict)]

    def _write(self, records):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": 1, "records": records}, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        temporary.replace(self.path)
