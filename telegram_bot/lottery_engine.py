import os
import re
import urllib.request
from datetime import datetime
from html.parser import HTMLParser

# Default Pricing Config (Khớp với thiết lập trên Web)
DEFAULT_PRICE_CONFIG = {
    "rateDeComm": 82.0,      # Đề giá xác (hoa hồng): 82%
    "rateDePayout": 80.0,    # Đề trúng: 1 ăn 80
    "rateLoCost": 21.65,     # Lô vốn: 21.65k / điểm
    "rateLoPayout": 80.0,    # Lô trúng: 80k / điểm (mỗi nháy)
    "rateXienComm": 65.0,    # Xiên giá xác: 65%
    "rateXien2Payout": 11.0, # Xiên 2: 1 ăn 11
    "rateXien3Payout": 45.0, # Xiên 3: 1 ăn 45
    "rateXien4Payout": 140.0,# Xiên 4: 1 ăn 140
    "rate3CComm": 75.0,      # 3 Càng giá xác: 75%
    "rate3CPayout": 400.0,   # 3 Càng trúng: 1 ăn 400
    "rate3CApMa": 5.0        # 3 Càng áp má: 1 ăn 5
}


class MinhNgocParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.db = None
        self.prizes = []
        self.in_target = False
        self.target_class = None

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        classes = attr_dict.get("class", "").split()
        for c in ["giaidb", "giai1", "giai2", "giai3", "giai4", "giai5", "giai6", "giai7"]:
            if c in classes:
                self.in_target = True
                self.target_class = c
                break

    def handle_endtag(self, tag):
        self.in_target = False

    def handle_data(self, data):
        if self.in_target:
            nums = re.findall(r"\b\d+\b", data)
            for n in nums:
                if self.target_class == "giaidb" and not self.db:
                    self.db = n
                self.prizes.append(n)


def fetch_xsmb() -> dict:
    url = "https://www.minhngoc.net.vn/getkqxs/mien-bac.js"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8", errors="ignore")

        appends = re.findall(r"\$\(\"#box_kqxs_minhngoc\"\)\.append\('(.*?)'\);", content)
        full_html = "".join(appends).replace(r"\'", "'").replace(r'\"', '"').replace(r"\/", "/")

        date_match = re.search(r"(\d{1,2}[-/]\d{1,2}[-/]\d{4})", full_html)
        date_str = date_match.group(1).replace("-", "/") if date_match else datetime.now().strftime("%d/%m/%Y")

        parser = MinhNgocParser()
        parser.feed(full_html)

        prizes = parser.prizes
        db = parser.db or (prizes[0] if prizes else "")

        special_last2 = db[-2:] if len(db) >= 2 else ""
        special_last3 = db[-3:] if len(db) >= 3 else ""

        all_last2 = [p[-2:] for p in prizes if len(p) >= 2]
        is_complete = len(prizes) >= 27

        return {
            "success": True,
            "date": date_str,
            "special_prize": db,
            "special_last2": special_last2,
            "special_last3": special_last3,
            "prizes": prizes,
            "all_last2": all_last2,
            "is_complete": is_complete
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "date": datetime.now().strftime("%d/%m/%Y"),
            "special_prize": "",
            "special_last2": "",
            "special_last3": "",
            "prizes": [],
            "all_last2": [],
            "is_complete": False
        }


def format_xsmb_message(kq: dict) -> str:
    if not kq.get("success"):
        return f"⚠️ <b>Chưa lấy được KQXS:</b> {kq.get('error', 'Lỗi không xác định')}"

    db = kq.get("special_prize", "Đang cập nhật...")
    de = kq.get("special_last2", "--")
    c3 = kq.get("special_last3", "---")
    date_str = kq.get("date", datetime.now().strftime("%d/%m"))
    count = len(kq.get("prizes", []))

    status = "✅ Đủ 27 giải" if kq.get("is_complete") else f"⏳ Đang quay ({count}/27 giải)"

    lines = [
        f"🎯 <b>KẾT QUẢ XỔ SỐ MIỀN BẮC - {date_str}</b>",
        f"<i>Trạng thái: {status}</i>",
        "━━━━━━━━━━━━━━━━━━",
        f"👑 <b>Giải Đặc Biệt:</b> <code>{db}</code>",
        f"🔴 <b>ĐỀ (2 số cuối):</b> <b>{de}</b>",
        f"🟣 <b>3 CÀNG:</b> <b>{c3}</b>",
        "━━━━━━━━━━━━━━━━━━",
        "🔵 <b>Lô 2 số (27 giải):</b>"
    ]

    all_lotto = kq.get("all_last2", [])
    chunks = [all_lotto[i:i + 5] for i in range(0, len(all_lotto), 5)]
    for chunk in chunks:
        lines.append("  " + " - ".join(chunk))

    return "\n".join(lines)


def calculate_board_accounting(balancer, kqxs: dict, price_config: dict = None) -> dict:
    cfg = price_config or DEFAULT_PRICE_CONFIG

    rate_de_comm = float(cfg.get("rateDeComm", 82.0)) / 100.0
    rate_de_payout = float(cfg.get("rateDePayout", 80.0))
    rate_lo_cost = float(cfg.get("rateLoCost", 21.65))
    rate_lo_payout = float(cfg.get("rateLoPayout", 80.0))
    rate_xien_comm = float(cfg.get("rateXienComm", 65.0)) / 100.0
    rate_x2_payout = float(cfg.get("rateXien2Payout", 11.0))
    rate_x3_payout = float(cfg.get("rateXien3Payout", 45.0))
    rate_x4_payout = float(cfg.get("rateXien4Payout", 140.0))
    rate_3c_comm = float(cfg.get("rate3CComm", 75.0)) / 100.0
    rate_3c_payout = float(cfg.get("rate3CPayout", 400.0))
    rate_3c_apma = float(cfg.get("rate3CApMa", 5.0))

    special_last2 = kqxs.get("special_last2", "")
    special_last3 = kqxs.get("special_last3", "")
    all_last2 = kqxs.get("all_last2", [])

    de_sums = balancer.de_sums
    lo_sums = balancer.lo_sums
    bacang_sums = balancer.bacang_sums
    xien_bets = balancer.xien_bets

    thau_de_xac = sum(de_sums.values())
    thau_de_von = thau_de_xac * rate_de_comm
    thau_de_win_xac = de_sums.get(special_last2, 0) if special_last2 else 0
    thau_de_trung = thau_de_win_xac * rate_de_payout
    thau_de_net = thau_de_von - thau_de_trung

    thau_lo_xac = sum(lo_sums.values())
    thau_lo_von = thau_lo_xac * rate_lo_cost
    thau_lo_win_xac = 0
    if all_last2:
        for num, amt in lo_sums.items():
            hits = all_last2.count(num)
            if hits > 0:
                thau_lo_win_xac += hits * amt
    thau_lo_trung = thau_lo_win_xac * rate_lo_payout
    thau_lo_net = thau_lo_von - thau_lo_trung

    thau_3c_xac = sum(bacang_sums.values())
    thau_3c_von = thau_3c_xac * rate_3c_comm
    thau_3c_win_xac = 0
    thau_3c_trung = 0
    if special_last3:
        for num, amt in bacang_sums.items():
            if num == special_last3:
                thau_3c_win_xac += amt
                thau_3c_trung += amt * rate_3c_payout
            elif special_last2 and num[-2:] == special_last2:
                thau_3c_win_xac += amt
                thau_3c_trung += amt * rate_3c_apma
    thau_3c_net = thau_3c_von - thau_3c_trung

    thau_xien_xac = sum(b.get("amount", 0) for b in xien_bets)
    thau_xien_von = thau_xien_xac * rate_xien_comm
    thau_xien_win_xac = 0
    thau_xien_trung = 0
    if all_last2:
        for b in xien_bets:
            nums = b.get("numbers", [])
            amt = b.get("amount", 0)
            if all(n in all_last2 for n in nums):
                thau_xien_win_xac += amt
                size = len(nums)
                win_rate = rate_x2_payout if size == 2 else (rate_x3_payout if size == 3 else rate_x4_payout)
                thau_xien_trung += amt * win_rate
    thau_xien_net = thau_xien_von - thau_xien_trung

    thau_total_von = thau_de_von + thau_lo_von + thau_3c_von + thau_xien_von
    thau_total_trung = thau_de_trung + thau_lo_trung + thau_3c_trung + thau_xien_trung
    thau_total_net = thau_total_von - thau_total_trung

    thau = {
        "deXac": thau_de_xac, "deVon": thau_de_von, "deWinXac": thau_de_win_xac, "deTrung": thau_de_trung, "deNet": thau_de_net,
        "loXac": thau_lo_xac, "loVon": thau_lo_von, "loWinXac": thau_lo_win_xac, "loTrung": thau_lo_trung, "loNet": thau_lo_net,
        "baCangXac": thau_3c_xac, "baCangVon": thau_3c_von, "baCangWinXac": thau_3c_win_xac, "baCangTrung": thau_3c_trung, "baCangNet": thau_3c_net,
        "xienXac": thau_xien_xac, "xienVon": thau_xien_von, "xienWinXac": thau_xien_win_xac, "xienTrung": thau_xien_trung, "xienNet": thau_xien_net,
        "totalVon": thau_total_von, "totalTrung": thau_total_trung, "totalNet": thau_total_net
    }

    chuyen_de = balancer.cumulative_de_transfers
    chuyen_lo = balancer.cumulative_lo_transfers
    chuyen_3c = balancer.cumulative_bacang_transfers
    chuyen_xien = balancer.cumulative_xien_transfers

    chuyen_de_xac = sum(chuyen_de.values())
    chuyen_de_von = chuyen_de_xac * rate_de_comm
    chuyen_de_win_xac = chuyen_de.get(special_last2, 0) if special_last2 else 0
    chuyen_de_trung = chuyen_de_win_xac * rate_de_payout
    chuyen_de_net = chuyen_de_von - chuyen_de_trung

    chuyen_lo_xac = sum(chuyen_lo.values())
    chuyen_lo_von = chuyen_lo_xac * rate_lo_cost
    chuyen_lo_win_xac = 0
    if all_last2:
        for num, amt in chuyen_lo.items():
            hits = all_last2.count(num)
            if hits > 0:
                chuyen_lo_win_xac += hits * amt
    chuyen_lo_trung = chuyen_lo_win_xac * rate_lo_payout
    chuyen_lo_net = chuyen_lo_von - chuyen_lo_trung

    chuyen_3c_xac = sum(chuyen_3c.values())
    chuyen_3c_von = chuyen_3c_xac * rate_3c_comm
    chuyen_3c_win_xac = 0
    chuyen_3c_trung = 0
    if special_last3:
        for num, amt in chuyen_3c.items():
            if num == special_last3:
                chuyen_3c_win_xac += amt
                chuyen_3c_trung += amt * rate_3c_payout
            elif special_last2 and num[-2:] == special_last2:
                chuyen_3c_win_xac += amt
                chuyen_3c_trung += amt * rate_3c_apma
    chuyen_3c_net = chuyen_3c_von - chuyen_3c_trung

    chuyen_xien_xac = 0
    chuyen_xien_von = 0
    chuyen_xien_win_xac = 0
    chuyen_xien_trung = 0
    for idx_str, amt in chuyen_xien.items():
        try:
            idx = int(idx_str)
            bet = xien_bets[idx]
            nums = bet.get("numbers", [])
            chuyen_xien_xac += amt
            chuyen_xien_von += amt * rate_xien_comm
            if all_last2 and all(n in all_last2 for n in nums):
                chuyen_xien_win_xac += amt
                size = len(nums)
                win_rate = rate_x2_payout if size == 2 else (rate_x3_payout if size == 3 else rate_x4_payout)
                chuyen_xien_trung += amt * win_rate
        except Exception:
            pass
    chuyen_xien_net = chuyen_xien_von - chuyen_xien_trung

    chuyen_total_von = chuyen_de_von + chuyen_lo_von + chuyen_3c_von + chuyen_xien_von
    chuyen_total_trung = chuyen_de_trung + chuyen_lo_trung + chuyen_3c_trung + chuyen_xien_trung
    chuyen_total_net = chuyen_total_von - chuyen_total_trung

    chuyen = {
        "deXac": chuyen_de_xac, "deVon": chuyen_de_von, "deWinXac": chuyen_de_win_xac, "deTrung": chuyen_de_trung, "deNet": chuyen_de_net,
        "loXac": chuyen_lo_xac, "loVon": chuyen_lo_von, "loWinXac": chuyen_lo_win_xac, "loTrung": chuyen_lo_trung, "loNet": chuyen_lo_net,
        "baCangXac": chuyen_3c_xac, "baCangVon": chuyen_3c_von, "baCangWinXac": chuyen_3c_win_xac, "baCangTrung": chuyen_3c_trung, "baCangNet": chuyen_3c_net,
        "xienXac": chuyen_xien_xac, "xienVon": chuyen_xien_von, "xienWinXac": chuyen_xien_win_xac, "xienTrung": chuyen_xien_trung, "xienNet": chuyen_xien_net,
        "totalVon": chuyen_total_von, "totalTrung": chuyen_total_trung, "totalNet": chuyen_total_net
    }

    giulai = {
        "deXac": max(0.0, thau["deXac"] - chuyen["deXac"]),
        "deVon": thau["deVon"] - chuyen["deVon"],
        "deWinXac": max(0.0, thau["deWinXac"] - chuyen["deWinXac"]),
        "deTrung": thau["deTrung"] - chuyen["deTrung"],
        "deNet": thau["deNet"] - chuyen["deNet"],

        "loXac": max(0.0, thau["loXac"] - chuyen["loXac"]),
        "loVon": thau["loVon"] - chuyen["loVon"],
        "loWinXac": max(0.0, thau["loWinXac"] - chuyen["loWinXac"]),
        "loTrung": thau["loTrung"] - chuyen["loTrung"],
        "loNet": thau["loNet"] - chuyen["loNet"],

        "baCangXac": max(0.0, thau["baCangXac"] - chuyen["baCangXac"]),
        "baCangVon": thau["baCangVon"] - chuyen["baCangVon"],
        "baCangWinXac": max(0.0, thau["baCangWinXac"] - chuyen["baCangWinXac"]),
        "baCangTrung": thau["baCangTrung"] - chuyen["baCangTrung"],
        "baCangNet": thau["baCangNet"] - chuyen["baCangNet"],

        "xienXac": max(0.0, thau["xienXac"] - chuyen["xienXac"]),
        "xienVon": thau["xienVon"] - chuyen["xienVon"],
        "xienWinXac": max(0.0, thau["xienWinXac"] - chuyen["xienWinXac"]),
        "xienTrung": thau["xienTrung"] - chuyen["xienTrung"],
        "xienNet": thau["xienNet"] - chuyen["xienNet"],

        "totalVon": thau["totalVon"] - chuyen["totalVon"],
        "totalTrung": thau["totalTrung"] - chuyen["totalTrung"],
        "totalNet": thau["totalNet"] - chuyen["totalNet"]
    }

    return {
        "thau": thau,
        "chuyen": chuyen,
        "giulai": giulai,
        "date": kqxs.get("date", datetime.now().strftime("%d/%m"))
    }


def format_accounting_report(report_data: dict, tab: str = "thau") -> str:
    data = report_data.get(tab, {})
    raw_date = report_data.get("date", "")
    day_month_text = ""
    if "/" in raw_date:
        parts = raw_date.split("/")
        day_month_text = f"{int(parts[0])}/{int(parts[1])}"
    elif "-" in raw_date:
        parts = raw_date.split("-")
        day_month_text = f"{int(parts[2])}/{int(parts[1])}"
    else:
        day_month_text = datetime.now().strftime("%d/%m")

    header_title = "Thầu"
    if tab == "chuyen":
        header_title = "Chuyển"
    elif tab == "giulai":
        header_title = "Giữ lại"

    lines = [f"{header_title} - {day_month_text}"]

    def fmt_num(val):
        return f"{round(val):,}".replace(",", ".")

    if data.get("deXac", 0) > 0:
        lines.append(f"Đề: {fmt_num(data['deXac'])} / {fmt_num(data.get('deWinXac', 0))}")

    if data.get("loXac", 0) > 0:
        lines.append(f"Lô: {fmt_num(data['loXac'])} / {fmt_num(data.get('loWinXac', 0))}")

    if data.get("xienXac", 0) > 0:
        lines.append(f"Xiên: {fmt_num(data['xienXac'])} / {fmt_num(data.get('xienWinXac', 0))}")

    if data.get("baCangXac", 0) > 0:
        lines.append(f"3c: {fmt_num(data['baCangXac'])} / {fmt_num(data.get('baCangWinXac', 0))}")

    net_val = data.get("totalNet", 0)
    net_val_abs = fmt_num(abs(net_val))

    if tab == "thau":
        if net_val > 0:
            net_label = "Khách thua"
        elif net_val < 0:
            net_label = "Thầu bù"
        else:
            net_label = "Hòa"
    elif tab == "chuyen":
        if net_val > 0:
            net_label = "Nộp"
        elif net_val < 0:
            net_label = "Lấy về"
        else:
            net_label = "Hòa"
    else:
        if net_val > 0:
            net_label = "Lời"
        elif net_val < 0:
            net_label = "Lỗ"
        else:
            net_label = "Hòa"

    lines.append(f"{net_label}: {net_val_abs}")
    return "\n".join(lines)
