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


def parse_rate_item(cfg_dict: dict) -> dict:
    """Chuẩn hóa giá trị tỷ lệ giá (hỗ trợ cả dạng thô từ Web như 2165đ lẫn 21.65k)"""
    c = cfg_dict or {}
    lo_cost = float(c.get("rateLoCost", 21.65))
    if lo_cost > 100:  # Ví dụ web nhập 2165đ -> đổi ra 21.65k
        lo_cost = lo_cost / 100.0
    return {
        "de_comm": float(c.get("rateDeComm", 82.0)) / 100.0,
        "de_payout": float(c.get("rateDePayout", 80.0)),
        "lo_cost": lo_cost,
        "lo_payout": float(c.get("rateLoPayout", 80.0)),
        "xien_comm": float(c.get("rateXienComm", 65.0)) / 100.0,
        "x2_payout": float(c.get("rateXien2Payout", 11.0)),
        "x3_payout": float(c.get("rateXien3Payout", 45.0)),
        "x4_payout": float(c.get("rateXien4Payout", 140.0)),
        "c3_comm": float(c.get("rate3CComm", 75.0)) / 100.0,
        "c3_payout": float(c.get("rate3CPayout", 400.0)),
        "c3_apma": float(c.get("rate3CApMa", 5.0)),
    }


def format_price_config_summary(price_config: dict = None) -> str:
    """Tạo bảng báo cáo hiển thị cấu hình giá thầu & giá chuyển"""
    cfg = price_config or DEFAULT_PRICE_CONFIG
    thau_raw = cfg.get("thau") if isinstance(cfg.get("thau"), dict) else cfg
    chuyen_raw = cfg.get("chuyen") if isinstance(cfg.get("chuyen"), dict) else cfg

    t = parse_rate_item(thau_raw)
    c = parse_rate_item(chuyen_raw)

    lines = [
        "⚙️ <b>CẤU HÌNH BẢNG GIÁ & HOA HỒNG:</b>",
        "━━━━━━━━━━━━━━━━━━",
        "📊 <b>1. BẢNG THẦU (Nhận của khách):</b>",
        f"• Đề: Giá xác <b>{t['de_comm']*100:.1f}%</b>, Trúng 1 ăn <b>{t['de_payout']:g}</b>",
        f"• Lô: Vốn <b>{t['lo_cost']:g}k/đ</b> ({t['lo_cost']*1000:g}đ), Thưởng <b>{t['lo_payout']:g}k/đ</b>",
        f"• 3 Càng: Giá xác <b>{t['c3_comm']*100:.1f}%</b>, Trúng 1 ăn <b>{t['c3_payout']:g}</b>",
        f"• Xiên: Giá xác <b>{t['xien_comm']*100:.1f}%</b> (X2: 1 ăn {t['x2_payout']:g}, X3: 1 ăn {t['x3_payout']:g}, X4: 1 ăn {t['x4_payout']:g})",
        "",
        "🔄 <b>2. BẢNG CHUYỂN (Bắn thầu trên):</b>",
        f"• Đề: Giá xác <b>{c['de_comm']*100:.1f}%</b>, Trúng 1 ăn <b>{c['de_payout']:g}</b>",
        f"• Lô: Vốn <b>{c['lo_cost']:g}k/đ</b> ({c['lo_cost']*1000:g}đ), Thưởng <b>{c['lo_payout']:g}k/đ</b>",
        f"• 3 Càng: Giá xác <b>{c['c3_comm']*100:.1f}%</b>, Trúng 1 ăn <b>{c['c3_payout']:g}</b>",
        f"• Xiên: Giá xác <b>{c['xien_comm']*100:.1f}%</b> (X2: 1 ăn {c['x2_payout']:g}, X3: 1 ăn {c['x3_payout']:g}, X4: 1 ăn {c['x4_payout']:g})",
        "━━━━━━━━━━━━━━━━━━",
        "📝 <b>ĐỂ SỬA BẢNG GIÁ TRÊN TELEGRAM:</b>",
        "• <b>Giá Thầu:</b> <code>/giathau &lt;món&gt; &lt;giá&gt;</code>",
        "  - Đề: <code>/giathau de 82 80</code> <i>(Xác 82%, trúng 80)</i>",
        "  - Lô: <code>/giathau lo 21.65 80</code> <i>(Vốn 21.65k, trúng 80)</i>",
        "  - 3C: <code>/giathau 3c 75 400</code> <i>(Xác 75%, trúng 400)</i>",
        "  - Xiên: <code>/giathau xien 65</code> <i>(Xác 65%)</i>",
        "• <b>Giá Chuyển:</b> <code>/giachuyen &lt;món&gt; &lt;giá&gt;</code>",
        "  - Đề: <code>/giachuyen de 80 80</code>",
        "  - Lô: <code>/giachuyen lo 21.6 80</code>",
        "  - 3C: <code>/giachuyen 3c 70 400</code>",
        "  - Xiên: <code>/giachuyen xien 62</code>"
    ]
    return "\n".join(lines)


def format_retain_config_summary(config: dict) -> str:
    """Tạo bảng báo cáo hiển thị cấu hình Cân chuyển & Mức giữ lại"""
    r = config.get("retain_config", {})
    r_type = r.get("retain_type", "percentage")
    is_pct = (r_type == "percentage")

    de_val = r.get("retain_de", 0)
    lo_val = r.get("retain_lo", 0)
    c3_val = r.get("retain_3c", 0)
    x_val = r.get("retain_x", 0)

    unit_de = "%" if is_pct else "k"
    unit_lo = "%" if is_pct else "đ"
    unit_3c = "%" if is_pct else "k"
    unit_x = "%" if is_pct else "k"

    mode = config.get("mode", "instant")
    mode_str = "Tức thì (cược thừa bắn ngay)" if mode == "instant" else "Gom bảng (chờ lệnh mới bắn)"
    recipient = config.get("target_recipient", "(Chưa cài đặt)")
    clean_h = config.get("cleanup_after_hours", 24)
    auto_reply = "Bật" if config.get("auto_reply_client", True) else "Tắt"
    auto_fwd = "Bật" if config.get("auto_forward_excess", True) else "Tắt"

    use_branch = bool(r.get("retain_use_branch", False))
    b_de = r.get("branch_de", 0)
    b_lo = r.get("branch_lo", 0)
    b_3c = r.get("branch_3c", 0)
    b_x = r.get("branch_x", 0)
    branch_title = "Mức trần Tối Đa (k/đ)" if is_pct else "Mức định mức Nhánh"
    branch_val_str = f"BẬT (Đề: {b_de:g}k, Lô: {b_lo:g}đ, 3C: {b_3c:g}k, X: {b_x:g}k)" if use_branch else "TẮT"

    is_fwd_on = config.get("auto_forward_excess", True)
    fwd_status_str = "🟢 <b>BẬT (Đang hoạt động)</b>" if is_fwd_on else "🔴 <b>TẮT (Đang tạm dừng)</b>"

    lines = [
        "⚙️ <b>CẤU HÌNH CÂN CHUYỂN & MỨC GIỮ LẠI:</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"• <b>Chức năng Cân Chuyển:</b> {fwd_status_str}",
        f"• <b>Hình thức giữ:</b> <code>{'Phần trăm (%)' if is_pct else 'Tiền mặt (k/đ)'}</code>",
        f"• <b>Giữ Đề:</b> <code>{de_val:g}{unit_de}</code>",
        f"• <b>Giữ Lô:</b> <code>{lo_val:g}{unit_lo}</code>",
        f"• <b>Giữ 3 Càng:</b> <code>{c3_val:g}{unit_3c}</code>",
        f"• <b>Giữ Xiên:</b> <code>{x_val:g}{unit_x}</code>",
        f"• <b>{branch_title}:</b> <code>{branch_val_str}</code>",
        f"• <b>Người nhận cược thừa:</b> <code>{recipient}</code>",
        f"• <b>Chế độ chuyển:</b> {mode_str}",
        f"• <b>Tự động xác nhận:</b> {auto_reply} | <b>Tự động bắn:</b> {auto_fwd}",
        f"• <b>Tự động xóa vết:</b> {clean_h:g} giờ",
        "━━━━━━━━━━━━━━━━━━",
        "📝 <b>ĐỂ SỬA THIẾT LẬP CÂN CHUYỂN TRÊN TELEGRAM:</b>",
        "• <b>Bật / Tắt Cân Chuyển:</b>",
        "  <code>/canchuyen bat</code> hoặc <code>/canchuyen tat</code>",
        "  <i>(Hoặc dùng: <code>/chuyen bat</code> / <code>/chuyen tat</code>)</i>",
        "• <b>Đặt theo Tiền:</b>",
        "  <code>/giulai tien &lt;đề&gt; &lt;lô&gt; &lt;3c&gt; &lt;xiên&gt;</code>",
        "  <i>(Ví dụ: <code>/giulai tien 20 5 0 0</code>)</i>",
        "• <b>Đặt theo Phần trăm:</b>",
        "  <code>/giulai % &lt;đề&gt; &lt;lô&gt; &lt;3c&gt; &lt;xiên&gt;</code>",
        "  <i>(Ví dụ: <code>/giulai % 50 50 0 0</code>)</i>",
        "• <b>Cài đặt Mức trần Tối đa (k/đ) khi giữ %:</b>",
        "  <code>/toida &lt;đề&gt; &lt;lô&gt; &lt;3c&gt; &lt;xiên&gt;</code>",
        "  <i>(Ví dụ: <code>/toida 20 5 0 0</code> hoặc <code>/toida tat</code> / <code>/toida bat</code>)</i>",
        "• <b>Sửa từng món riêng lẻ:</b>",
        "  <code>/giulai de 30k</code> hoặc <code>/giulai de 50%</code>",
        "  <code>/giulai lo 10d</code> hoặc <code>/giulai lo 40%</code>",
        "  <code>/giulai 3c 10k</code> hoặc <code>/giulai x 20k</code>",
        "• <b>Đổi người nhận cược thừa:</b>",
        "  <code>/chuyensang &lt;chat_id hoặc @username&gt;</code>",
        "• <b>Đổi chế độ chuyển:</b> <code>/chedo tucthi</code> hoặc <code>/chedo gomban</code>",
        "• <b>Đổi giờ xóa vết cược:</b> <code>/timer &lt;giờ&gt;</code>"
    ]
    return "\n".join(lines)


def calculate_board_accounting(balancer, kqxs: dict, price_config: dict = None) -> dict:
    cfg = price_config or DEFAULT_PRICE_CONFIG

    thau_raw = cfg.get("thau") if isinstance(cfg.get("thau"), dict) else cfg
    chuyen_raw = cfg.get("chuyen") if isinstance(cfg.get("chuyen"), dict) else cfg

    t = parse_rate_item(thau_raw)
    c = parse_rate_item(chuyen_raw)

    special_last2 = kqxs.get("special_last2", "")
    special_last3 = kqxs.get("special_last3", "")
    all_last2 = kqxs.get("all_last2", [])

    de_sums = balancer.de_sums
    lo_sums = balancer.lo_sums
    bacang_sums = balancer.bacang_sums
    xien_bets = balancer.xien_bets

    # 1. BẢNG THẦU (Áp dụng giá Thầu)
    thau_de_xac = sum(de_sums.values())
    thau_de_von = thau_de_xac * t["de_comm"]
    thau_de_win_xac = de_sums.get(special_last2, 0) if special_last2 else 0
    thau_de_trung = thau_de_win_xac * t["de_payout"]
    thau_de_net = thau_de_von - thau_de_trung

    thau_lo_xac = sum(lo_sums.values())
    thau_lo_von = thau_lo_xac * t["lo_cost"]
    thau_lo_win_xac = 0
    if all_last2:
        for num, amt in lo_sums.items():
            hits = all_last2.count(num)
            if hits > 0:
                thau_lo_win_xac += hits * amt
    thau_lo_trung = thau_lo_win_xac * t["lo_payout"]
    thau_lo_net = thau_lo_von - thau_lo_trung

    thau_3c_xac = sum(bacang_sums.values())
    thau_3c_von = thau_3c_xac * t["c3_comm"]
    thau_3c_win_xac = 0
    thau_3c_trung = 0
    if special_last3:
        for num, amt in bacang_sums.items():
            if num == special_last3:
                thau_3c_win_xac += amt
                thau_3c_trung += amt * t["c3_payout"]
            elif special_last2 and num[-2:] == special_last2:
                thau_3c_win_xac += amt
                thau_3c_trung += amt * t["c3_apma"]
    thau_3c_net = thau_3c_von - thau_3c_trung

    thau_xien_xac = sum(b.get("amount", 0) for b in xien_bets)
    thau_xien_von = thau_xien_xac * t["xien_comm"]
    thau_xien_win_xac = 0
    thau_xien_trung = 0
    if all_last2:
        for b in xien_bets:
            nums = b.get("numbers", [])
            amt = b.get("amount", 0)
            if all(n in all_last2 for n in nums):
                thau_xien_win_xac += amt
                size = len(nums)
                win_rate = t["x2_payout"] if size == 2 else (t["x3_payout"] if size == 3 else t["x4_payout"])
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

    # 2. BẢNG CHUYỂN (Áp dụng giá Chuyển)
    chuyen_de = balancer.cumulative_de_transfers
    chuyen_lo = balancer.cumulative_lo_transfers
    chuyen_3c = balancer.cumulative_bacang_transfers
    chuyen_xien = balancer.cumulative_xien_transfers

    chuyen_de_xac = sum(chuyen_de.values())
    chuyen_de_von = chuyen_de_xac * c["de_comm"]
    chuyen_de_win_xac = chuyen_de.get(special_last2, 0) if special_last2 else 0
    chuyen_de_trung = chuyen_de_win_xac * c["de_payout"]
    chuyen_de_net = chuyen_de_von - chuyen_de_trung

    chuyen_lo_xac = sum(chuyen_lo.values())
    chuyen_lo_von = chuyen_lo_xac * c["lo_cost"]
    chuyen_lo_win_xac = 0
    if all_last2:
        for num, amt in chuyen_lo.items():
            hits = all_last2.count(num)
            if hits > 0:
                chuyen_lo_win_xac += hits * amt
    chuyen_lo_trung = chuyen_lo_win_xac * c["lo_payout"]
    chuyen_lo_net = chuyen_lo_von - chuyen_lo_trung

    chuyen_3c_xac = sum(chuyen_3c.values())
    chuyen_3c_von = chuyen_3c_xac * c["c3_comm"]
    chuyen_3c_win_xac = 0
    chuyen_3c_trung = 0
    if special_last3:
        for num, amt in chuyen_3c.items():
            if num == special_last3:
                chuyen_3c_win_xac += amt
                chuyen_3c_trung += amt * c["c3_payout"]
            elif special_last2 and num[-2:] == special_last2:
                chuyen_3c_win_xac += amt
                chuyen_3c_trung += amt * c["c3_apma"]
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
            chuyen_xien_von += amt * c["xien_comm"]
            if all_last2 and all(n in all_last2 for n in nums):
                chuyen_xien_win_xac += amt
                size = len(nums)
                win_rate = c["x2_payout"] if size == 2 else (c["x3_payout"] if size == 3 else c["x4_payout"])
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
