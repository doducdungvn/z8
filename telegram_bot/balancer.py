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

    def _calc_excess_for_item(self, val: float, retain_val: float, branch_limit: float, is_excluded: bool = False) -> float:
        if is_excluded:
            return math.ceil(val)

        retain_type = self.config.get("retain_type", "money")
        use_branch = self.config.get("retain_use_branch", False)

        if retain_type == "money":
            if use_branch:
                if val >= branch_limit:
                    excess = max(0.0, val - retain_val)
                else:
                    excess = val
            else:
                excess = val - retain_val
            return max(0.0, math.ceil(excess))
        else:
            # percentage (%)
            if use_branch:
                target_retain = min(val * (retain_val / 100.0), branch_limit)
                excess = val - target_retain
            else:
                excess = val - (val * (retain_val / 100.0))
            return max(0.0, math.ceil(excess))

    def calculate_excess(self) -> dict:
        """
        Tính toán phần cược mới vượt mức giữ lại (cần bắn đi ngay)
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
            excess = self._calc_excess_for_item(
                val,
                cfg.get('retain_de', 20.0),
                cfg.get('branch_de', 0.0)
            )
            already = self.cumulative_de_transfers.get(num, 0.0)
            pending = max(0.0, excess - already)
            if pending >= 0.1:
                new_transfers['de'][num] = pending

        # 2. Lô
        for num, val in self.lo_sums.items():
            excess = self._calc_excess_for_item(
                val,
                cfg.get('retain_lo', 5.0),
                cfg.get('branch_lo', 0.0)
            )
            already = self.cumulative_lo_transfers.get(num, 0.0)
            pending = max(0.0, excess - already)
            if pending >= 0.1:
                new_transfers['lo'][num] = pending

        # 3. 3 Càng
        for num, val in self.bacang_sums.items():
            excess = self._calc_excess_for_item(
                val,
                cfg.get('retain_3c', 0.0),
                cfg.get('branch_3c', 0.0)
            )
            already = self.cumulative_bacang_transfers.get(num, 0.0)
            pending = max(0.0, excess - already)
            if pending >= 0.1:
                new_transfers['bacang'][num] = pending

        # 4. Xiên
        retain_x = cfg.get('retain_x', 0.0)
        branch_x = cfg.get('branch_x', 0.0)
        for idx, bet in enumerate(self.xien_bets):
            val = bet['amount']
            excess = self._calc_excess_for_item(val, retain_x, branch_x)
            already = self.cumulative_xien_transfers.get(idx, 0.0)
            pending = max(0.0, excess - already)
            if pending >= 0.1:
                key_str = "-".join(bet['numbers'])
                new_transfers['xien'][key_str] = pending

        return new_transfers

    def commit_transfers(self, transfers: dict):
        """
        Đánh dấu đã chuyển đi phần cược thừa (ghi nhận lũy kế)
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
            self.step_count += 1
            self.transfer_history.append({
                "step": self.step_count,
                "timestamp": datetime.now().strftime("%H:%M:%S %d/%m/%Y"),
                "transferred": transfers
            })

    def format_transfer_message(self, transfers: dict, header_prefix: str = "Thầu") -> str:
        """
        Định dạng tin nhắn cược chuyển đi để gửi Telegram theo chuẩn thầu:
        Đề 88x135, 77x130, 16.61.74x50...
        Lô 01.46.64x10
        Xiên 12-34x50
        3C 123.456x20
        """
        today_str = datetime.now().strftime("%d/%m")
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
                    # Mỗi cặp xiên phân cách bằng dấu phẩy
                    group_strings.append(f"{', '.join(nums)}x{a}")
                else:
                    nums.sort(key=lambda x: int(x) if x.isdigit() else x)
                    group_strings.append(f"{'.'.join(nums)}x{a}")

            lines.append(f"{cat_name} {', '.join(group_strings)}")

        if not lines:
            return ""

        header = f"🛸 {header_prefix} - {today_str}"
        return f"{header}\n" + "\n".join(lines)
