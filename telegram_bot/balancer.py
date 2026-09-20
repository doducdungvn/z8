import math
from datetime import datetime

class BoardBalancer:
    def __init__(self, config=None):
        self.config = config or self.default_config()
        self.reset_board()

    @staticmethod
    def default_config():
        return {
            "retain_type": "money",  # "money" hoặc "percentage"
            "retain_de": 20.0,
            "retain_lo": 5.0,
            "retain_3c": 0.0,
            "retain_x": 0.0,
            "retain_use_branch": False,
            "branch_de": 0.0,
            "branch_lo": 0.0,
            "branch_3c": 0.0,
            "branch_x": 0.0,
            "excluded_bets": {}  # Danh sách số loại trừ
        }

    def reset_board(self):
        """Khởi tạo hoặc làm mới bảng cược trong ngày"""
        self.de_sums = {}      # '12': 150.0
        self.lo_sums = {}      # '01': 20.0
        self.bacang_sums = {}  # '123': 50.0
        self.xien_bets = []    # [{'numbers': ['12', '34'], 'amount': 100.0}, ...]

        # Đã chuyển lũy kế
        self.cumulative_de_transfers = {}
        self.cumulative_lo_transfers = {}
        self.cumulative_bacang_transfers = {}
        self.cumulative_xien_transfers = {}  # index -> amount

        # Lịch sử các bước chuyển
        self.transfer_history = []
        self.step_count = 0

    def add_bets(self, parsed_bets: dict) -> dict:
        """
        Cộng dồn các cược từ tin nhắn của khách vào bảng cược hiện tại
        """
        for item in parsed_bets.get('de', []):
            num = item['number']
            amt = item['amount']
            self.de_sums[num] = self.de_sums.get(num, 0.0) + amt

        for item in parsed_bets.get('lo', []):
            num = item['number']
            amt = item['amount']
            self.lo_sums[num] = self.lo_sums.get(num, 0.0) + amt

        for item in parsed_bets.get('bacang', []):
            num = item['number']
            amt = item['amount']
            self.bacang_sums[num] = self.bacang_sums.get(num, 0.0) + amt

        for cat in ['xien2', 'xien3', 'xien4']:
            for item in parsed_bets.get(cat, []):
                self.xien_bets.append(item)

        return self.calculate_excess()

    def _get_category_retain_type(self, cat: str) -> str:
        """Xác định loại tính giữ lại (tiền hoặc %) cho từng thể loại theo cấu hình"""
        r_type = self.config.get("retain_type", "money")
        if r_type == "money":
            return "money"
        elif r_type == "percentage":
            return "percentage"
        elif r_type == "de_money_lo_percent":
            return "percentage" if cat == "lo" else "money"
        elif r_type == "de_percent_lo_money":
            return "money" if cat == "lo" else "percentage"
        return "money"

    def _calc_excess_for_item(self, total: float, prev_transfer: float, limit: float, branch_limit: float, is_excluded: bool = False, cat: str = "de") -> float:
        if is_excluded:
            excess = total - prev_transfer
            return max(0.0, math.ceil(excess))

        retain_type = self._get_category_retain_type(cat)
        use_branch = self.config.get("retain_use_branch", False)

        if retain_type == "percentage":
            if use_branch and branch_limit > 0:
                target_retain = min(total * (limit / 100.0), branch_limit)
                target_transfer = total - target_retain
            else:
                target_transfer = total * ((100.0 - limit) / 100.0)
            excess = target_transfer - prev_transfer
            return max(0.0, math.ceil(excess))
        else:
            # money
            if use_branch and branch_limit > 0:
                if total >= branch_limit:
                    target_transfer = total - limit
                else:
                    target_transfer = total
            else:
                target_transfer = total - limit
            excess = target_transfer - prev_transfer
            return max(0.0, math.ceil(excess))

    def calculate_excess(self) -> dict:
        """
        Tính toán phần cược mới vượt mức giữ lại (cần bắn đi ngay)
        Đồng bộ 100% với processCategory trong index.html
        """
        cfg = self.config
        new_transfers = {
            'de': {},
            'lo': {},
            'bacang': {},
            'xien': {}
        }

        # 1. Đề
        for num, val in self.de_sums.items():
            already = self.cumulative_de_transfers.get(num, 0.0)
            pending = self._calc_excess_for_item(
                val,
                already,
                cfg.get('retain_de', 20.0),
                cfg.get('branch_de', 0.0),
                cat='de'
            )
            if pending >= 0.1:
                new_transfers['de'][num] = pending

        # 2. Lô
        for num, val in self.lo_sums.items():
            already = self.cumulative_lo_transfers.get(num, 0.0)
            pending = self._calc_excess_for_item(
                val,
                already,
                cfg.get('retain_lo', 5.0),
                cfg.get('branch_lo', 0.0),
                cat='lo'
            )
            if pending >= 0.1:
                new_transfers['lo'][num] = pending

        # 3. 3 Càng
        for num, val in self.bacang_sums.items():
            already = self.cumulative_bacang_transfers.get(num, 0.0)
            pending = self._calc_excess_for_item(
                val,
                already,
                cfg.get('retain_3c', 0.0),
                cfg.get('branch_3c', 0.0),
                cat='bacang'
            )
            if pending >= 0.1:
                new_transfers['bacang'][num] = pending

        # 4. Xiên
        retain_x = cfg.get('retain_x', 0.0)
        branch_x = cfg.get('branch_x', 0.0)
        for idx, bet in enumerate(self.xien_bets):
            val = bet['amount']
            already = self.cumulative_xien_transfers.get(idx, 0.0)
            pending = self._calc_excess_for_item(
                val,
                already,
                retain_x,
                branch_x,
                cat='xien'
            )
            if pending >= 0.1:
                key_str = "-".join(bet['numbers'])
                new_transfers['xien'][key_str] = pending

        return new_transfers

    def commit_transfers(self, transfers: dict, client_chat_id: str = "", client_msg_idx: int = None, client_history_idx: int = None):
        """
        Đánh dấu đã chuyển đi phần cược thừa (ghi nhận lũy kế) và gắn liên kết với tin cược của khách.
        """
        has_transfer = False
        for num, amt in transfers.get('de', {}).items():
            self.cumulative_de_transfers[num] = self.cumulative_de_transfers.get(num, 0.0) + amt
            has_transfer = True

        for num, amt in transfers.get('lo', {}).items():
            self.cumulative_lo_transfers[num] = self.cumulative_lo_transfers.get(num, 0.0) + amt
            has_transfer = True

        for num, amt in transfers.get('bacang', {}).items():
            self.cumulative_bacang_transfers[num] = self.cumulative_bacang_transfers.get(num, 0.0) + amt
            has_transfer = True

        # Đối với xiên: đánh dấu theo index
        for key_str, amt in transfers.get('xien', {}).items():
            for idx, bet in enumerate(self.xien_bets):
                if "-".join(bet['numbers']) == key_str:
                    self.cumulative_xien_transfers[idx] = self.cumulative_xien_transfers.get(idx, 0.0) + amt
                    has_transfer = True
                    break

        if has_transfer:
            transfer_text = self.format_transfer_message(transfers, include_header=False)
            self.step_count += 1
            self.transfer_history.append({
                "step": self.step_count,
                "timestamp": datetime.now().strftime("%H:%M:%S %d/%m/%Y"),
                "transferred": transfers,
                "transfer_text": transfer_text,
                "voided": False,
                "client_chat_id": str(client_chat_id) if client_chat_id else "",
                "client_msg_idx": client_msg_idx,
                "client_history_idx": client_history_idx
            })

    def void_transfers_for_client_bet(self, client_chat_id: str, client_history_idx: int = None, client_msg_idx: int = None, voided: bool = True) -> int:
        """
        Tự động bật/tắt bỏ qua (void) các lần chuyển thầu liên quan đến tin cược của khách.
        Đảm bảo nguyên tắc: Khách bỏ qua tin nào -> cược chuyển thầu của tin đó tự động hủy theo.
        """
        affected = 0
        cid_str = str(client_chat_id or "").strip()
        for h in self.transfer_history:
            h_cid = str(h.get("client_chat_id", "")).strip()
            h_idx = h.get("client_history_idx")
            m_idx = h.get("client_msg_idx")

            match = False
            if cid_str and h_cid == cid_str:
                if client_history_idx is not None and h_idx == client_history_idx:
                    match = True
                elif client_msg_idx is not None and m_idx == client_msg_idx:
                    match = True

            if match:
                h["voided"] = voided
                affected += 1

        if affected > 0:
            self.recalculate_cumulative_transfers()
        return affected

    def delete_transfers_for_client_bet(self, client_chat_id: str, client_history_idx: int = None, client_msg_idx: int = None) -> int:
        """
        Xóa vĩnh viễn các lần chuyển thầu liên quan đến tin cược của khách bị xóa.
        """
        cid_str = str(client_chat_id or "").strip()
        new_hist = []
        deleted = 0
        for h in self.transfer_history:
            h_cid = str(h.get("client_chat_id", "")).strip()
            h_idx = h.get("client_history_idx")
            m_idx = h.get("client_msg_idx")

            match = False
            if cid_str and h_cid == cid_str:
                if client_history_idx is not None and h_idx == client_history_idx:
                    match = True
                elif client_msg_idx is not None and m_idx == client_msg_idx:
                    match = True

            if match:
                deleted += 1
            else:
                new_hist.append(h)

        if deleted > 0:
            self.transfer_history = new_hist
            self.recalculate_cumulative_transfers()
        return deleted

    def _format_grouped_numbers(self, items: dict) -> str:
        """Gom nhóm các con số có cùng số tiền: ví dụ 12.34x20, 56x50"""
        groups = {}
        for num, amt in items.items():
            amt_rounded = int(amt) if isinstance(amt, (int, float)) and float(amt).is_integer() else amt
            groups.setdefault(amt_rounded, []).append(str(num))
        sorted_amts = sorted(groups.keys(), key=lambda x: float(x))
        group_strings = []
        for a in sorted_amts:
            nums = sorted(groups[a], key=lambda x: int(x) if x.isdigit() else x)
            group_strings.append(f"{'.'.join(nums)}x{a}")
        return ", ".join(group_strings)

    def calculate_retained_from_single_bet(self, parsed: dict, excess: dict) -> str:
        """
        Tính toán và định dạng chuỗi các con số giữ lại để ôm từ 1 tin cược của khách (sau khi trừ cược thừa).
        """
        excess_de = excess.get('de', {}) if excess else {}
        excess_lo = excess.get('lo', {}) if excess else {}
        excess_bc = excess.get('bacang', {}) if excess else {}
        excess_xien = excess.get('xien', {}) if excess else {}

        lines = []

        # Đề
        de_items = {}
        for b in parsed.get('de', []):
            num = str(b['number']).zfill(2)
            amt = float(b['amount'])
            ex_amt = float(excess_de.get(num, 0.0))
            held = amt - ex_amt
            if held > 0:
                de_items[num] = de_items.get(num, 0.0) + held
        if de_items:
            lines.append(f"Đề {self._format_grouped_numbers(de_items)}")

        # Lô
        lo_items = {}
        for b in parsed.get('lo', []):
            num = str(b['number']).zfill(2)
            amt = float(b['amount'])
            ex_amt = float(excess_lo.get(num, 0.0))
            held = amt - ex_amt
            if held > 0:
                lo_items[num] = lo_items.get(num, 0.0) + held
        if lo_items:
            lines.append(f"Lô {self._format_grouped_numbers(lo_items)}")

        # 3C
        bc_items = {}
        for b in parsed.get('bacang', []):
            num = str(b['number']).zfill(3)
            amt = float(b['amount'])
            ex_amt = float(excess_bc.get(num, 0.0))
            held = amt - ex_amt
            if held > 0:
                bc_items[num] = bc_items.get(num, 0.0) + held
        if bc_items:
            lines.append(f"3c {self._format_grouped_numbers(bc_items)}")

        # Xiên
        all_xien = parsed.get('xien2', []) + parsed.get('xien3', []) + parsed.get('xien4', [])
        xien_items = {}
        for b in all_xien:
            key_str = "-".join(str(x).zfill(2) for x in b.get('numbers', []))
            amt = float(b.get('amount', 0))
            ex_amt = float(excess_xien.get(key_str, 0.0))
            held = amt - ex_amt
            if held > 0:
                xien_items[key_str] = xien_items.get(key_str, 0.0) + held
        if xien_items:
            lines.append(f"Xiên {', '.join(f'{k}x{int(v) if v.is_integer() else v}' for k, v in xien_items.items())}")

        return "\n".join(lines) if lines else "Không ôm (Chuyển 100%)"

    def recalculate_cumulative_transfers(self):
        """
        Tính toán lại các giá trị cược đã chuyển lũy kế dựa trên các lần chuyển CHƯA BỊ BỎ QUA.
        """
        self.cumulative_de_transfers = {}
        self.cumulative_lo_transfers = {}
        self.cumulative_bacang_transfers = {}
        self.cumulative_xien_transfers = {}

        for h in self.transfer_history:
            if h.get("voided", False):
                continue
            transfers = h.get("transferred", {})
            for num, amt in transfers.get('de', {}).items():
                self.cumulative_de_transfers[num] = self.cumulative_de_transfers.get(num, 0.0) + amt
            for num, amt in transfers.get('lo', {}).items():
                self.cumulative_lo_transfers[num] = self.cumulative_lo_transfers.get(num, 0.0) + amt
            for num, amt in transfers.get('bacang', {}).items():
                self.cumulative_bacang_transfers[num] = self.cumulative_bacang_transfers.get(num, 0.0) + amt
            for key_str, amt in transfers.get('xien', {}).items():
                for idx, bet in enumerate(self.xien_bets):
                    if "-".join(bet.get('numbers', [])) == key_str:
                        self.cumulative_xien_transfers[idx] = self.cumulative_xien_transfers.get(idx, 0.0) + amt
                        break

    def toggle_transfer_void(self, step_idx: int) -> bool:
        """
        Bật/tắt trạng thái bỏ qua (voided) của một lần chuyển cược cho chủ thầu.
        Khi bỏ qua: không tính các con trong lần chuyển này vào nợ thầu và hoàn trả lại bảng giữ lại.
        """
        if 0 <= step_idx < len(self.transfer_history):
            h = self.transfer_history[step_idx]
            h["voided"] = not h.get("voided", False)
            self.recalculate_cumulative_transfers()
            return h["voided"]
        return False

    def get_retained_bets(self) -> dict:
        """
        Lấy chi tiết các cược đang thực giữ lại (tổng nhận trừ tổng chuyển)
        """
        retained_de = {}
        for num, total in self.de_sums.items():
            transferred = self.cumulative_de_transfers.get(num, 0.0)
            held = total - transferred
            if held > 0:
                retained_de[num] = held

        retained_lo = {}
        for num, total in self.lo_sums.items():
            transferred = self.cumulative_lo_transfers.get(num, 0.0)
            held = total - transferred
            if held > 0:
                retained_lo[num] = held

        retained_3c = {}
        for num, total in self.bacang_sums.items():
            transferred = self.cumulative_bacang_transfers.get(num, 0.0)
            held = total - transferred
            if held > 0:
                retained_3c[num] = held

        retained_xien = []
        for idx, bet in enumerate(self.xien_bets):
            transferred = self.cumulative_xien_transfers.get(idx, 0.0)
            held = bet['amount'] - transferred
            if held > 0:
                retained_xien.append({
                    "numbers": bet['numbers'],
                    "amount": held
                })

        return {
            "de": retained_de,
            "lo": retained_lo,
            "bacang": retained_3c,
            "xien": retained_xien,
            "summary": {
                "de_count": len(retained_de),
                "de_sum": sum(retained_de.values()),
                "lo_count": len(retained_lo),
                "lo_sum": sum(retained_lo.values()),
                "bacang_count": len(retained_3c),
                "bacang_sum": sum(retained_3c.values()),
                "xien_count": len(retained_xien),
                "xien_sum": sum(x['amount'] for x in retained_xien)
            }
        }


    def format_transfer_message(self, transfers: dict, header_prefix: str = "", include_header: bool = False) -> str:
        """
        Định dạng tin nhắn cược chuyển đi để gửi Telegram:
        Chỉ bao gồm các loại hình thừa chuẩn định dạng cược, không kèm 'chuyển 1,2,3... ngày ...':
        Đề 88x135, 77x130, 16.61.74x50
        Lô 01.46.64x10
        3c 123.456x20
        Xiên 12-34x50
        """
        lines = []

        categories = [
            ('de', 'Đề'),
            ('lo', 'Lô'),
            ('bacang', '3c'),
            ('xien', 'Xiên')
        ]

        total_con = 0
        total_val = 0.0

        for cat_key, cat_name in categories:
            items = transfers.get(cat_key, {})
            if not items:
                continue

            # Gom nhóm theo đơn giá cược (amount)
            groups = {}
            for num, amt in items.items():
                amt_rounded = int(amt) if amt.is_integer() else amt
                groups.setdefault(amt_rounded, []).append(num)
                total_con += 1
                total_val += amt

            # Sắp xếp giá tăng dần
            sorted_amts = sorted(groups.keys())
            group_strings = []
            for a in sorted_amts:
                nums = groups[a]
                if cat_key == 'xien':
                    # Mỗi cặp xiên phải có 'x' tiền cược riêng (ví dụ: 10-01x20, 10-52x20)
                    for n in nums:
                        group_strings.append(f"{n}x{a}")
                else:
                    nums.sort(key=lambda x: int(x) if x.isdigit() else x)
                    group_strings.append(f"{'.'.join(nums)}x{a}")

            lines.append(f"{cat_name} {', '.join(group_strings)}")

        if not lines:
            return ""

        if include_header:
            today_str = datetime.now().strftime("%d/%m")
            step_num = self.step_count + 1
            prefix = "% " if self.config.get("retain_type") == "percentage" else ""
            header = f"🛸 {prefix}Chuyển {step_num} ({today_str}):"
            return f"{header}\n" + "\n".join(lines)

        return "\n".join(lines)
