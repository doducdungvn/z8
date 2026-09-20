import os
import sys
import json
import time
import threading
import requests
import re
from datetime import datetime, timezone, timedelta

VN_TZ = timezone(timedelta(hours=7))

def now_vn() -> datetime:
    return datetime.now(VN_TZ)

from bet_parser import (
    parse_bet_message,
    format_ok_receipt,
    expand_filter_numbers,
    filter_parsed_bets,
    format_rejected_receipt,
    update_parsed_summary,
    strip_accents
)
from balancer import BoardBalancer
from lottery_engine import (
    DEFAULT_PRICE_CONFIG,
    fetch_xsmb,
    format_xsmb_message,
    calculate_board_accounting,
    calculate_single_client_accounting,
    format_accounting_report,
    format_price_config_summary,
    format_retain_config_summary
)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
LOG_PATH = os.path.join(os.path.dirname(__file__), "bot_activity.log")
KNOWN_USERS_PATH = os.path.join(os.path.dirname(__file__), "known_users.json")
TRACKED_MESSAGES_PATH = os.path.join(os.path.dirname(__file__), "tracked_messages.json")
CLIENT_BETS_PATH = os.path.join(os.path.dirname(__file__), "client_bets.json")
PENDING_BETS_PATH = os.path.join(os.path.dirname(__file__), "pending_bets.json")


class TelegramBotService:
    def __init__(self):
        self.config = self.load_config()
        self.known_users = self.load_known_users()
        self.tracked_messages = self.load_tracked_messages()
        self.client_bets = self.load_client_bets()
        self.pending_bets = self.load_pending_bets()
        self.pending_recipient_acks = None  # Theo dõi phản hồi của người nhận cược thừa (timeout 5p)
        self.pending_client_receipts = []  # Danh sách tin xác nhận khách cược đang chờ chủ thầu Ok
        self.last_cleanup_ts = 0
        self.last_bet_timestamp = None
        self.balancer = BoardBalancer(self.config.get("retain_config", {}))
        self.is_running = False
        self.polling_thread = None
        self.last_update_id = 0
        self.current_date = now_vn().strftime("%Y-%m-%d")
        self.last_daily_report_date = None
        self.is_settled_today = False
        self.cached_kqxs = None
        self.logs = []  # Ring buffer log messages (max 200)
        self.client_msg_counters = {}  # Đếm số thứ tự tin của từng khách trong ngày (Ok tin 1, Ok tin 2...)
        self.stats = {
            "messages_received": 0,
            "transfers_sent": 0,
            "last_active": None,
            "bot_info": None,
            "error_message": None
        }
        if self.config.get("bot_token"):
            try:
                self.check_bot_token()
            except Exception:
                pass

    def load_config(self) -> dict:
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    # Bổ sung các trường mới nếu thiếu
                    if "price_config" not in cfg:
                        cfg["price_config"] = DEFAULT_PRICE_CONFIG
                    if "owner_chat_id" not in cfg:
                        cfg["owner_chat_id"] = ""
                    if "cleanup_after_seconds" not in cfg:
                        cfg["cleanup_after_seconds"] = 86400  # 24 giờ tự động xóa vết cược
                    if "cleanup_after_hours" not in cfg:
                        cfg["cleanup_after_hours"] = 24.0
                    if "admin_password" not in cfg or not cfg.get("admin_password"):
                        cfg["admin_password"] = "123456"
                    if "authenticated_admins" not in cfg:
                        cfg["authenticated_admins"] = []
                    if "forward_contractor_to_owner" not in cfg:
                        cfg["forward_contractor_to_owner"] = False
                    if "forward_client_to_owner" not in cfg:
                        cfg["forward_client_to_owner"] = False
                    if "bet_filter_enabled" not in cfg:
                        cfg["bet_filter_enabled"] = False
                    if "bot_filter_keywords" not in cfg:
                        cfg["bet_filter_keywords"] = ""
                    if "bot_mode" not in cfg:
                        cfg["bot_mode"] = "auto"
                    # Đọc bổ sung từ biến môi trường (nếu có, tiện cho Cloud hosting)
                    if os.environ.get("TELEGRAM_BOT_TOKEN") and not cfg.get("bot_token"):
                        cfg["bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN")
                    if os.environ.get("TARGET_RECIPIENT") and not cfg.get("target_recipient"):
                        cfg["target_recipient"] = os.environ.get("TARGET_RECIPIENT")
                    if os.environ.get("OWNER_CHAT_ID") and not cfg.get("owner_chat_id"):
                        cfg["owner_chat_id"] = os.environ.get("OWNER_CHAT_ID")
                    if os.environ.get("ADMIN_PASSWORD") and not cfg.get("admin_password"):
                        cfg["admin_password"] = os.environ.get("ADMIN_PASSWORD")
                    return cfg
            except Exception as e:
                self.log(f"Lỗi đọc config.json: {e}", "WARN")
        
        # Cấu hình mặc định
        default_cfg = {
            "bot_token": "",
            "bot_mode": "auto",           # "auto" (Bật tự động) hoặc "manual" (Tắt - Treo chờ duyệt)
            "allowed_senders": ["*"],     # Mặc định * để nhận cược thuận tiện
            "target_recipient": "7715286942", # Mặc định trên localhost là @lalalew (7715286942)
            "owner_chat_id": "",          # Chat ID của chủ bảng (nhận báo cáo & lệnh admin)
            "auto_reply_client": True,    # Tự động báo nhận cược cho khách
            "auto_forward_excess": True,  # Tự động bắn cược thừa cho người nhận
            "forward_contractor_to_owner": False, # Chuyển tiếp tin nhắn chủ thầu cho chủ bot
            "forward_client_to_owner": False,     # Chuyển tiếp tin nhắn khách cược cho chủ bot
            "bet_filter_enabled": False,  # Bật/tắt bộ lọc cược cấm nhận
            "bet_filter_keywords": "",    # Từ khóa hoặc số cấm nhận
            "cancel_detail_client": False,      # Kèm diễn giải con số cược khi nhắn hủy cho Khách
            "cancel_detail_contractor": False,  # Kèm diễn giải con số cược khi báo hủy cho Chủ thầu
            "auto_fetch_kqxs_daily": True,# Tự động lấy KQXS lúc 18h30
            "mode": "instant",            # "instant" hoặc "batch"
            "retain_config": BoardBalancer.default_config(),
            "price_config": DEFAULT_PRICE_CONFIG,
            "cleanup_after_seconds": 86400,
            "cleanup_after_hours": 24.0,
            "history_retention_hours": 36.0,
            "admin_password": "123456",
            "authenticated_admins": []
        }
        self.save_config(default_cfg)
        return default_cfg

    def is_admin(self, chat_id: int | str, username: str = "") -> bool:
        """Kiểm tra một chat_id hoặc username có quyền quản trị viên hay không"""
        cid = str(chat_id).strip()
        if not cid:
            return False
        u = (username or "").strip().lstrip("@").lower()
        if not u and hasattr(self, "known_users") and cid in self.known_users:
            u = (self.known_users[cid].get("username") or "").strip().lstrip("@").lower()
        owner = str(self.config.get("owner_chat_id", "")).strip()
        if owner:
            owner_clean = owner.lstrip("@").lower()
            if cid == owner or cid == owner_clean or (u and u == owner_clean):
                return True
        admins = [str(x).strip().lstrip("@").lower() for x in self.config.get("authenticated_admins", [])]
        return cid in admins or (u and u in admins)

    def msg_need_auth(self) -> str:
        return (
            "🔒 <b>KHU VỰC QUẢN TRỊ ĐƯỢC BẢO VỆ BẰNG MẬT KHẨU!</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Mục này chứa dữ liệu tài chính & cấu hình của Chủ bảng.\n"
            "👉 Vui lòng nhập mật khẩu để mở khóa:\n"
            "<code>/mk &lt;mật_khẩu&gt;</code>\n"
            "<i>(Tin nhắn mật khẩu sẽ được tự động xóa ngay để bảo mật)</i>"
        )

    def save_config(self, cfg: dict = None):
        if cfg:
            self.config = cfg
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
            if hasattr(self, "balancer"):
                self.balancer.config = self.config.get("retain_config", {})
        except Exception as e:
            self.log(f"Lỗi lưu config.json: {e}", "ERROR")

    def load_known_users(self) -> dict:
        if os.path.exists(KNOWN_USERS_PATH):
            try:
                with open(KNOWN_USERS_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save_known_users(self):
        try:
            with open(KNOWN_USERS_PATH, "w", encoding="utf-8") as f:
                json.dump(self.known_users, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def record_user(self, from_user: dict, chat_id: int):
        if not from_user:
            return
        uid = str(from_user.get("id"))
        username = from_user.get("username", "")
        first_name = from_user.get("first_name", "")
        self.known_users[uid] = {
            "user_id": uid,
            "chat_id": chat_id,
            "username": username,
            "first_name": first_name,
            "updated_at": now_vn().strftime("%H:%M:%S %d/%m")
        }
        self.save_known_users()

    def load_client_bets(self) -> dict:
        if os.path.exists(CLIENT_BETS_PATH):
            try:
                with open(CLIENT_BETS_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save_client_bets(self):
        try:
            with open(CLIENT_BETS_PATH, "w", encoding="utf-8") as f:
                json.dump(self.client_bets, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def load_pending_bets(self) -> list:
        if os.path.exists(PENDING_BETS_PATH):
            try:
                with open(PENDING_BETS_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def save_pending_bets(self):
        try:
            with open(PENDING_BETS_PATH, "w", encoding="utf-8") as f:
                json.dump(self.pending_bets, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def get_next_pending_id(self) -> int:
        if not hasattr(self, "pending_bets") or not self.pending_bets:
            return 1
        return max(int(b.get("id", 0)) for b in self.pending_bets) + 1

    def add_pending_bet(self, chat_id_str: str, sender_label: str, username: str, parsed: dict, raw_text: str = "", user_msg_id: int = None, filter_return_msg: str = "") -> dict:
        pid = self.get_next_pending_id()
        item = {
            "id": pid,
            "chat_id": chat_id_str,
            "sender_label": sender_label,
            "username": username,
            "timestamp": now_vn().strftime("%H:%M:%S %d/%m"),
            "raw_text": raw_text,
            "parsed": json.loads(json.dumps(parsed)),
            "filter_return_msg": filter_return_msg,
            "user_msg_id": user_msg_id,
            "status": "pending",
            "created_at": time.time()
        }
        self.pending_bets.append(item)
        self.save_pending_bets()
        return item

    def record_client_bet(self, chat_id_str: str, sender_label: str, username: str, parsed: dict, raw_text: str = "", msg_index: int = 1, transfer_text: str = "", retain_text: str = "", user_msg_id: int = None):
        c = self.client_bets.setdefault(chat_id_str, {
            "name": sender_label,
            "username": username,
            "de": {},
            "lo": {},
            "bacang": {},
            "xien": [],
            "history": [],
            "msg_count": 0,
            "last_settled": None
        })
        c["name"] = sender_label
        c["username"] = username
        c["msg_count"] = c.get("msg_count", 0) + 1

        if "history" not in c:
            c["history"] = []

        if raw_text:
            c["history"].append({
                "timestamp": now_vn().strftime("%H:%M:%S %d/%m"),
                "raw_text": raw_text,
                "msg_index": msg_index,
                "summary": parsed.get("summary", {}),
                "invalid_items": parsed.get("invalid_items", []),
                "parsed": parsed,
                "voided": False,
                "transfer_text": transfer_text,  # Nội dung cân chuyển sang thầu (nếu có)
                "retain_text": retain_text,       # Tóm tắt phần giữ lại
                "user_msg_id": user_msg_id
            })


        for b in parsed.get("de", []):
            num = str(b["number"]).zfill(2)
            c["de"][num] = c["de"].get(num, 0.0) + float(b["amount"])

        for b in parsed.get("lo", []):
            num = str(b["number"]).zfill(2)
            c["lo"][num] = c["lo"].get(num, 0.0) + float(b["amount"])

        for b in parsed.get("bacang", []):
            num = str(b["number"]).zfill(3)
            c["bacang"][num] = c["bacang"].get(num, 0.0) + float(b["amount"])

        all_xien = parsed.get("xien2", []) + parsed.get("xien3", []) + parsed.get("xien4", [])
        for b in all_xien:
            c.setdefault("xien", []).append({
                "numbers": [str(x).zfill(2) for x in b["numbers"]],
                "amount": float(b["amount"])
            })

        self.save_client_bets()

    def update_last_bet_transfer(self, chat_id_str: str, transfer_text: str = "", retain_text: str = ""):
        """Cập nhật thông tin cân chuyển/giữ lại vào item lịch sử cuối cùng của khách.
        Được gọi SAU khi đã tính toán excess, để lưu vào Hộp Thư hiển thị cho admin xem."""
        if chat_id_str in self.client_bets:
            hist = self.client_bets[chat_id_str].get("history", [])
            if hist:
                hist[-1]["transfer_text"] = transfer_text
                hist[-1]["retain_text"] = retain_text
                self.save_client_bets()

    def recompute_client_totals(self, chat_id_str: str):
        """Tính toán lại tổng cược tích lũy của 1 khách từ các tin nhắn chưa bị hủy bỏ (voided=False)"""
        if chat_id_str not in self.client_bets:
            return
        c = self.client_bets[chat_id_str]
        c["de"] = {}
        c["lo"] = {}
        c["bacang"] = {}
        c["xien"] = []
        for h in c.get("history", []):
            if h.get("voided", False):
                continue
            p = h.get("parsed")
            if not p and h.get("raw_text"):
                p = parse_bet_message(h.get("raw_text", ""))
                if self.config.get("bet_filter_enabled", False) and self.config.get("bet_filter_keywords", ""):
                    b2d, b3d = expand_filter_numbers(self.config.get("bet_filter_keywords", ""))
                    p, _ = filter_parsed_bets(p, b2d, b3d)
            if p:
                for b in p.get("de", []):
                    num = str(b["number"]).zfill(2)
                    c["de"][num] = c["de"].get(num, 0.0) + float(b["amount"])
                for b in p.get("lo", []):
                    num = str(b["number"]).zfill(2)
                    c["lo"][num] = c["lo"].get(num, 0.0) + float(b["amount"])
                for b in p.get("bacang", []):
                    num = str(b["number"]).zfill(3)
                    c["bacang"][num] = c["bacang"].get(num, 0.0) + float(b["amount"])
                all_xien = p.get("xien2", []) + p.get("xien3", []) + p.get("xien4", [])
                for b in all_xien:
                    c["xien"].append({
                        "numbers": [str(x).zfill(2) for x in b["numbers"]],
                        "amount": float(b["amount"])
                    })
        self.save_client_bets()

    def rebuild_board_from_active_bets(self):
        """Tái cấu trúc lại bảng cược balancer từ tất cả các tin cược chưa bị hủy bỏ"""
        self.balancer.de_sums = {}
        self.balancer.lo_sums = {}
        self.balancer.bacang_sums = {}
        self.balancer.xien_bets = []

        for cid_str, cdata in self.client_bets.items():
            for h in cdata.get("history", []):
                if h.get("voided", False):
                    continue
                p = h.get("parsed")
                if not p and h.get("raw_text"):
                    p = parse_bet_message(h.get("raw_text", ""))
                    if self.config.get("bet_filter_enabled", False) and self.config.get("bet_filter_keywords", ""):
                        b2d, b3d = expand_filter_numbers(self.config.get("bet_filter_keywords", ""))
                        p, _ = filter_parsed_bets(p, b2d, b3d)
                if p:
                    for item in p.get('de', []):
                        self.balancer.de_sums[item['number']] = self.balancer.de_sums.get(item['number'], 0.0) + item['amount']
                    for item in p.get('lo', []):
                        self.balancer.lo_sums[item['number']] = self.balancer.lo_sums.get(item['number'], 0.0) + item['amount']
                    for item in p.get('bacang', []):
                        self.balancer.bacang_sums[item['number']] = self.balancer.bacang_sums.get(item['number'], 0.0) + item['amount']
                    for cat in ['xien2', 'xien3', 'xien4']:
                        for item in p.get(cat, []):
                            self.balancer.xien_bets.append(item)

        self.balancer.recalculate_cumulative_transfers()

    def get_all_raw_messages(self) -> list:
        """Lấy danh sách tất cả tin nhắn gốc đã nhận từ các khách (bao gồm cả tin đang treo chờ duyệt)"""
        all_msgs = []
        for cid_str, cdata in self.client_bets.items():
            name = cdata.get("name") or cid_str
            username = cdata.get("username") or ""
            history = cdata.get("history") or []
            for h_idx, item in enumerate(history):
                all_msgs.append({
                    "chat_id": cid_str,
                    "sender_name": name,
                    "username": username,
                    "timestamp": item.get("timestamp"),
                    "raw_text": item.get("raw_text"),
                    "msg_index": item.get("msg_index"),
                    "history_idx": h_idx,
                    "summary": item.get("summary", {}),
                    "invalid_items": item.get("invalid_items", []),
                    "voided": item.get("voided", False),
                    "transfer_text": item.get("transfer_text", ""),
                    "retained_text": item.get("retained_text") or item.get("retain_text", ""),
                    "is_pending": False,
                    "status": "active"
                })

        for pb in getattr(self, "pending_bets", []):
            if pb.get("status") == "approved":
                continue
            all_msgs.append({
                "chat_id": pb.get("chat_id"),
                "sender_name": pb.get("sender_label") or pb.get("chat_id"),
                "username": pb.get("username", ""),
                "timestamp": pb.get("timestamp"),
                "raw_text": pb.get("raw_text"),
                "msg_index": pb.get("msg_idx", 0),
                "pending_id": pb.get("id"),
                "summary": pb.get("parsed", {}).get("summary", {}),
                "invalid_items": pb.get("parsed", {}).get("invalid_items", []),
                "voided": False,
                "transfer_text": pb.get("transfer_text", ""),
                "retained_text": pb.get("retained_text") or pb.get("retain_text", ""),
                "is_pending": (pb.get("status") == "pending"),
                "status": pb.get("status", "pending")
            })

        return all_msgs

    def toggle_client_message_void(self, chat_id_str: str, history_idx: int) -> dict:
        """Bật/tắt trạng thái bỏ qua tin nhắn cược của khách, đồng thời tự động hủy/khôi phục cược chuyển thầu tương ứng"""
        if chat_id_str in self.client_bets:
            hist = self.client_bets[chat_id_str].get("history", [])
            if 0 <= history_idx < len(hist):
                item = hist[history_idx]
                item["voided"] = not item.get("voided", False)
                is_voided = item["voided"]

                # 1. Tự động hủy đồng bộ các bước chuyển thầu sinh ra từ tin cược này
                m_idx = item.get("msg_index")
                affected_transfers = self.balancer.void_transfers_for_client_bet(
                    client_chat_id=chat_id_str,
                    client_history_idx=history_idx,
                    client_msg_idx=m_idx,
                    voided=is_voided
                )

                # 2. Tính lại tiền cược của khách và nạp lại bảng Balancer
                self.recompute_client_totals(chat_id_str)
                self.rebuild_board_from_active_bets()

                # 3. Đồng bộ lại số thứ tự tin hợp lệ của khách
                # Nếu tin bị bỏ qua, số tin hợp lệ lùi lại để tin tiếp theo khách gửi sẽ nhận đúng số thứ tự
                valid_count = len([h for h in hist if not h.get("voided", False)])
                self.client_msg_counters[chat_id_str] = valid_count

                status_txt = "BỎ QUA (không tính tiền)" if is_voided else "KHÔI PHỤC tính tiền"
                tf_note = f" (Đã tự động hủy {affected_transfers} bước chuyển thầu tương ứng)" if (is_voided and affected_transfers > 0) else ""
                self.log(f"Đã {status_txt} tin #{history_idx + 1} của khách {chat_id_str}{tf_note}. Đã cập nhật lại bảng cược & tiền thầu.", "SUCCESS")
                return {"success": True, "voided": is_voided, "affected_transfers": affected_transfers}
        return {"success": False, "error": "Không tìm thấy tin nhắn"}

    def toggle_transfer_void(self, step_idx: int) -> dict:
        """Bật/tắt trạng thái bỏ qua tin chuyển cho chủ thầu"""
        voided = self.balancer.toggle_transfer_void(step_idx)
        status_txt = "BỎ QUA (không tính thầu)" if voided else "KHÔI PHỤC tính thầu"
        self.log(f"Đã {status_txt} tin chuyển #{step_idx + 1}. Đã cập nhật lại bảng cược & tiền thầu.", "SUCCESS")
        return {"success": True, "voided": voided}

    def cancel_client_bet_by_index(self, chat_id_str: str, sender_label: str, target_msg_idx: int = None, target_msg_id: int = None, target_raw_text: str = None) -> dict:
        """Xử lý yêu cầu hủy tin cược từ khách (VD: 'hủy tin 1' hoặc reply tin cược nhắn 'hủy').
        Thực hiện đầy đủ:
        1. Tìm tin cược theo msg_index, message_id (reply), hoặc raw_text của khách đó.
        2. Đánh dấu HỦY (Void) tin cược của khách và tính lại tiền cược.
        3. HỦY các bước chuyển thầu sinh ra từ tin cược này và dựng lại bảng balancer.
        4. Gửi tin nhắn thông báo hủy cho CHỦ THẦU (KHÔNG nhắc đến khách để bảo mật).
        5. Thông báo cho Chủ Bot (có ghi rõ khách nào để chủ bot quản lý).
        6. Trả về câu phản hồi gửi lại cho khách (VD: 'Đã hủy tin 1' - KHÔNG nhắc đến thầu).
        """
        cid_str = str(chat_id_str).strip()

        # 1. Kiểm tra nếu là tin treo (chưa duyệt)
        if hasattr(self, "pending_bets"):
            for i, pb in enumerate(self.pending_bets):
                match = False
                if str(pb.get("chat_id")) == cid_str:
                    if target_msg_idx is not None and pb.get("msg_idx") == target_msg_idx:
                        match = True
                    elif target_msg_id is not None and pb.get("user_msg_id") == target_msg_id:
                        match = True
                    elif target_raw_text and pb.get("raw_text", "").strip() == target_raw_text.strip():
                        match = True

                if match:
                    m_idx = pb.get("msg_idx", target_msg_idx or pb.get("id"))
                    raw_txt = pb.get("raw_text", "").strip()
                    self.pending_bets.pop(i)
                    self.save_pending_bets()
                    self.log(f"Đã hủy tin treo #{m_idx} của khách {sender_label}", "SUCCESS")
                    include_details_client = bool(self.config.get("cancel_detail_client", False))
                    c_reply = f"Đã hủy tin {m_idx}:\n{raw_txt}" if (include_details_client and raw_txt) else f"Đã hủy tin {m_idx}"
                    return {
                        "success": True,
                        "client_reply": c_reply,
                        "cancelled_transfers": 0
                    }

        # 2. Tìm trong client_bets
        target_client_key = None
        if cid_str in self.client_bets:
            target_client_key = cid_str
        else:
            for k, c in self.client_bets.items():
                if str(k) == cid_str or str(k).lstrip("@") == cid_str.lstrip("@") or str(c.get("username", "")).lstrip("@") == cid_str.lstrip("@"):
                    target_client_key = k
                    break

        if not target_client_key or target_client_key not in self.client_bets:
            lbl = f"#{target_msg_idx}" if target_msg_idx else ""
            return {
                "success": False,
                "client_reply": f"Không tìm thấy tin cược {lbl} để hủy.".strip(),
                "cancelled_transfers": 0
            }

        client_data = self.client_bets[target_client_key]
        hist = client_data.get("history", [])

        # Tìm tin cược trong history
        found_hist_idx = None
        found_item = None

        # Ưu tiên 1: Theo target_msg_idx (nếu có chỉ định)
        if target_msg_idx is not None:
            for idx, item in enumerate(hist):
                if item.get("msg_index") == target_msg_idx:
                    found_hist_idx = idx
                    found_item = item
                    break
            # Fallback theo vị trí index nếu phù hợp
            if found_item is None and 0 <= (target_msg_idx - 1) < len(hist):
                found_hist_idx = target_msg_idx - 1
                found_item = hist[found_hist_idx]

        # Ưu tiên 2: Theo message_id của tin nhắn khách được reply
        if found_item is None and target_msg_id is not None:
            for idx in range(len(hist) - 1, -1, -1):
                item = hist[idx]
                if item.get("user_msg_id") == target_msg_id:
                    found_hist_idx = idx
                    found_item = item
                    break

        # Ưu tiên 3: Theo nội dung tin cược raw_text
        if found_item is None and target_raw_text:
            clean_tgt = target_raw_text.strip()
            for idx in range(len(hist) - 1, -1, -1):
                item = hist[idx]
                if item.get("raw_text", "").strip() == clean_tgt and not item.get("voided", False):
                    found_hist_idx = idx
                    found_item = item
                    break

        # Ưu tiên 4: Nếu không có target_msg_idx và khách chỉ có đúng 1 tin chưa hủy
        if found_item is None and target_msg_idx is None:
            unvoided = [(i, it) for i, it in enumerate(hist) if not it.get("voided", False)]
            if len(unvoided) == 1:
                found_hist_idx, found_item = unvoided[0]

        if found_item is None:
            lbl = f"#{target_msg_idx}" if target_msg_idx else ""
            return {
                "success": False,
                "client_reply": f"Không tìm thấy tin cược {lbl} để hủy.".strip(),
                "cancelled_transfers": 0
            }

        target_msg_idx = found_item.get("msg_index", found_hist_idx + 1)

        if found_item.get("voided", False):
            return {
                "success": True,
                "client_reply": f"Tin #{target_msg_idx} đã được hủy trước đó rồi.",
                "cancelled_transfers": 0
            }

        # Đánh dấu HỦY tin cược của khách
        found_item["voided"] = True
        raw_text_cancelled = found_item.get("raw_text", "")

        # 3. Hủy đồng bộ các bước chuyển thầu tương ứng trong balancer.transfer_history
        transfers_to_notify = []
        for h in getattr(self.balancer, "transfer_history", []):
            h_cid = str(h.get("client_chat_id", "")).strip()
            h_idx = h.get("client_history_idx")
            m_idx = h.get("client_msg_idx")

            match = False
            if h_cid == target_client_key:
                if m_idx is not None and m_idx == target_msg_idx:
                    match = True
                elif found_hist_idx is not None and h_idx == found_hist_idx:
                    match = True

            if match and not h.get("voided", False):
                h["voided"] = True
                transfers_to_notify.append(h)

        if transfers_to_notify:
            self.balancer.recalculate_cumulative_transfers()

        # Tính lại tiền cược của khách và nạp lại bảng Balancer
        self.recompute_client_totals(target_client_key)
        self.rebuild_board_from_active_bets()
        self.save_client_bets()

        # Đồng bộ lại số thứ tự tin của khách
        valid_count = len([h for h in hist if not h.get("voided", False)])
        self.client_msg_counters[target_client_key] = valid_count

        # 4. Gửi tin nhắn thông báo HỦY SANG CHỦ THẦU (target_recipient)
        # BẢO MẬT TUYỆT ĐỐI: KHÔNG NHẮC ĐẾN TÊN HOẶC TIN CỦA KHÁCH KHI GỬI CHỦ THẦU
        # Chỉ gửi thông báo ngắn gọn: "Hủy tin 1", "Hủy tin 2"... KHÔNG viết thêm diễn giải các con số cược
        target_recipient = self.config.get("target_recipient", "").strip()
        contractor_notified = False
        include_details_contractor = bool(self.config.get("cancel_detail_contractor", False))
        if transfers_to_notify and target_recipient:
            sent_cancel_steps = set()
            for t_item in transfers_to_notify:
                step_num = t_item.get("step")
                cancel_num = step_num if (step_num is not None and str(step_num).strip() != "") else (target_msg_idx or 1)
                if cancel_num in sent_cancel_steps:
                    continue
                sent_cancel_steps.add(cancel_num)

                if include_details_contractor:
                    t_text = t_item.get("transfer_text", "").strip()
                    if not t_text:
                        transferred_dict = t_item.get("transferred", {})
                        t_text = self.balancer.format_transfer_message(transferred_dict, include_header=False)
                    cancel_contractor_msg = f"Hủy tin {cancel_num}:\n{t_text}" if t_text else f"Hủy tin {cancel_num}"
                else:
                    cancel_contractor_msg = f"Hủy tin {cancel_num}"

                ok, err = self.send_telegram_message(target_recipient, cancel_contractor_msg, track_for_cleanup=True, tag="transfer_cancel")
                if ok:
                    contractor_notified = True
                    self.log(f"📤 Đã gửi tin báo HỦY CƯỢC sang chủ thầu {target_recipient}: {cancel_contractor_msg}", "SUCCESS")
                else:
                    self.log(f"⚠️ Không gửi được tin báo hủy sang chủ thầu {target_recipient}: {err}", "WARN")

        # 5. Thông báo cho Chủ Bot (owner_chat_id)
        # CHỈ GỬI CHO CHỦ BOT MỚI NÊU RÕ KHÁCH NÀO ĐỂ CHỦ BOT QUẢN LÝ
        owner_cid = self.config.get("owner_chat_id")
        is_same_owner = False
        if owner_cid:
            clean_owner = str(owner_cid).strip().lstrip("@").lower()
            clean_target = str(target_client_key).strip().lstrip("@").lower()
            if clean_owner == clean_target:
                is_same_owner = True
            elif hasattr(self, "known_users"):
                u_info = self.known_users.get(str(target_client_key), {})
                if str(u_info.get("username", "")).lower() == clean_owner or str(u_info.get("user_id", "")) == clean_owner:
                    is_same_owner = True

        if owner_cid and not is_same_owner:
            owner_msg = (
                f"🔔 <b>KHÁCH HỦY TIN #{target_msg_idx}:</b>\n"
                f"👤 Khách: {sender_label}\n"
                f"📝 Nội dung tin hủy: <code>{raw_text_cancelled}</code>"
            )
            self.send_telegram_message(str(owner_cid), owner_msg)

        include_details_client = bool(self.config.get("cancel_detail_client", False))
        if include_details_client and raw_text_cancelled:
            client_reply_msg = f"Đã hủy tin {target_msg_idx}:\n{raw_text_cancelled}"
        else:
            client_reply_msg = f"Đã hủy tin {target_msg_idx}"

        self.log(f"✅ Đã hủy thành công tin #{target_msg_idx} của khách {sender_label}. Đã cập nhật lại bảng cược.", "SUCCESS")
        return {
            "success": True,
            "client_reply": client_reply_msg,
            "cancelled_transfers": len(transfers_to_notify),
            "contractor_notified": contractor_notified
        }

    def delete_single_raw_message(self, chat_id_str: str, history_idx: int = None, pending_id: any = None, raw_text: str = None) -> bool:
        """Xóa 1 tin nhắn gốc cụ thể khỏi danh sách (hoặc tin treo) và xóa đồng bộ cược chuyển thầu"""
        clean_pending_id = None
        if pending_id not in [None, "", "null", "undefined"]:
            try:
                clean_pending_id = int(pending_id)
            except (ValueError, TypeError):
                clean_pending_id = None

        # 1. Xóa nếu là tin treo (pending)
        if clean_pending_id is not None:
            for i, pb in enumerate(getattr(self, "pending_bets", [])):
                if pb.get("id") == clean_pending_id:
                    m_idx = pb.get("msg_idx")
                    self.balancer.delete_transfers_for_client_bet(
                        client_chat_id=pb.get("chat_id"),
                        client_msg_idx=m_idx
                    )
                    self.pending_bets.pop(i)
                    self.save_pending_bets()
                    self.log(f"Đã xóa vĩnh viễn tin treo #{clean_pending_id}", "SUCCESS")
                    return True

        # 2. Xóa nếu là tin đã nhận trong client_bets
        cid_str = str(chat_id_str or "").strip()
        target_cid = None
        if cid_str in self.client_bets:
            target_cid = cid_str
        else:
            for k, c in self.client_bets.items():
                if str(k) == cid_str or str(k).lstrip("@") == cid_str.lstrip("@") or str(c.get("username", "")).lstrip("@") == cid_str.lstrip("@"):
                    target_cid = k
                    break

        if target_cid and target_cid in self.client_bets:
            hist = self.client_bets[target_cid].get("history", [])
            target_h_idx = None

            # Xác định index cần xóa: ưu tiên history_idx, nếu lệch thì đối chiếu raw_text
            if history_idx is not None and 0 <= history_idx < len(hist):
                target_h_idx = history_idx
            elif raw_text:
                clean_raw = raw_text.strip()
                for idx, h in enumerate(hist):
                    if h.get("raw_text", "").strip() == clean_raw:
                        target_h_idx = idx
                        break

            if target_h_idx is not None and 0 <= target_h_idx < len(hist):
                item = hist[target_h_idx]
                m_idx = item.get("msg_index")
                self.balancer.delete_transfers_for_client_bet(
                    client_chat_id=target_cid,
                    client_history_idx=target_h_idx,
                    client_msg_idx=m_idx
                )
                hist.pop(target_h_idx)
                self.save_client_bets()
                self.recompute_client_totals(target_cid)
                self.rebuild_board_from_active_bets()
                valid_count = len([h for h in hist if not h.get("voided", False)])
                self.client_msg_counters[target_cid] = valid_count
                self.log(f"Đã xóa vĩnh viễn tin nhắn gốc #{target_h_idx + 1} của khách {target_cid}", "SUCCESS")
                return True

        # 3. Phương án vét cạn: tìm theo raw_text trên toàn bộ client_bets
        if raw_text:
            clean_raw = raw_text.strip()
            for k, c in self.client_bets.items():
                hist = c.get("history", [])
                for idx, h in enumerate(hist):
                    if h.get("raw_text", "").strip() == clean_raw:
                        m_idx = h.get("msg_index")
                        self.balancer.delete_transfers_for_client_bet(client_chat_id=k, client_history_idx=idx, client_msg_idx=m_idx)
                        hist.pop(idx)
                        self.save_client_bets()
                        self.recompute_client_totals(k)
                        self.rebuild_board_from_active_bets()
                        self.client_msg_counters[k] = len([x for x in hist if not x.get("voided", False)])
                        self.log(f"Đã tìm thấy và xóa vĩnh viễn tin nhắn theo nội dung của khách {k}", "SUCCESS")
                        return True

        return False


    def add_web_bets(self, bet_text: str, force: bool = False) -> dict:
        """
        Nhận cược từ Web nạp sang Bot:
        Phân tích cú pháp, nạp vào bảng cân cược và bắn cược thừa nếu vượt hạn mức.
        Có cơ chế chống đẩy trùng lặp nội dung.
        """
        clean_text = bet_text.strip()
        if not clean_text:
            return {"success": False, "error": "Chưa có nội dung cược để nạp."}

        self.check_and_rollover_date()

        if getattr(self, "is_settled_today", False):
            self.balancer.reset_board()
            self.client_bets = {}
            self.save_client_bets()
            self.client_msg_counters = {}
            self.pending_bets = []
            self.save_pending_bets()
            self.is_settled_today = False

        import hashlib
        text_hash = hashlib.md5(clean_text.encode("utf-8")).hexdigest()

        if not force and hasattr(self, "last_web_bet_hash") and self.last_web_bet_hash == text_hash:
            return {
                "success": False,
                "is_duplicate": True,
                "error": "Nội dung cược này vừa được nạp vào Bot rồi (dữ liệu không có gì thay đổi). Bot đã chặn để tránh cược bị nhân đôi và bắn lặp tin!",
                "total_bets": 0
            }

        from bet_parser import parse_bet_message
        parsed = parse_bet_message(clean_text)
        summary = parsed.get("summary", {})
        total_bets_count = summary.get("de_count", 0) + summary.get("lo_count", 0) + summary.get("bacang_count", 0) + summary.get("xien_count", 0)
        
        if total_bets_count == 0:
            return {"success": False, "error": "Nội dung không chứa cú pháp cược hợp lệ.", "parsed": parsed}

        self.last_web_bet_hash = text_hash
        self.last_web_bet_text = clean_text

        chat_id_str = "web_input"
        sender_label = "Chủ Bảng (Web)"
        msg_idx = self.client_msg_counters.get(chat_id_str, 0) + 1
        self.client_msg_counters[chat_id_str] = msg_idx
        self.record_client_bet(chat_id_str, sender_label, "web_user", parsed, raw_text=clean_text, msg_index=msg_idx)

        excess = self.balancer.add_bets(parsed)
        excess_count = sum(len(v) for v in excess.values())
        self.log(f"📥 Đã nạp {total_bets_count} cược từ Web vào Bot. Vượt mức giữ lại {excess_count} con.", "SUCCESS")

        transfer_msg = ""
        transferred = False
        if excess_count > 0:
            transfer_msg = self.balancer.format_transfer_message(excess, header_prefix="Thầu")
            if self.config.get("auto_forward_excess", True):
                target_recipient = self.config.get("target_recipient", "").strip()
                if target_recipient:
                    ok, err = self.send_telegram_message(target_recipient, transfer_msg, track_for_cleanup=True, tag="transfer")
                    if ok:
                        self.balancer.commit_transfers(excess)
                        self.stats["transfers_sent"] += 1
                        transferred = True
                        self.log(f"🛸 Đã tự động bắn cược thừa từ Web tới {target_recipient}:\n{transfer_msg}", "SUCCESS")

        return {
            "success": True,
            "total_bets": total_bets_count,
            "excess_count": excess_count,
            "transferred": transferred,
            "transfer_message": transfer_msg,
            "board": {
                "step_count": self.balancer.step_count,
                "de_sums": self.balancer.de_sums,
                "lo_sums": self.balancer.lo_sums,
                "bacang_sums": self.balancer.bacang_sums,
                "xien_count": len(self.balancer.xien_bets)
            }
        }

    def load_tracked_messages(self) -> list:
        if os.path.exists(TRACKED_MESSAGES_PATH):
            try:
                with open(TRACKED_MESSAGES_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def save_tracked_messages(self):
        try:
            with open(TRACKED_MESSAGES_PATH, "w", encoding="utf-8") as f:
                json.dump(self.tracked_messages, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def track_message(self, chat_id: int | str, message_id: int, tag: str = "bet"):
        """Lưu lại message_id và chat_id để tự động xóa sau 24h"""
        if not chat_id or not message_id:
            return
        try:
            cid = str(chat_id)
            mid = int(message_id)
            for item in self.tracked_messages:
                if str(item.get("chat_id")) == cid and item.get("message_id") == mid:
                    return
            self.tracked_messages.append({
                "chat_id": cid,
                "message_id": mid,
                "created_at": time.time(),
                "tag": tag
            })
            self.save_tracked_messages()
        except Exception as e:
            self.log(f"Lỗi theo dõi tin nhắn #{message_id}: {e}", "WARN")

    def delete_telegram_message(self, chat_id: int | str, message_id: int) -> tuple[bool, str]:
        """Gọi API Telegram deleteMessage để xóa tin nhắn"""
        token = self.config.get("bot_token", "").strip()
        if not token:
            return False, "Chưa nhập Bot Token"
        url = f"https://api.telegram.org/bot{token}/deleteMessage"
        payload = {
            "chat_id": str(chat_id),
            "message_id": int(message_id)
        }
        try:
            resp = requests.post(url, json=payload, timeout=8)
            data = resp.json()
            if data.get("ok"):
                return True, ""
            desc = data.get("description", "Không rõ lỗi")
            # Nếu tin nhắn đã bị xóa trước đó thì coi như thành công
            if "message to delete not found" in desc.lower():
                return True, desc
            return False, desc
        except Exception as e:
            return False, str(e)

    def clean_old_logs(self, max_age_seconds: int = 86400):
        """Xóa các dòng log cũ hơn 24h trong bot_activity.log để bảo mật tuyệt đối"""
        if not os.path.exists(LOG_PATH):
            return
        try:
            now = now_vn()
            kept_lines = []
            with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            
            current_entry_keep = True
            for line in lines:
                if len(line) >= 17 and line[0] == '[' and line[9] == ' ' and line[15] == ']':
                    date_str = line[10:15]  # DD/MM
                    time_str = line[1:9]    # HH:MM:SS
                    try:
                        d, m = map(int, date_str.split('/'))
                        h, mi, s = map(int, time_str.split(':'))
                        entry_dt = datetime(now.year, m, d, h, mi, s, tzinfo=VN_TZ)
                        if entry_dt > now + timedelta(days=1):
                            entry_dt = entry_dt.replace(year=now.year - 1)
                        age_sec = (now - entry_dt).total_seconds()
                        current_entry_keep = (0 <= age_sec <= max_age_seconds)
                    except Exception:
                        current_entry_keep = True
                if current_entry_keep:
                    kept_lines.append(line)
            
            if len(kept_lines) < len(lines):
                with open(LOG_PATH, "w", encoding="utf-8") as f:
                    f.writelines(kept_lines)
        except Exception:
            pass

    def check_and_cleanup_traces(self, force: bool = False) -> tuple[int, int]:
        """Tự động kiểm tra và xóa dấu vết cược sau 24h"""
        cleanup_seconds = int(self.config.get("cleanup_after_seconds", 86400))
        now = time.time()
        remaining = []
        deleted_count = 0

        for item in self.tracked_messages:
            item_time = item.get("created_at", 0)
            if force or (now - item_time >= cleanup_seconds):
                cid = item.get("chat_id")
                mid = item.get("message_id")
                ok, err = self.delete_telegram_message(cid, mid)
                if ok:
                    deleted_count += 1
                else:
                    # Nếu lỗi vĩnh viễn không thể xóa (ví dụ quá 48h, hoặc giới hạn Telegram)
                    if "can't be deleted" in err.lower() or "not found" in err.lower():
                        deleted_count += 1
                    else:
                        remaining.append(item)
            else:
                remaining.append(item)

        h_label = f"{round(cleanup_seconds/3600, 1):g}h"
        if deleted_count > 0:
            self.tracked_messages = remaining
            self.save_tracked_messages()
            self.log(f"🗑️ [Tự động xóa {h_label}] Đã xóa {deleted_count} tin nhắn cược trên Telegram.", "INFO")

        # Xóa nhật ký log cũ hơn cleanup_seconds
        self.clean_old_logs(max_age_seconds=cleanup_seconds)

        # Xóa dữ liệu cược trên bảng nếu sau cleanup_seconds không có hoạt động
        if self.last_bet_timestamp and (now - self.last_bet_timestamp >= cleanup_seconds):
            has_bets = (len(self.balancer.de_sums) > 0 or len(self.balancer.lo_sums) > 0 or 
                        len(self.balancer.bacang_sums) > 0 or len(self.balancer.xien_bets) > 0)
            if has_bets:
                self.balancer.reset_board()
                self.client_msg_counters = {}
                self.last_bet_timestamp = None
                self.log(f"🗑️ [Tự động xóa {h_label}] Đã tự động reset bảng cược và số thứ tự tin về 0 sau {h_label} không có hoạt động.", "INFO")

        return deleted_count, len(self.tracked_messages)

    def resolve_recipient(self, recipient_input: str) -> tuple[str, str]:
        """
        Phân giải recipient từ input (có thể là Chat ID số, hoặc @username).
        Trả về (chat_id, error_message).
        """
        raw = str(recipient_input).strip()
        if not raw:
            return "", "Chưa nhập thông tin người nhận."

        # Nếu là số điện thoại (bắt đầu bằng +84 hoặc 0 và có từ 9 chữ số)
        cleaned_num = raw.replace(" ", "").replace("-", "")
        if (cleaned_num.startswith("+84") and len(cleaned_num) >= 11) or (cleaned_num.startswith("0") and len(cleaned_num) >= 10):
            return "", "Telegram Bot KHÔNG THỂ gửi tin bằng Số điện thoại! Bạn cần lấy số Chat ID (ví dụ: 123456789) hoặc bảo người đó mở Bot bấm /start."

        # Nếu là @username hoặc tên hiển thị
        bot_uname = (self.stats.get("bot_info") or {}).get("username") or "bot"
        if raw.startswith("@") or not raw.lstrip("-").isdigit():
            uname = raw.lstrip("@").lower().strip()
            # Luôn nạp lại danh sách mới nhất từ known_users.json
            self.known_users = self.load_known_users()
            # 1. Tra trong known_users theo username
            for uid, info in self.known_users.items():
                if (info.get("username") or "").lower().strip() == uname:
                    return str(info.get("chat_id") or uid), ""
            # 2. Tra trong known_users theo first_name / display name
            for uid, info in self.known_users.items():
                if (info.get("first_name") or "").lower().strip() == uname:
                    return str(info.get("chat_id") or uid), ""
            # 3. Tra trong client_bets
            for cid_str, cinfo in self.client_bets.items():
                if (cinfo.get("username") or "").lower().strip() == uname:
                    return str(cid_str), ""
                if uname in (cinfo.get("name") or "").lower():
                    return str(cid_str), ""
            # Chưa từng nhắn cho bot
            return "", f"Người dùng @{uname} chưa từng mở chat hoặc bấm /start với @{bot_uname}. Hãy bảo @{uname} tìm @{bot_uname} trên Telegram và bấm /start trước, hoặc bấm nút '👥 Người đã chat' để chọn nhanh số Chat ID."

        # Nếu là số nguyên (hoặc số âm với nhóm Telegram)
        if raw.lstrip("-").isdigit():
            return raw, ""

        return "", f"Không nhận diện được người nhận '{raw}'. Vui lòng nhập Chat ID dạng số hoặc @username hợp lệ."

    def log(self, message: str, level: str = "INFO"):
        now_str = now_vn().strftime("%H:%M:%S %d/%m")
        entry = {
            "timestamp": now_str,
            "level": level,
            "message": message
        }
        self.logs.append(entry)
        if len(self.logs) > 200:
            self.logs.pop(0)

        # Ghi log ra file
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"[{now_str}] [{level}] {message}\n")
        except Exception:
            pass

    def send_telegram_message(self, recipient: str, text: str, track_for_cleanup: bool = False, tag: str = "outgoing") -> tuple[bool, str]:
        """Gửi tin nhắn qua Telegram Bot API. Trả về (thành công, thông điệp lỗi nếu có)"""
        if not self.is_running:
            self.log(f"⚠️ Bot đang TẮT: Đã chặn gửi tin nhắn tới {recipient}.", "WARN")
            return False, "Bot đang ở trạng thái TẮT (Chưa bấm Bật Bot trên Web)"

        token = self.config.get("bot_token", "").strip()
        if not token:
            return False, "Chưa nhập Bot Token."

        chat_id, err = self.resolve_recipient(recipient)
        if not chat_id:
            self.log(f"Lỗi gửi tin tới {recipient}: {err}", "WARN")
            return False, err

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            data = resp.json()
            if not data.get("ok"):
                desc = data.get("description", "Lỗi Telegram không xác định")
                bot_uname = (self.stats.get("bot_info") or {}).get("username") or "bot"
                if "chat not found" in desc.lower():
                    friendly_err = f"Telegram báo 'chat not found': Người nhận ({recipient}) chưa từng bấm /start với @{bot_uname} hoặc sai Chat ID."
                elif "blocked" in desc.lower():
                    friendly_err = f"Người nhận ({recipient}) đã chặn (block) Bot."
                else:
                    friendly_err = f"Lỗi Telegram: {desc}"
                self.log(f"Lỗi gửi tin tới {recipient} (Chat ID: {chat_id}): {friendly_err}", "WARN")
                return False, friendly_err

            # Nếu bật theo dõi để tự động xóa sau 24h
            if track_for_cleanup:
                sent_msg = data.get("result", {})
                sent_mid = sent_msg.get("message_id")
                sent_cid = sent_msg.get("chat", {}).get("id") or chat_id
                if sent_mid and sent_cid:
                    self.track_message(sent_cid, sent_mid, tag=tag)

            return True, ""
        except Exception as e:
            err_msg = f"Lỗi kết nối máy chủ Telegram: {e}"
            self.log(f"Lỗi kết nối tới {recipient}: {err_msg}", "ERROR")
            return False, err_msg

    def check_bot_token(self) -> dict:
        """Kiểm tra tính hợp lệ của Bot Token qua getMe"""
        token = self.config.get("bot_token", "").strip()
        if not token:
            return {"valid": False, "error": "Chưa nhập Bot Token"}

        url = f"https://api.telegram.org/bot{token}/getMe"
        try:
            resp = requests.get(url, timeout=6)
            data = resp.json()
            if data.get("ok"):
                self.stats["bot_info"] = data.get("result")
                return {"valid": True, "info": data.get("result")}
            else:
                err = data.get("description", "Token không hợp lệ")
                self.stats["error_message"] = err
                return {"valid": False, "error": err}
        except Exception as e:
            err = f"Không kết nối được server Telegram: {e}"
            self.stats["error_message"] = err
            return {"valid": False, "error": err}

    def is_sender_allowed(self, user_id: int, username: str) -> bool:
        """Kiểm tra người gửi có nằm trong danh sách khách được chỉ định không"""
        allowed = self.config.get("allowed_senders", [])
        if not allowed or "*" in allowed or "" in allowed:
            return True  # Cho phép tất cả nếu danh sách để trống hoặc chứa *

        # Chuẩn hóa
        uid_str = str(user_id)
        uname_str = ("@" + username.lstrip("@").lower()) if username else ""

        for item in allowed:
            item_str = str(item).strip().lower()
            if item_str == uid_str:
                return True
            if uname_str and (item_str == uname_str or item_str.lstrip("@") == uname_str.lstrip("@")):
                return True

    def parse_contractor_return_text(self, text: str, context_transferred: dict = None, context_parsed: dict = None) -> tuple[dict, str]:
        """
        Phân tích phần cược bị chủ thầu từ chối / trả lại trong tin nhắn:
        Ví dụ: 'Ok tin 1 Đề 12.32.52.62x10 trả lại Đề 98x10' -> ({'de': [{'number': '98', 'amount': 10.0}], ...}, 'Đề 98x10')
        """
        ret_match = re.search(
            r'(?:trả\s*lại|tra\s*lai|không\s*nhận|khong\s*nhan|ko\s*nhận|ko\s*nhan|k\s*nhận|k\s*nhan|từ\s*chối|tu\s*choi|\btrả\b|\btra\b)\s*(.*)',
            text,
            re.IGNORECASE
        )
        if not ret_match:
            return {}, ""

        raw_part = ret_match.group(1).strip()
        if not raw_part:
            return {}, ""

        parsed = parse_bet_message(raw_part)
        has_bets = bool(
            parsed.get('de') or parsed.get('lo') or parsed.get('bacang') or
            parsed.get('xien2') or parsed.get('xien3') or parsed.get('xien4')
        )

        # Nếu không parse được cược trực tiếp (ví dụ: 'trả lại 98' hoặc 'trả 98'),
        # đối chiếu với các số trong context_transferred hoặc context_parsed
        if not has_bets:
            candidate_nums = re.findall(r'\b\d{2,3}\b', raw_part)
            amt_match = re.search(r'[xX*](\d+(?:\.\d+)?)', raw_part)
            custom_amt = float(amt_match.group(1)) if amt_match else None

            for n in candidate_nums:
                matched = False
                # 1. Tra trong context_transferred
                if context_transferred:
                    if n in context_transferred.get('de', {}):
                        amt = custom_amt if custom_amt is not None else context_transferred['de'][n]
                        parsed['de'].append({'number': n, 'amount': amt})
                        matched = True
                    elif n in context_transferred.get('lo', {}):
                        amt = custom_amt if custom_amt is not None else context_transferred['lo'][n]
                        parsed['lo'].append({'number': n, 'amount': amt})
                        matched = True
                    elif n in context_transferred.get('bacang', {}):
                        amt = custom_amt if custom_amt is not None else context_transferred['bacang'][n]
                        parsed['bacang'].append({'number': n, 'amount': amt})
                        matched = True
                # 2. Tra trong context_parsed nếu chưa match
                if not matched and context_parsed:
                    for b in context_parsed.get('de', []):
                        if b['number'] == n:
                            amt = custom_amt if custom_amt is not None else b['amount']
                            parsed['de'].append({'number': n, 'amount': amt})
                            matched = True
                            break
                    if not matched:
                        for b in context_parsed.get('lo', []):
                            if b['number'] == n:
                                amt = custom_amt if custom_amt is not None else b['amount']
                                parsed['lo'].append({'number': n, 'amount': amt})
                                matched = True
                                break
                    if not matched:
                        for b in context_parsed.get('bacang', []):
                            if b['number'] == n:
                                amt = custom_amt if custom_amt is not None else b['amount']
                                parsed['bacang'].append({'number': n, 'amount': amt})
                                matched = True
                                break

        # Tra cứu bổ sung nếu có invalid_items dạng số
        if not (parsed.get('de') or parsed.get('lo') or parsed.get('bacang')):
            for inv in parsed.get('invalid_items', []):
                if inv.isdigit():
                    if context_transferred and inv in context_transferred.get('de', {}):
                        parsed['de'].append({'number': inv, 'amount': context_transferred['de'][inv]})
                    elif context_parsed:
                        for b in context_parsed.get('de', []):
                            if b['number'] == inv:
                                parsed['de'].append({'number': inv, 'amount': b['amount']})
                                break

        update_parsed_summary(parsed)
        return parsed, raw_part

    def apply_contractor_returned_bets(self, text_clean: str, sender_label: str) -> bool:
        """
        Xử lý khi Chủ thầu nhắn Ok kèm trả lại một phần:
        - Trừ các số trả lại khỏi chuyển thầu (không tính nợ thầu)
        - Trừ các số trả lại khỏi cược của khách (không tính tiền khách)
        - Gửi xác nhận cho khách (Ok tin X + Trả lại ...)
        - Forward cảnh báo cho Chủ bot
        """
        # 1. Thu thập ngữ cảnh cược đã chuyển và cược của khách
        last_transfer = None
        for th in reversed(self.balancer.transfer_history):
            if not th.get("voided", False):
                last_transfer = th
                break
        context_transferred = last_transfer.get("transferred", {}) if last_transfer else {}

        context_parsed = {}
        if self.pending_client_receipts:
            context_parsed = self.pending_client_receipts[-1].get("parsed", {})

        parsed_ret, raw_return_part = self.parse_contractor_return_text(text_clean, context_transferred, context_parsed)
        if not raw_return_part:
            return False

        # 2. Trừ khỏi lần chuyển gần nhất cho Chủ thầu
        if last_transfer:
            trans = last_transfer.get("transferred", {})
            for item in parsed_ret.get("de", []):
                num = str(item["number"]).zfill(2)
                amt = float(item["amount"])
                if num in trans.get("de", {}):
                    trans["de"][num] = max(0.0, trans["de"][num] - amt)
                    if trans["de"][num] <= 0.001:
                        del trans["de"][num]

            for item in parsed_ret.get("lo", []):
                num = str(item["number"]).zfill(2)
                amt = float(item["amount"])
                if num in trans.get("lo", {}):
                    trans["lo"][num] = max(0.0, trans["lo"][num] - amt)
                    if trans["lo"][num] <= 0.001:
                        del trans["lo"][num]

            for item in parsed_ret.get("bacang", []):
                num = str(item["number"]).zfill(3)
                amt = float(item["amount"])
                if num in trans.get("bacang", {}):
                    trans["bacang"][num] = max(0.0, trans["bacang"][num] - amt)
                    if trans["bacang"][num] <= 0.001:
                        del trans["bacang"][num]

            for cat in ["xien2", "xien3", "xien4"]:
                for item in parsed_ret.get(cat, []):
                    key_str = "-".join(str(x).zfill(2) for x in item.get("numbers", []))
                    amt = float(item["amount"])
                    if key_str in trans.get("xien", {}):
                        trans["xien"][key_str] = max(0.0, trans["xien"][key_str] - amt)
                        if trans["xien"][key_str] <= 0.001:
                            del trans["xien"][key_str]

            last_transfer["transferred"] = trans
            last_transfer["transfer_text"] = self.balancer.format_transfer_message(trans, include_header=False)
            self.balancer.recalculate_cumulative_transfers()

        # 3. Trừ khỏi cược của khách trong client_bets & gửi tin báo khách
        ret_summary = format_rejected_receipt(parsed_ret)
        if not ret_summary:
            ret_summary = f"Trả lại {raw_return_part}"

        if self.pending_client_receipts:
            for pending in list(self.pending_client_receipts):
                cid = pending.get("chat_id")
                m_idx = pending.get("msg_idx", 1)
                s_label = pending.get("sender_label", cid)
                orig_text = pending.get("text", "")

                if cid and cid in self.client_bets:
                    cdata = self.client_bets[cid]
                    h_target = None
                    for h in reversed(cdata.get("history", [])):
                        if h.get("msg_index") == m_idx:
                            h_target = h
                            break
                    if not h_target and cdata.get("history"):
                        h_target = cdata["history"][-1]

                    if h_target and h_target.get("parsed"):
                        p_hist = h_target["parsed"]
                        # Trừ đề
                        for ret_item in parsed_ret.get("de", []):
                            num = str(ret_item["number"]).zfill(2)
                            amt = float(ret_item["amount"])
                            for b in list(p_hist.get("de", [])):
                                if str(b.get("number")).zfill(2) == num:
                                    if b.get("amount", 0) <= amt:
                                        amt -= b.get("amount", 0)
                                        p_hist["de"].remove(b)
                                    else:
                                        b["amount"] -= amt
                                        amt = 0
                                    if amt <= 0:
                                        break
                        # Trừ lô
                        for ret_item in parsed_ret.get("lo", []):
                            num = str(ret_item["number"]).zfill(2)
                            amt = float(ret_item["amount"])
                            for b in list(p_hist.get("lo", [])):
                                if str(b.get("number")).zfill(2) == num:
                                    if b.get("amount", 0) <= amt:
                                        amt -= b.get("amount", 0)
                                        p_hist["lo"].remove(b)
                                    else:
                                        b["amount"] -= amt
                                        amt = 0
                                    if amt <= 0:
                                        break
                        # Trừ 3 càng
                        for ret_item in parsed_ret.get("bacang", []):
                            num = str(ret_item["number"]).zfill(3)
                            amt = float(ret_item["amount"])
                            for b in list(p_hist.get("bacang", [])):
                                if str(b.get("number")).zfill(3) == num:
                                    if b.get("amount", 0) <= amt:
                                        amt -= b.get("amount", 0)
                                        p_hist["bacang"].remove(b)
                                    else:
                                        b["amount"] -= amt
                                        amt = 0
                                    if amt <= 0:
                                        break
                        # Trừ xiên
                        for cat in ["xien2", "xien3", "xien4"]:
                            for ret_item in parsed_ret.get(cat, []):
                                ret_nums = sorted([str(x).zfill(2) for x in ret_item.get("numbers", [])])
                                amt = float(ret_item["amount"])
                                for b in list(p_hist.get(cat, [])):
                                    b_nums = sorted([str(x).zfill(2) for x in b.get("numbers", [])])
                                    if b_nums == ret_nums:
                                        if b.get("amount", 0) <= amt:
                                            amt -= b.get("amount", 0)
                                            p_hist[cat].remove(b)
                                        else:
                                            b["amount"] -= amt
                                            amt = 0
                                        if amt <= 0:
                                            break

                        update_parsed_summary(p_hist)
                        h_target["summary"] = p_hist.get("summary", {})

                    self.recompute_client_totals(cid)

                # Nhắn xác nhận cho khách: Ok tin X + Trả lại ...
                client_reply = f"Ok tin {m_idx}\n{ret_summary}".strip()
                self.send_telegram_message(cid, client_reply, track_for_cleanup=True, tag="receipt")
                self.log(f"Khách {s_label}: {orig_text} (Chủ thầu Ok có trả lại -> Đã gửi: '{client_reply}')", "SUCCESS")

            self.pending_client_receipts.clear()

        self.rebuild_board_from_active_bets()
        self.save_client_bets()

        # 4. Gửi cảnh báo cho Chủ bot
        owner_cid = self.config.get("owner_chat_id")
        if owner_cid:
            owner_alert = (
                f"⚠️ <b>CHỦ THẦU TRẢ LẠI MỘT PHẦN TIỀN CƯỢC:</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📩 Chủ thầu {sender_label}: <i>{text_clean}</i>\n"
                f"👉 <b>{ret_summary}</b>\n"
                f"✅ <i>Đã tự động trừ số này ra khỏi tính tiền thầu & tiền khách, và đã nhắn báo trả lại khách cược!</i>"
            )
            self.send_telegram_message(str(owner_cid), owner_alert)

        return True

    def notify_owner_new_pending_bet(self, item: dict):
        """Gửi tin nhắn thông báo cho Chủ bot khi có tin cược mới ở trạng thái Treo chờ duyệt"""
        owner_cid = self.config.get("owner_chat_id")
        if not owner_cid:
            return

        parsed = item.get("parsed", {})
        summary = parsed.get("summary", {})
        de_sum = summary.get("de_sum", 0)
        lo_sum = summary.get("lo_sum", 0)
        bc_sum = summary.get("bacang_sum", 0)
        x_sum = summary.get("xien_sum", 0)

        details = []
        if de_sum > 0: details.append(f"Đề: {summary.get('de_count')} con ({de_sum:g}k)")
        if lo_sum > 0: details.append(f"Lô: {summary.get('lo_count')} con ({lo_sum:g}đ)")
        if bc_sum > 0: details.append(f"3C: {summary.get('bacang_count')} con ({bc_sum:g}k)")
        if x_sum > 0: details.append(f"Xiên: {summary.get('xien_count')} cặp ({x_sum:g}k)")
        detail_txt = " | ".join(details) if details else "Hợp lệ"

        pid = item.get("id")
        sender_label = item.get("sender_label", "")
        raw_text = item.get("raw_text", "")

        msg = (
            f"⏳ <b>[TIN CƯỢC MỚI ĐANG TREO - CHỜ DUYỆT]</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🔢 <b>Mã tin:</b> <code>#{pid}</code>\n"
            f"👤 <b>Khách:</b> {sender_label}\n"
            f"📩 <b>Nội dung:</b> <code>{raw_text}</code>\n"
            f"📊 <b>Tổng cược:</b> {detail_txt}\n"
            f"⚠️ <i>Bot đang ở trạng thái TẮT -> Tin đang treo chờ duyệt trên Web!</i>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👉 Duyệt tin này: <code>/duyet {pid}</code>\n"
            f"👉 Duyệt tất cả: <code>/duyet all</code>\n"
            f"👉 Từ chối tin: <code>/huy {pid}</code>\n"
            f"<i>(Hoặc vào bảng Tin Gốc trên Web để bấm Duyệt)</i>"
        )
        self.send_telegram_message(str(owner_cid), msg)

    def approve_pending_bet(self, pending_id: int) -> dict:
        """
        Duyệt một tin cược đang ở trạng thái treo.
        Khi duyệt:
        - Tính toán cược, ghi nhận vào client_bets
        - Đưa vào balancer, tính toán cân chuyển cho chủ thầu
        - Bắn cược thừa cho chủ thầu (nếu có)
        - Nhắn tin Ok lại cho khách cược!
        """
        item = None
        for b in self.pending_bets:
            if b.get("id") == pending_id:
                item = b
                break

        if not item:
            return {"success": False, "error": f"Không tìm thấy tin cược #{pending_id}"}

        if item.get("status") != "pending":
            return {"success": False, "error": f"Tin cược #{pending_id} đã ở trạng thái '{item.get('status')}'"}

        chat_id_str = item["chat_id"]
        sender_label = item.get("sender_label", chat_id_str)
        username = item.get("username", "")
        parsed = item["parsed"]
        text = item["raw_text"]
        filter_return_msg = item.get("filter_return_msg", "")

        # 1. Tăng số thứ tự tin của khách (Ok tin 1, Ok tin 2...)
        msg_idx = self.client_msg_counters.get(chat_id_str, 0) + 1
        self.client_msg_counters[chat_id_str] = msg_idx
        item["msg_idx"] = msg_idx

        # 2. Ghi nhận cược vào client_bets
        self.record_client_bet(chat_id_str, sender_label, username, parsed, raw_text=text, msg_index=msg_idx, user_msg_id=item.get("user_msg_id"))
        hist_idx = len(self.client_bets[chat_id_str].get("history", [])) - 1

        # 3. Cân bảng và tính phần cược thừa
        excess = self.balancer.add_bets(parsed)
        excess_count = sum(len(v) for v in excess.values())
        has_forwarded_excess = False

        # 4. Nếu có cược thừa và bật chế độ tự động bắn
        if self.config.get("auto_forward_excess", True) and self.config.get("mode", "instant") == "instant":
            if excess_count > 0:
                target_recipient = self.config.get("target_recipient", "").strip()
                if target_recipient:
                    transfer_msg = self.balancer.format_transfer_message(excess, header_prefix="Thầu")
                    success, err = self.send_telegram_message(target_recipient, transfer_msg, track_for_cleanup=True, tag="transfer")
                    if success:
                        self.balancer.commit_transfers(
                            excess,
                            client_chat_id=chat_id_str,
                            client_msg_idx=msg_idx,
                            client_history_idx=hist_idx
                        )
                        self.stats["transfers_sent"] += 1
                        self.pending_recipient_acks = {
                            "timestamp": time.time(),
                            "recipient": target_recipient,
                            "alerted": False
                        }
                        has_forwarded_excess = True
                        transfer_summary = " ; ".join(transfer_msg.strip().splitlines())
                        self.log(f"Gửi chủ thầu {target_recipient}: {transfer_summary}", "INFO")
                    else:
                        self.log(f"❌ Thất bại khi gửi cược thừa tới {target_recipient}: {err}", "ERROR")

        # Lưu nội dung cân chuyển và giữ lại vào chi tiết tin khách
        single_transfer_txt = self.balancer.format_transfer_message(excess, include_header=False) if excess_count > 0 else ""
        single_retained_txt = self.balancer.calculate_retained_from_single_bet(parsed, excess)
        self.update_last_bet_transfer(chat_id_str, transfer_text=single_transfer_txt, retain_text=single_retained_txt)
        item["transfer_text"] = single_transfer_txt
        item["retained_text"] = single_retained_txt

        # 5. Phản hồi xác nhận cho khách:
        receipt_text = format_ok_receipt(parsed, msg_idx)
        if filter_return_msg:
            receipt_text = f"{receipt_text}\n{filter_return_msg}"

        if has_forwarded_excess:
            self.pending_client_receipts.append({
                "chat_id": chat_id_str,
                "receipt_text": receipt_text,
                "msg_idx": msg_idx,
                "sender_label": sender_label,
                "text": text,
                "parsed": json.loads(json.dumps(parsed)),
                "created_at": time.time()
            })
            self.log(f"Khách {sender_label}: {text} (Đã duyệt tin #{pending_id} -> Đã chuyển thầu {target_recipient}, chờ thầu Ok mới nhắn khách...)", "INFO")
        else:
            if len(receipt_text) > 3800:
                chunks = [receipt_text[i:i+3800] for i in range(0, len(receipt_text), 3800)]
                for chunk in chunks:
                    self.send_telegram_message(chat_id_str, chunk, track_for_cleanup=True, tag="receipt")
            else:
                self.send_telegram_message(chat_id_str, receipt_text, track_for_cleanup=True, tag="receipt")
            self.log(f"Khách {sender_label}: {text} (Đã duyệt tin #{pending_id} -> Giữ lại 100%, đã nhắn Ok tin {msg_idx})", "SUCCESS")

        # 6. Đánh dấu đã duyệt
        item["status"] = "approved"
        item["approved_at"] = now_vn().strftime("%H:%M:%S %d/%m")
        self.save_pending_bets()
        self.save_client_bets()

        return {"success": True, "message": f"Đã duyệt thành công tin #{pending_id}"}

    def approve_all_pending_bets(self) -> dict:
        """Duyệt tất cả các tin cược đang ở trạng thái treo"""
        pending_list = [b for b in self.pending_bets if b.get("status") == "pending"]
        if not pending_list:
            return {"success": False, "message": "Không có tin cược nào đang chờ duyệt."}

        approved_count = 0
        for b in pending_list:
            res = self.approve_pending_bet(b.get("id"))
            if res.get("success"):
                approved_count += 1

        return {"success": True, "approved_count": approved_count, "message": f"Đã duyệt thành công {approved_count} tin cược!"}

    def reject_pending_bet(self, pending_id: int, notify_client: bool = False, reason: str = "") -> dict:
        """Từ chối / Hủy một tin cược đang treo"""
        item = None
        for b in self.pending_bets:
            if b.get("id") == pending_id:
                item = b
                break

        if not item:
            return {"success": False, "error": f"Không tìm thấy tin cược #{pending_id}"}

        if item.get("status") != "pending":
            return {"success": False, "error": f"Tin #{pending_id} đã ở trạng thái '{item.get('status')}'"}

        item["status"] = "rejected"
        item["rejected_at"] = now_vn().strftime("%H:%M:%S %d/%m")
        self.save_pending_bets()

        chat_id_str = item["chat_id"]
        sender_label = item.get("sender_label", chat_id_str)
        text = item["raw_text"]

        # Tăng counter ngay cả khi reject để giữ đúng thứ tự tin (Ok tin 1, Ok tin 2, ...)
        # Nếu không tăng, tin tiếp theo của cùng khách sẽ bị đánh sai số thứ tự
        self.client_msg_counters[chat_id_str] = self.client_msg_counters.get(chat_id_str, 0) + 1
        item["msg_idx"] = self.client_msg_counters[chat_id_str]

        # Xóa các receipt đang chờ thầu OK cho khách này khỏi hàng đợi
        # (tránh trường hợp tin cũ được gửi nhầm cho khách khi thầu OK sau đó)
        before_count = len(self.pending_client_receipts)
        self.pending_client_receipts = [
            r for r in self.pending_client_receipts
            if r.get("chat_id") != chat_id_str
        ]
        if len(self.pending_client_receipts) < before_count:
            self.log(f"🗑️ Đã xóa {before_count - len(self.pending_client_receipts)} receipt đang chờ thầu của {sender_label} do tin #{pending_id} bị từ chối.", "WARN")

        if notify_client:
            reject_msg = f"Bot không nhận tin cược này:\n{text}"
            if reason:
                reject_msg += f"\n(Lý do: {reason})"
            self.send_telegram_message(chat_id_str, reject_msg)

        self.log(f"Chủ bot đã TỪ CHỐI tin #{pending_id} của {sender_label} ('{text}')", "WARN")
        return {"success": True, "message": f"Đã từ chối tin #{pending_id}"}


    def handle_incoming_message(self, message: dict):
        """Xử lý một tin nhắn nhận được từ khách"""
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        chat_type = chat.get("type", "private")
        is_group = chat_type in ["group", "supergroup"]
        group_title = f" [Nhóm: {chat.get('title')}]" if is_group else ""

        from_user = message.get("from", {})
        user_id = from_user.get("id")
        username = from_user.get("username", "")
        first_name = from_user.get("first_name", "")
        text = (message.get("text") or message.get("caption") or "").strip()

        if not text:
            return

        # Tự động kiểm tra sang ngày mới trước khi tiếp nhận bất kỳ tin nhắn nào
        self.check_and_rollover_date()

        # Lưu người dùng vào known_users
        self.record_user(from_user, chat_id)

        sender_label = f"(@{username})" if username else (f"({first_name})" if first_name else f"({user_id})")

        # Kiểm tra nếu người gửi là Người nhận cược thừa (target_recipient) đang được theo dõi phản hồi
        target_rec = str(self.config.get("target_recipient", "")).strip().lstrip("@").lower()
        is_recipient_sender = False
        if target_rec:
            sender_uid = str(user_id).strip()
            sender_cid = str(chat_id).strip()
            sender_un = (username or "").lower().lstrip("@")
            target_cid, _ = self.resolve_recipient(target_rec)
            if target_rec in [sender_uid, sender_cid, sender_un] or (target_cid and target_cid in [sender_uid, sender_cid]):
                is_recipient_sender = True
                if self.pending_recipient_acks:
                    self.pending_recipient_acks = None

                owner_cid = self.config.get("owner_chat_id")
                forward_contractor_all = self.config.get("forward_contractor_to_owner", False)

                # Phân tích nội dung tin nhắn của Chủ thầu:
                text_clean = text.strip()
                text_lower = text_clean.lower()

                # 1. Kiểm tra từ khóa từ chối / trả lại: "trả", "trả lại", "không nhận", "ko nhận"...
                reject_phrases = ["trả lại", "tra lai", "không nhận", "khong nhan", "ko nhận", "ko nhan", "k nhận", "k nhan", "từ chối", "tu choi", "hủy", "huy"]
                has_rejection = any(p in text_lower for p in reject_phrases) or bool(re.search(r"(?:\b|^)(trả|tra)(?:\b|$|\s|[0-9])", text_lower))

                # 2. Kiểm tra có chữ Ok, ok, OK ở đầu tin
                has_ok_start = bool(re.match(r"^\s*(ok|ок)\b", text_clean, re.IGNORECASE))

                if has_ok_start:
                    if has_rejection:
                        # TRƯỜNG HỢP 1A: CHỦ THẦU CÓ OK Ở ĐẦU TIN KÈM THEO TRẢ LẠI MỘT PHẦN
                        # Ví dụ: "Ok tin 1 Đề 12.32.52.62x10 trả lại Đề 98x10"
                        self.log(f"Chủ thầu {sender_label}: {text} (Ok có trả lại số)", "WARN")
                        handled = self.apply_contractor_returned_bets(text_clean, sender_label)
                        if not handled:
                            if forward_contractor_all and owner_cid and str(chat_id) != str(owner_cid):
                                self.send_telegram_message(str(owner_cid), f"📩 Chủ thầu {sender_label}: {text}")
                    else:
                        # TRƯỜNG HỢP 1B: CHỦ THẦU OK TOÀN BỘ
                        self.log(f"Chủ thầu {sender_label}: {text} (đã xác nhận Ok toàn bộ)", "SUCCESS")
                        if forward_contractor_all and owner_cid and str(chat_id) != str(owner_cid):
                            self.send_telegram_message(str(owner_cid), f"📩 Chủ thầu {sender_label}: {text}")

                        # Giải phóng hàng đợi: Nhắn Ok tin X cho các khách đang chờ
                        if hasattr(self, "pending_client_receipts") and self.pending_client_receipts:
                            for pending in list(self.pending_client_receipts):
                                cid = pending["chat_id"]
                                r_text = pending["receipt_text"]
                                s_label = pending.get("sender_label", cid)
                                orig_text = pending.get("text", "")
                                m_idx = pending.get("msg_idx", "")
                                if len(r_text) > 3800:
                                    chunks = [r_text[i:i+3800] for i in range(0, len(r_text), 3800)]
                                    for chunk in chunks:
                                        self.send_telegram_message(cid, chunk, track_for_cleanup=True, tag="receipt")
                                else:
                                    self.send_telegram_message(cid, r_text, track_for_cleanup=True, tag="receipt")
                                self.log(f"Khách {s_label}: {orig_text} (Chủ thầu đã Ok -> Đã nhắn lại Ok tin {m_idx})", "SUCCESS")
                            self.pending_client_receipts.clear()

                elif has_rejection:
                    # TRƯỜNG HỢP 2: CHỦ THẦU TỪ CHỐI / TRẢ LẠI TOÀN BỘ (KHÔNG CÓ OK Ở ĐẦU TIN)
                    # Bắt buộc forward tin nhắn này cho Chủ Bot cho dù KHÔNG chọn chức năng forward tất cả!
                    self.log(f"⚠️ Chủ thầu {sender_label} TỪ CHỐI/TRẢ LẠI TOÀN BỘ: '{text}' -> Bot KHÔNG gửi Ok cho khách!", "WARN")
                    if owner_cid and str(chat_id) != str(owner_cid):
                        reject_alert = (
                            f"🚨 <b>CẢNH BÁO: CHỦ THẦU TỪ CHỐI / TRẢ LẠI TIN CƯỢC!</b>\n"
                            f"━━━━━━━━━━━━━━━━━━\n"
                            f"📩 Chủ thầu {sender_label}: <i>{text}</i>\n"
                            f"👉 <b>Bot KHÔNG gửi Ok cho khách cược!</b>"
                        )
                        self.send_telegram_message(str(owner_cid), reject_alert)

                    # Cảnh báo chi tiết từng tin cược của khách đang bị treo cho Chủ bot xử lý
                    if hasattr(self, "pending_client_receipts") and self.pending_client_receipts:
                        for pending in list(self.pending_client_receipts):
                            s_label = pending.get("sender_label", "")
                            orig_text = pending.get("text", "")
                            m_idx = pending.get("msg_idx", "")
                            if owner_cid and str(chat_id) != str(owner_cid):
                                self.send_telegram_message(str(owner_cid), f"⚠️ Tin #{m_idx} của {s_label} ('{orig_text}') chưa được thầu nhận do bị trả lại.")
                        self.pending_client_receipts.clear()

                else:
                    # TRƯỜNG HỢP 3: CHỦ THẦU NHẮN TIN KHÁC (chưa có Ok ở đầu tin)
                    self.log(f"Chủ thầu {sender_label}: {text} (chưa có Ok ở đầu tin -> tiếp tục đợi Ok)", "INFO")
                    if forward_contractor_all and owner_cid and str(chat_id) != str(owner_cid):
                        self.send_telegram_message(str(owner_cid), f"📩 Chủ thầu {sender_label}: {text}")

        # Xử lý các lệnh điều khiển hệ thống
        cmd = text.lower().strip()
        parts = text.split()
        cmd_root = parts[0].lower() if parts else ""

        # Nếu người gửi là Chủ thầu và không phải gõ lệnh quản trị, dừng lại tại đây (không coi là cược của khách)
        if is_recipient_sender:
            admin_cmds = ["/mk", "mk", "/pass", "pass", "/login", "login", "/matkhau", "matkhau", "/help", "help", "/menu", "/status", "status"]
            if not any(cmd_root.startswith(c) for c in admin_cmds):
                return

        message_id = message.get("message_id")
        user_is_admin = self.is_admin(chat_id, username)

        # A. Đăng nhập mật khẩu (/mk <pass>, /pass <pass>, /login <pass>, /matkhau <pass>)
        if cmd_root in ["/mk", "mk", "/pass", "pass", "/login", "login", "/matkhau", "matkhau"] or cmd_root.startswith("/mk@") or cmd_root.startswith("/pass@"):
            # BẢO MẬT TUYỆT ĐỐI: TỰ ĐỘNG XÓA TIN NHẮN CHỨA MẬT KHẨU KHỎI LỊCH SỬ CHAT
            if message_id:
                self.delete_telegram_message(chat_id, message_id)

            if len(parts) < 2:
                self.send_telegram_message(str(chat_id), "🔑 <b>Nhập mật khẩu quản trị:</b>\n👉 Hãy gõ: <code>/mk &lt;mật_khẩu&gt;</code>\n<i>(Tin nhắn mật khẩu sẽ được tự động xóa ngay để bảo mật)</i>")
                return
            entered_pass = parts[1].strip()
            admin_pass = str(self.config.get("admin_password", "123456")).strip()
            if entered_pass == admin_pass:
                admins = self.config.setdefault("authenticated_admins", [])
                cid_str = str(chat_id)
                if cid_str not in [str(x) for x in admins]:
                    admins.append(cid_str)
                    self.save_config()
                self.log(f"{sender_label} đã đăng nhập quyền quản trị viên thành công!", "SUCCESS")
                welcome_msg = (
                    "🔓 <b>ĐĂNG NHẬP QUẢN TRỊ VIÊN THÀNH CÔNG!</b>\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "Chào mừng Chủ bảng! Bạn đã mở khóa toàn bộ quyền xem và thiết lập hệ thống:\n\n"
                    "📊 <b>/baocao</b>: Xem Báo Cáo Thầu / Giữ lại / Chuyển\n"
                    "📋 <b>/bang</b>: Xem Bảng Cược Tích Lũy hôm nay\n"
                    "⚙️ <b>/canchuyen</b>: Xem & Sửa Thiết Lập Cân Chuyển\n"
                    "🏷️ <b>/gia</b>: Xem & Sửa Bảng Giá & Hoa Hồng\n"
                    "🚀 <b>/chuyen</b>: Bắn Cược Thừa Ngay Lập Tức\n"
                    "👑 <b>/chubot</b>: Cài đặt nick Chủ Bot tự nhận diện\n"
                    "⏰ <b>/timer &lt;giờ&gt;</b>: Đổi số giờ tự động xóa vết cược\n"
                    "🧹 <b>/clean</b>: Xóa ngay các vết tin cược cũ\n"
                    "🗑️ <b>/reset</b>: Xóa bảng cược bắt đầu ngày mới\n"
                    "🔑 <b>/doimk &lt;mk_mới&gt;</b>: Đổi mật khẩu quản trị\n"
                    "🚪 <b>/logout</b>: Đăng xuất quyền quản trị trên thiết bị này\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "💡 <i>Gõ /help bất kỳ lúc nào để xem lại danh sách lệnh.</i>"
                )
                self.send_telegram_message(str(chat_id), welcome_msg)
                return
            else:
                self.log(f"{sender_label} nhập sai mật khẩu quản trị: '{entered_pass}'", "WARN")
                self.send_telegram_message(str(chat_id), "❌ <b>Mật khẩu không chính xác!</b>\nVui lòng thử lại: <code>/mk &lt;mật_khẩu&gt;</code>")
                return

        # B. Đổi mật khẩu (/doimk <mk_moi>, /doimatkhau <mk_moi>)
        if cmd_root in ["/doimk", "doimk", "/doimatkhau", "doimatkhau"] or cmd_root.startswith("/doimk@"):
            # TỰ ĐỘNG XÓA TIN NHẮN ĐỔI MẬT KHẨU ĐỂ BẢO VỆ
            if message_id:
                self.delete_telegram_message(chat_id, message_id)

            if not user_is_admin:
                self.send_telegram_message(str(chat_id), self.msg_need_auth())
                return
            if len(parts) < 2 or not parts[1].strip():
                self.send_telegram_message(str(chat_id), "⚠️ Cú pháp: <code>/doimk &lt;mật_khẩu_mới&gt;</code>\nVí dụ: <code>/doimk 654321</code>")
                return
            new_pass = parts[1].strip()
            self.config["admin_password"] = new_pass
            self.save_config()
            self.log(f"{sender_label} đã đổi mật khẩu quản trị sang: {new_pass}", "INFO")
            self.send_telegram_message(str(chat_id), f"🔑 <b>Thành công:</b> Đã đổi mật khẩu quản trị mới thành công!\nHãy ghi nhớ mật khẩu này cho các lần truy cập sau.")
            return

        # B2. Cài đặt hoặc xem Chủ Bot (/chubot [id/@username], /owner)
        if cmd_root in ["/chubot", "chubot", "/owner", "owner"] or cmd_root.startswith("/chubot@"):
            if not user_is_admin:
                self.send_telegram_message(str(chat_id), self.msg_need_auth())
                return
            if len(parts) >= 2:
                new_owner = parts[1].strip()
                if new_owner.lower() in ["xoa", "clear", "huy"]:
                    self.config["owner_chat_id"] = ""
                    self.save_config()
                    self.send_telegram_message(str(chat_id), "👑 Đã xóa Chủ Bot. Bây giờ cần nhập mật khẩu (/mk) để quản trị.")
                    return
                self.config["owner_chat_id"] = new_owner
                self.save_config()
                self.log(f"{sender_label} đã đặt Chủ Bot là: {new_owner}", "SUCCESS")
                self.send_telegram_message(str(chat_id), f"👑 <b>Thành công:</b> Đã cài đặt Chủ Bot là: <code>{new_owner}</code>\nTài khoản này sẽ tự động được nhận diện làm Chủ bảng với toàn quyền điều khiển mà không cần gõ mật khẩu!")
                return
            cur_owner = self.config.get("owner_chat_id") or "Chưa cài đặt"
            self.send_telegram_message(str(chat_id), f"👑 <b>Chủ Bot hiện tại:</b> <code>{cur_owner}</code>\n\n👉 Cài đặt Chủ Bot: <code>/chubot &lt;chat_id_hoặc_@username&gt;</code>\n👉 Xóa: <code>/chubot xoa</code>\n<i>(Khi đã là Chủ Bot, nhắn tin lệnh điều khiển sẽ tự động nhận diện không cần gõ /mk)</i>")
            return

        # B3. Duyệt tin cược đang treo: /duyet <id> hoặc /duyet all
        if cmd_root in ["/duyet", "duyet"] or cmd_root.startswith("/duyet@"):
            if not user_is_admin:
                self.send_telegram_message(str(chat_id), self.msg_need_auth())
                return
            if len(parts) < 2:
                self.send_telegram_message(str(chat_id), "👉 Vui lòng nhập mã tin cần duyệt:\nVí dụ: <code>/duyet 1</code> hoặc <code>/duyet all</code> để duyệt tất cả.")
                return
            target_arg = parts[1].strip().lower()
            if target_arg in ["all", "het", "tatca"]:
                res = self.approve_all_pending_bets()
                self.send_telegram_message(str(chat_id), res.get("message", "Đã xử lý duyệt tất cả!"))
            elif target_arg.isdigit():
                res = self.approve_pending_bet(int(target_arg))
                if res.get("success"):
                    self.send_telegram_message(str(chat_id), f"✅ Đã duyệt thành công tin #{target_arg}!")
                else:
                    self.send_telegram_message(str(chat_id), f"❌ {res.get('error', 'Lỗi không xác định')}")
            else:
                self.send_telegram_message(str(chat_id), "❌ Mã tin không hợp lệ. Vui lòng nhập số (ví dụ: <code>/duyet 1</code>).")
            return

        # B4. Hủy / Từ chối tin cược đang treo: /huy <id>
        if cmd_root.startswith("/huy") or cmd_root in ["/tuchoi", "/reject"]:
            if not user_is_admin:
                self.send_telegram_message(str(chat_id), self.msg_need_auth())
                return
            if len(parts) < 2 or not parts[1].strip().isdigit():
                self.send_telegram_message(str(chat_id), "👉 Vui lòng nhập mã tin cần từ chối: <code>/huy 1</code>")
                return
            pid = int(parts[1].strip())
            res = self.reject_pending_bet(pid)
            if res.get("success"):
                self.send_telegram_message(str(chat_id), f"❌ Đã từ chối tin #{pid}!")
            else:
                self.send_telegram_message(str(chat_id), f"❌ {res.get('error', 'Lỗi không xác định')}")
            return

        # B5. Xem danh sách tin cược đang treo: /treo
        if cmd_root in ["/treo", "treo", "/pending", "pending"] or cmd_root.startswith("/treo@"):
            if not user_is_admin:
                self.send_telegram_message(str(chat_id), self.msg_need_auth())
                return
            pending_list = [b for b in self.pending_bets if b.get("status") == "pending"]
            if not pending_list:
                self.send_telegram_message(str(chat_id), "🟢 Hiện tại không có tin cược nào đang treo chờ duyệt.")
                return
            lines = [f"⏳ <b>DANH SÁCH TIN ĐANG TREO CHỜ DUYỆT ({len(pending_list)} tin):</b>\n━━━━━━━━━━━━━━━━━━"]
            for b in pending_list:
                lines.append(f"• <b>#{b.get('id')}</b> - {b.get('sender_label')}: <code>{b.get('raw_text')}</code>")
            lines.append("\n👉 Nhắn <code>/duyet &lt;mã&gt;</code> hoặc <code>/duyet all</code> để duyệt.")
            self.send_telegram_message(str(chat_id), "\n".join(lines))
            return

        # B6. Bật / Tắt chế độ tự động của Bot: /bat, /tat
        if cmd_root in ["/bat", "bat"] or cmd_root.startswith("/bat@"):
            if not user_is_admin:
                self.send_telegram_message(str(chat_id), self.msg_need_auth())
                return
            self.config["bot_mode"] = "auto"
            self.save_config()
            self.log("Chủ bot đã BẬT Bot (Chế độ tự động)", "SUCCESS")
            self.send_telegram_message(str(chat_id), "🟢 <b>ĐÃ BẬT BOT (TỰ ĐỘNG)</b>\nBot sẽ tự động nhận cược, cân chuyển và Ok lại cho khách!")
            return

        if cmd_root in ["/tat", "tat"] or cmd_root.startswith("/tat@"):
            if not user_is_admin:
                self.send_telegram_message(str(chat_id), self.msg_need_auth())
                return
            self.config["bot_mode"] = "manual"
            self.save_config()
            self.log("Chủ bot đã TẮT Bot (Chế độ Treo chờ duyệt)", "WARN")
            self.send_telegram_message(str(chat_id), "🟡 <b>ĐÃ TẮT BOT (CHẾ ĐỘ TREO CHỜ DUYỆT)</b>\nBot vẫn quản lý tin nhắn nhưng không tự ý Ok khách. Mọi tin cược sẽ ở trạng thái Treo chờ bạn duyệt!")
            return

        # C. Đăng xuất (/logout, /dangxuat)
        if cmd_root in ["/logout", "logout", "/dangxuat", "dangxuat"] or cmd_root.startswith("/logout@"):
            cid_str = str(chat_id)
            admins = self.config.get("authenticated_admins", [])
            self.config["authenticated_admins"] = [x for x in admins if str(x) != cid_str]
            self.save_config()
            self.log(f"{sender_label} đã đăng xuất quyền quản trị", "INFO")
            self.send_telegram_message(str(chat_id), "🔒 <b>Đã đăng xuất quyền quản trị.</b>\nĐể truy cập lại các tính năng quản lý, hãy gõ: <code>/mk &lt;mật_khẩu&gt;</code>")
            return

        # D. /start hoặc /id
        if cmd in ["/start", "start", "/id", "id"] or cmd.startswith("/start@") or cmd.startswith("/id@"):
            admin_status = " 👑 <i>(Quản trị viên đã đăng nhập)</i>" if self.is_admin(chat_id) else ""
            reply = (
                f"👋 <b>Xin chào {first_name}!</b>{admin_status}\n\n"
                f"🆔 <b>Chat ID của bạn:</b> <code>{chat_id}</code>\n"
                f"👤 <b>Username:</b> @{username if username else '(Chưa có)'}\n\n"
                f"👉 <i>Sao chép Chat ID <code>{chat_id}</code> này dán vào ô <b>'Người nhận cược thừa'</b> hoặc <b>'Khách được chỉ định'</b> trên Web nhé!</i>\n\n"
            )
            if self.is_admin(chat_id):
                reply += "💡 <i>Gõ <b>/help</b> để xem đầy đủ các lệnh quản trị hệ thống.</i>"
            else:
                reply += "💡 <i>Gửi tin cược (VD: <code>de 88=100</code>, <code>lo 12=10d</code>) để đặt số.\n🔒 Quản trị viên: Gõ <code>/mk &lt;mật_khẩu&gt;</code> để mở khóa quyền quản lý.</i>"
            self.send_telegram_message(str(chat_id), reply)
            self.log(f"Người dùng {sender_label} đã kết nối và nhận Chat ID: {chat_id}", "INFO")
            return

        # E. /help hoặc help
        if cmd in ["/help", "help", "/menu", "menu"] or cmd.startswith("/help@"):
            if self.is_admin(chat_id):
                help_text = (
                    "🤖 <b>CÁC LỆNH QUẢN TRỊ HỆ THỐNG (Chủ Bảng):</b>\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "📊 <b>/baocao</b>: Xem Báo cáo Thầu / Giữ lại / Chuyển\n"
                    "📋 <b>/bang</b>: Xem tổng cược tích lũy hôm nay\n"
                    "⚙️ <b>/canchuyen [bat|tat]</b>: Bật/Tắt Cân Chuyển hoặc Xem/Sửa mức giữ\n"
                    "🛸 <b>/nguoinhan [id|@user|xoa]</b>: Xem/Đổi/Xóa người nhận cược thừa\n"
                    "👥 <b>/khach [them|xoa|tatca]</b>: Xem/Thêm/Xóa khách gửi cược\n"
                    "📒 <b>/danhba</b>: Xem danh bạ những người đã chat với Bot\n"
                    "🏷️ <b>/gia</b>: Xem & Sửa Bảng Giá Thầu & Chuyển\n"
                    "🚀 <b>/chuyen [bat|tat]</b>: Bật/Tắt hoặc Bắn ngay các cược vượt định mức\n"
                    "⏰ <b>/timer &lt;giờ&gt;</b>: Đổi số giờ tự động xóa vết cược\n"
                    "🧹 <b>/clean</b>: Xóa dấu vết tin cược cũ ngay lập tức\n"
                    "🗑️ <b>/reset</b>: Xóa cược bắt đầu ngày mới\n"
                    "🎯 <b>/kqxs</b>: Xem Kết quả Xổ số Miền Bắc hôm nay\n"
                    "🔑 <b>/doimk &lt;mk_mới&gt;</b>: Đổi mật khẩu quản trị\n"
                    "🚪 <b>/logout</b>: Đăng xuất quyền quản trị\n"
                    "🆔 <b>/id</b>: Xem Chat ID của bạn\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "<i>Tin nhắn cược của khách (VD: de 88x100, lo 12x20d) bot vẫn tự động nhận & cân bảng.</i>"
                )
            else:
                help_text = (
                    "🤖 <b>HƯỚNG DẪN ĐẶT CƯỢC TỰ ĐỘNG:</b>\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "Bạn chỉ cần nhắn tin cược theo cú pháp, hệ thống sẽ tự động nhận & phản hồi biên nhận:\n"
                    "• Đề: <code>de 88=100</code>, <code>de 141 292x25k</code>, <code>dau 5=10</code>\n"
                    "• Lô: <code>lo 65=30d</code>, <code>lo 09 575=10d</code>\n"
                    "• 3 Càng: <code>3c 686 685 586=10k</code>\n"
                    "• Xiên: <code>xien 12.34=50k</code>, <code>xq 12.34.56=20k</code>\n"
                    "• Hủy tin: <code>hủy tin 1</code>, <code>huy tin 2</code> (để hủy tin cược đã đặt)\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "🆔 <b>/id</b>: Xem Chat ID của bạn\n"
                    "🎯 <b>/kqxs</b>: Xem Kết quả Xổ số Miền Bắc hôm nay\n"
                    "🔒 <i>Chức năng quản trị bảng cược yêu cầu mật khẩu:</i> <code>/mk &lt;mật_khẩu&gt;</code>"
                )
            self.send_telegram_message(str(chat_id), help_text)
            return

        # F. /kqxs hoặc kết quả (Mở cho tất cả, hỗ trợ xem theo ngày: /kqxs DD/MM/YYYY)
        if cmd_root in ["/kqxs", "kqxs", "kết quả", "ket qua", "kq"] or cmd_root.startswith("/kqxs@"):
            target_date = parts[1].strip() if len(parts) >= 2 else None
            self.log(f"{sender_label} yêu cầu lấy KQXS {target_date or 'hôm nay'}", "INFO")
            kq = fetch_xsmb(target_date)
            if not target_date or target_date == now_vn().strftime("%d/%m/%Y"):
                self.cached_kqxs = kq

            msg = format_xsmb_message(kq)
            self.send_telegram_message(str(chat_id), msg)

            if kq.get("success"):
                # Nếu là Admin, gửi thêm báo cáo tài chính tổng hợp
                if self.is_admin(chat_id):
                    acc = calculate_board_accounting(self.balancer, kq, self.config.get("price_config"))
                    thau_rep = format_accounting_report(acc, "thau")
                    giulai_rep = format_accounting_report(acc, "giulai")
                    chuyen_rep = format_accounting_report(acc, "chuyen")
                    admin_rep = (
                        f"📊 <b>BÁO CÁO TÀI CHÍNH ({kq.get('date')}):</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"📋 <b>BẢNG THẦU:</b>\n{thau_rep}\n\n"
                        f"🛸 <b>BẢNG CHUYỂN:</b>\n{chuyen_rep}\n\n"
                        f"🛡️ <b>BẢNG GIỮ LẠI (Ăn thua):</b>\n{giulai_rep}\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"👉 Gõ <code>/chottien {kq.get('date')}</code> để chốt và gửi tin nhắn âm dương tới từng khách & người nhận."
                    )
                    self.send_telegram_message(str(chat_id), admin_rep)
                # Nếu là Khách cược đã có cược trong ngày, gửi bảng chốt riêng của khách
                elif str(chat_id) in self.client_bets:
                    cdata = self.client_bets[str(chat_id)]
                    c_res = calculate_single_client_accounting(cdata, kq, self.config.get("price_config"))
                    if c_res["accounting"]["totalVon"] > 0:
                        self.send_telegram_message(str(chat_id), f"📊 <b>BẢNG CHỐT TIỀN CỦA BẠN:</b>\n{c_res['report_text']}")
            return

        # G. /baocao (Yêu cầu mật khẩu)
        if cmd in ["/baocao", "baocao", "báo cáo", "bao cao", "/bc", "bc"] or cmd.startswith("/baocao@"):
            if not self.is_admin(chat_id):
                self.send_telegram_message(str(chat_id), self.msg_need_auth())
                return
            self.log(f"{sender_label} yêu cầu xuất báo cáo", "INFO")
            kq = self.cached_kqxs or fetch_xsmb()
            self.cached_kqxs = kq
            acc = calculate_board_accounting(self.balancer, kq, self.config.get("price_config"))

            thau_rep = format_accounting_report(acc, "thau")
            giulai_rep = format_accounting_report(acc, "giulai")
            chuyen_rep = format_accounting_report(acc, "chuyen")

            reply_msg = (
                f"📊 <b>BÁO CÁO TÀI CHÍNH TỔNG HỢP - {kq.get('date')}</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📋 <b>BẢNG THẦU:</b>\n{thau_rep}\n\n"
                f"🛸 <b>BẢNG CHUYỂN:</b>\n{chuyen_rep}\n\n"
                f"🛡️ <b>GIỮ LẠI (Ăn thua):</b>\n{giulai_rep}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"👑 Đề về: <b>{kq.get('special_last2', '--')}</b> | 3C: <b>{kq.get('special_last3', '---')}</b>\n"
                f"👉 Gõ <code>/chottien</code> để tự động gửi tin nhắn âm dương tới khách và người nhận."
            )
            self.send_telegram_message(str(chat_id), reply_msg)
            return

        # G2. /chottien (Chốt tiền tự động bắn tin âm dương cho khách & người nhận - Yêu cầu mật khẩu)
        if cmd_root in ["/chottien", "chottien", "/chot", "chot", "/chotso", "chotso"] or cmd_root.startswith("/chottien@"):
            if not self.is_admin(chat_id):
                self.send_telegram_message(str(chat_id), self.msg_need_auth())
                return

            target_date = parts[1].strip() if len(parts) >= 2 else None
            self.log(f"{sender_label} kích hoạt lệnh chốt tiền ngày {target_date or 'hôm nay'}", "INFO")
            kq = fetch_xsmb(target_date)
            if not kq.get("success") or not kq.get("prizes"):
                self.send_telegram_message(str(chat_id), f"⚠️ Không lấy được KQXS cho ngày {target_date or 'hôm nay'} để chốt tiền ({kq.get('error', 'Chưa có kết quả')}).")
                return

            res = self.settle_all(kq, notify_clients=True, notify_recipient=True, notify_owner=True, requested_by=str(chat_id))
            self._reset_after_settle(kq.get('date', ''), settle_result=res, kqxs=kq)
            self.send_telegram_message(str(chat_id), f"✅ <b>ĐÃ HOÀN TẤT CHỐT TIỀN NGÀY {kq.get('date')}!</b>\n👉 Đã gửi tin nhắn âm/dương tới <b>{res['settled_clients_count']}</b> khách cược và người nhận cược thừa.\n🔄 Bảng cược đã được reset sang ngày mới (Đã lưu lịch sử đối soát).")
            return


        # H. /bang hoặc /canbang (Yêu cầu mật khẩu)
        if cmd in ["/bang", "bảng", "bang", "/canbang", "cân bảng", "can bang"] or cmd.startswith("/bang@"):
            if not self.is_admin(chat_id):
                self.send_telegram_message(str(chat_id), self.msg_need_auth())
                return
            b = self.balancer
            de_c = len(b.de_sums)
            de_s = sum(b.de_sums.values())
            lo_c = len(b.lo_sums)
            lo_s = sum(b.lo_sums.values())
            bc_c = len(b.bacang_sums)
            bc_s = sum(b.bacang_sums.values())
            x_c = len(b.xien_bets)
            x_s = sum(x.get("amount", 0) for x in b.xien_bets)

            lines = [
                "📋 <b>BẢNG CƯỢC TÍCH LŨY HÔM NAY:</b>",
                "━━━━━━━━━━━━━━━━━━",
                f"🔴 <b>Đề:</b> {de_c} con (Tổng: {de_s:,.0f}k)",
                f"🔵 <b>Lô:</b> {lo_c} con (Tổng: {lo_s:,.0f}đ)",
                f"🟣 <b>3 Càng:</b> {bc_c} con (Tổng: {bc_s:,.0f}k)",
                f"🟢 <b>Xiên:</b> {x_c} cặp (Tổng: {x_s:,.0f}k)",
                f"🛸 <b>Đã bắn cược:</b> {b.step_count} lần",
                "━━━━━━━━━━━━━━━━━━"
            ]
            if de_c > 0:
                top_de = sorted(b.de_sums.items(), key=lambda x: x[1], reverse=True)[:5]
                top_de_str = ", ".join(f"{k}x{v:,.0f}" for k, v in top_de)
                lines.append(f"• Top Đề: {top_de_str}")
            if lo_c > 0:
                top_lo = sorted(b.lo_sums.items(), key=lambda x: x[1], reverse=True)[:5]
                top_lo_str = ", ".join(f"{k}x{v:,.0f}đ" for k, v in top_lo)
                lines.append(f"• Top Lô: {top_lo_str}")

            self.send_telegram_message(str(chat_id), "\n".join(lines))
            return

        # I. /canchuyen, /thietlap, /giulai (Xem & Sửa thiết lập cân chuyển - Yêu cầu mật khẩu)
        if cmd_root in ["/canchuyen", "canchuyen", "/thietlap", "thietlap", "/giulai", "giulai", "/bancuoc", "bancuoc", "/batcanchuyen", "batcanchuyen", "/tatcanchuyen", "tatcanchuyen"] or cmd_root.startswith("/canchuyen@") or cmd_root.startswith("/giulai@") or cmd_root.startswith("/bancuoc@"):
            if not self.is_admin(chat_id):
                self.send_telegram_message(str(chat_id), self.msg_need_auth())
                return

            # Xử lý lệnh dạng /batcanchuyen hoặc /tatcanchuyen
            if cmd_root in ["/batcanchuyen", "batcanchuyen"]:
                self.config["auto_forward_excess"] = True
                self.save_config()
                self.log(f"{sender_label} đã BẬT chức năng cân chuyển cược", "INFO")
                mode_txt = "Tức thì (instant)" if self.config.get("mode") == "instant" else "Gom bảng (batch)"
                self.send_telegram_message(str(chat_id), f"🟢 <b>Thành công:</b> Đã <b>BẬT</b> chức năng cân chuyển cược tự động!\n\n💡 <i>Chế độ hiện tại: <b>{mode_txt}</b>. Cược của khách vượt định mức giữ lại sẽ được tự động cân và chuyển sang người nhận.</i>")
                return
            elif cmd_root in ["/tatcanchuyen", "tatcanchuyen"]:
                self.config["auto_forward_excess"] = False
                self.save_config()
                self.log(f"{sender_label} đã TẮT chức năng cân chuyển cược", "INFO")
                self.send_telegram_message(str(chat_id), "🔴 <b>Thành công:</b> Đã <b>TẮT</b> chức năng cân chuyển cược tự động!\n\n💡 <i>Bot vẫn nhận cược và gửi tin xác nhận (Ok...) cho khách bình thường, nhưng TẠM DỪNG tự động cân và bắn cược thừa sang thầu trên.\n👉 Để bật lại: gõ <code>/canchuyen bat</code></i>")
                return

            if len(parts) == 1:
                summary_msg = format_retain_config_summary(self.config)
                self.send_telegram_message(str(chat_id), summary_msg)
                return

            arg1 = parts[1].lower()
            if arg1 in ["tat", "off", "0", "huy", "dong", "disable", "pause", "tamdung", "dung"]:
                self.config["auto_forward_excess"] = False
                self.save_config()
                self.log(f"{sender_label} đã TẮT chức năng cân chuyển cược", "INFO")
                self.send_telegram_message(str(chat_id), "🔴 <b>Thành công:</b> Đã <b>TẮT</b> chức năng cân chuyển cược tự động!\n\n💡 <i>Bot vẫn nhận cược và gửi tin xác nhận (Ok...) cho khách bình thường, nhưng TẠM DỪNG tự động cân và bắn cược thừa sang thầu trên.\n👉 Để bật lại: gõ <code>/canchuyen bat</code></i>")
                return
            elif arg1 in ["bat", "on", "1", "mo", "enable", "chay"]:
                self.config["auto_forward_excess"] = True
                self.save_config()
                self.log(f"{sender_label} đã BẬT chức năng cân chuyển cược", "INFO")
                mode_txt = "Tức thì (instant)" if self.config.get("mode") == "instant" else "Gom bảng (batch)"
                self.send_telegram_message(str(chat_id), f"🟢 <b>Thành công:</b> Đã <b>BẬT</b> chức năng cân chuyển cược tự động!\n\n💡 <i>Chế độ hiện tại: <b>{mode_txt}</b>. Cược của khách vượt định mức giữ lại sẽ được tự động cân và chuyển sang người nhận.\n👉 Để tắt: gõ <code>/canchuyen tat</code></i>")
                return

            sub_type = parts[1].lower().replace("%", "percentage").replace("phantram", "percentage").replace("pt", "percentage")
            if sub_type in ["percentage", "percent", "tien", "money", "k"]:
                r_type = "percentage" if sub_type in ["percentage", "percent"] else "money"
                r = self.config.setdefault("retain_config", {})
                r["retain_type"] = r_type
                nums = []
                for p in parts[2:]:
                    val_str = p.replace(",", ".").replace("%", "").replace("k", "").replace("d", "").replace("đ", "")
                    try:
                        nums.append(float(val_str))
                    except ValueError:
                        pass
                if len(nums) >= 1: r["retain_de"] = nums[0]
                if len(nums) >= 2: r["retain_lo"] = nums[1]
                if len(nums) >= 3: r["retain_3c"] = nums[2]
                if len(nums) >= 4: r["retain_x"] = nums[3]

                self.balancer.config = r
                self.save_config()
                unit = "%" if r_type == "percentage" else "k/đ"
                self.log(f"Đã cập nhật mức giữ lại ({r_type}): Đề={r.get('retain_de')}{unit}, Lô={r.get('retain_lo')}{unit}, 3C={r.get('retain_3c')}{unit}, X={r.get('retain_x')}{unit}", "INFO")
                reply = (
                    f"✅ <b>Thành công:</b> Đã cập nhật mức giữ lại theo <b>{'Phần trăm (%)' if r_type == 'percentage' else 'Tiền (k/đ)'}</b>:\n"
                    f"• Đề: <code>{r.get('retain_de', 0):g}{unit}</code> | Lô: <code>{r.get('retain_lo', 0):g}{unit}</code> | 3C: <code>{r.get('retain_3c', 0):g}{unit}</code> | Xiên: <code>{r.get('retain_x', 0):g}{unit}</code>"
                )
                self.send_telegram_message(str(chat_id), reply)
                return

            target_item = parts[1].lower()
            if target_item in ["de", "đề", "lo", "lô", "3c", "3cang", "3_cang", "xien", "x"]:
                if len(parts) >= 3:
                    val_raw = parts[2].lower()
                    is_pct_explicit = "%" in val_raw
                    val_clean = val_raw.replace(",", ".").replace("%", "").replace("k", "").replace("d", "").replace("đ", "")
                    try:
                        v = float(val_clean)
                        r = self.config.setdefault("retain_config", {})
                        if is_pct_explicit:
                            r["retain_type"] = "percentage"
                        key_map = {"de": "retain_de", "đề": "retain_de", "lo": "retain_lo", "lô": "retain_lo", "3c": "retain_3c", "3cang": "retain_3c", "3_cang": "retain_3c", "xien": "retain_x", "x": "retain_x"}
                        cfg_k = key_map[target_item]
                        r[cfg_k] = v
                        self.balancer.config = r
                        self.save_config()
                        u = "%" if r.get("retain_type") == "percentage" else ("đ" if "lo" in target_item else "k")
                        self.send_telegram_message(str(chat_id), f"✅ <b>Thành công:</b> Đã cập nhật giữ <b>{target_item.upper()}</b> = <code>{v:g}{u}</code>!")
                        return
                    except ValueError:
                        pass

            nums = []
            for p in parts[1:]:
                val_str = p.replace(",", ".").replace("%", "").replace("k", "").replace("d", "").replace("đ", "")
                try:
                    nums.append(float(val_str))
                except ValueError:
                    pass
            if len(nums) >= 2:
                r = self.config.setdefault("retain_config", {})
                if any("%" in p for p in parts[1:]):
                    r["retain_type"] = "percentage"
                if len(nums) >= 1: r["retain_de"] = nums[0]
                if len(nums) >= 2: r["retain_lo"] = nums[1]
                if len(nums) >= 3: r["retain_3c"] = nums[2]
                if len(nums) >= 4: r["retain_x"] = nums[3]
                self.balancer.config = r
                self.save_config()
                unit = "%" if r.get("retain_type") == "percentage" else "k/đ"
                reply = (
                    f"✅ <b>Thành công:</b> Đã cập nhật mức giữ lại:\n"
                    f"• Đề: <code>{r.get('retain_de', 0):g}{unit}</code> | Lô: <code>{r.get('retain_lo', 0):g}{unit}</code> | 3C: <code>{r.get('retain_3c', 0):g}{unit}</code> | Xiên: <code>{r.get('retain_x', 0):g}{unit}</code>"
                )
                self.send_telegram_message(str(chat_id), reply)
                return

            self.send_telegram_message(str(chat_id), "⚠️ Cú pháp: <code>/giulai tien 20 5 0 0</code> hoặc <code>/giulai % 50 50 0 0</code> hoặc <code>/giulai de 30k</code>")
            return

        # J. /toida hoặc /nhanh (Cài đặt mức trần tối đa khi giữ % hoặc mức nhánh khi giữ tiền)
        if cmd_root in ["/toida", "toida", "/nhanh", "nhanh"] or cmd_root.startswith("/toida@") or cmd_root.startswith("/nhanh@"):
            if not self.is_admin(chat_id):
                self.send_telegram_message(str(chat_id), self.msg_need_auth())
                return
            r = self.config.setdefault("retain_config", {})
            is_pct = (r.get("retain_type") == "percentage")
            title = "Mức trần Tối đa (k/đ)" if is_pct else "Mức Nhánh"

            if len(parts) >= 2:
                arg1 = parts[1].lower()
                if arg1 in ["tat", "off", "0", "huy", "dong"]:
                    r["retain_use_branch"] = False
                    self.balancer.config = r
                    self.save_config()
                    self.send_telegram_message(str(chat_id), f"✅ Đã <b>TẮT</b> cấu hình {title}.")
                    return
                elif arg1 in ["bat", "on", "1", "mo"]:
                    r["retain_use_branch"] = True
                    self.balancer.config = r
                    self.save_config()
                    self.send_telegram_message(str(chat_id), f"✅ Đã <b>BẬT</b> cấu hình {title}.")
                    return

                # Check if numbers: /toida 20 5 0 0
                nums = []
                for p in parts[1:]:
                    val_str = p.replace(",", ".").replace("%", "").replace("k", "").replace("d", "").replace("đ", "")
                    try:
                        nums.append(float(val_str))
                    except ValueError:
                        pass
                if len(nums) >= 1:
                    r["retain_use_branch"] = True
                    if len(nums) >= 1: r["branch_de"] = nums[0]
                    if len(nums) >= 2: r["branch_lo"] = nums[1]
                    if len(nums) >= 3: r["branch_3c"] = nums[2]
                    if len(nums) >= 4: r["branch_x"] = nums[3]
                    self.balancer.config = r
                    self.save_config()
                    reply = (
                        f"✅ <b>Thành công:</b> Đã BẬT và cập nhật <b>{title}</b>:\n"
                        f"• Đề: <code>{r.get('branch_de', 0):g}k</code> | Lô: <code>{r.get('branch_lo', 0):g}đ</code> | 3C: <code>{r.get('branch_3c', 0):g}k</code> | Xiên: <code>{r.get('branch_x', 0):g}k</code>"
                    )
                    self.send_telegram_message(str(chat_id), reply)
                    return

            cur_st = "BẬT" if r.get("retain_use_branch") else "TẮT"
            self.send_telegram_message(str(chat_id), (
                f"⚙️ <b>{title} hiện tại:</b> <code>{cur_st}</code>\n"
                f"• Đề: <code>{r.get('branch_de', 0):g}k</code> | Lô: <code>{r.get('branch_lo', 0):g}đ</code> | 3C: <code>{r.get('branch_3c', 0):g}k</code> | Xiên: <code>{r.get('branch_x', 0):g}k</code>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"👉 Để sửa: <code>/toida &lt;đề&gt; &lt;lô&gt; &lt;3c&gt; &lt;xiên&gt;</code> (VD: <code>/toida 20 5 0 0</code>)\n"
                f"👉 Hoặc: <code>/toida bat</code> / <code>/toida tat</code>"
            ))
            return

        # K1. /nguoinhan hoặc /chuyensang (Xem/Thêm/Sửa/Xóa người nhận cược thừa - Yêu cầu mật khẩu)
        if cmd_root in ["/chuyensang", "chuyensang", "/nguoinhan", "nguoinhan"] or cmd_root.startswith("/chuyensang@") or cmd_root.startswith("/nguoinhan@"):
            if not self.is_admin(chat_id):
                self.send_telegram_message(str(chat_id), self.msg_need_auth())
                return
            if len(parts) >= 2:
                sub = parts[1].strip()
                if sub.lower() in ["xoa", "huy", "tat", "delete", "clear", "none", "0"]:
                    self.config["target_recipient"] = ""
                    self.save_config()
                    self.log(f"{sender_label} đã xóa người nhận cược thừa", "INFO")
                    self.send_telegram_message(str(chat_id), "🗑️ <b>Thành công:</b> Đã <b>XÓA</b> người nhận cược thừa!\n<i>(Hệ thống sẽ không bắn cược thừa sang bất kỳ ai cho đến khi bạn cài đặt lại).</i>")
                    return
                else:
                    target = sub
                    # Phân giải thử xem có trong danh bạ không để hiển thị thông tin chi tiết
                    resolved_id, _ = self.resolve_recipient(target)
                    display_target = target
                    if resolved_id and target.startswith("@"):
                        display_target = f"{target} (Chat ID: <code>{resolved_id}</code>)"
                    elif resolved_id:
                        self.known_users = self.load_known_users()
                        user_match = self.known_users.get(str(resolved_id))
                        if user_match and user_match.get("username"):
                            display_target = f"@{user_match['username']} (Chat ID: <code>{resolved_id}</code>)"
                        elif user_match and user_match.get("first_name"):
                            display_target = f"{user_match['first_name']} (Chat ID: <code>{resolved_id}</code>)"
                        else:
                            display_target = f"<code>{target}</code>"

                    self.config["target_recipient"] = target
                    self.save_config()
                    self.log(f"{sender_label} đã đổi người nhận cược thừa sang: {target}", "INFO")
                    self.send_telegram_message(str(chat_id), f"✅ <b>Thành công:</b> Đã cài đặt người nhận cược thừa là:\n👉 <b>{display_target}</b>\n\n💡 <i>Cược vượt định mức sẽ tự động được gửi tới người này.</i>")
                    return
            else:
                cur = self.config.get("target_recipient", "").strip()
                if not cur:
                    cur_display = "<i>(Chưa cài đặt)</i>"
                else:
                    resolved_id, _ = self.resolve_recipient(cur)
                    if resolved_id and cur.startswith("@"):
                        cur_display = f"{cur} (Chat ID: <code>{resolved_id}</code>)"
                    elif resolved_id:
                        self.known_users = self.load_known_users()
                        user_match = self.known_users.get(str(resolved_id))
                        if user_match and user_match.get("username"):
                            cur_display = f"@{user_match['username']} (Chat ID: <code>{resolved_id}</code>)"
                        else:
                            cur_display = f"<code>{cur}</code>"
                    else:
                        cur_display = f"<code>{cur}</code>"

                reply = (
                    f"🛸 <b>NGƯỜI NHẬN CƯỢC THỪA (Thầu trên):</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"• <b>Hiện tại:</b> {cur_display}\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"👉 <b>Cài / Sửa người nhận:</b>\n"
                    f"  <code>/nguoinhan &lt;@username hoặc Chat ID&gt;</code>\n"
                    f"  <i>(Ví dụ: <code>/nguoinhan @lalalew</code> hoặc <code>/nguoinhan 7715286942</code>)</i>\n"
                    f"👉 <b>Xóa người nhận:</b> <code>/nguoinhan xoa</code>\n"
                    f"👉 <b>Xem danh bạ đã chat:</b> <code>/danhba</code>"
                )
                self.send_telegram_message(str(chat_id), reply)
                return

        # K2. /khach hoặc /nguoigui (Xem/Thêm/Sửa/Xóa khách được phép cược - Yêu cầu mật khẩu)
        if cmd_root in ["/khach", "khach", "/nguoigui", "nguoigui", "/khachhang", "khachhang"] or cmd_root.startswith("/khach@") or cmd_root.startswith("/nguoigui@"):
            if not self.is_admin(chat_id):
                self.send_telegram_message(str(chat_id), self.msg_need_auth())
                return

            self.known_users = self.load_known_users()
            allowed = self.config.setdefault("allowed_senders", ["*"])

            if len(parts) >= 2:
                sub_action = parts[1].lower()

                # A. Nhận từ tất cả mọi người
                if sub_action in ["tatca", "all", "*", "tat_ca", "reset", "mohet"]:
                    self.config["allowed_senders"] = ["*"]
                    self.save_config()
                    self.log(f"{sender_label} đã mở nhận cược từ TẤT CẢ mọi người (*)", "INFO")
                    self.send_telegram_message(str(chat_id), "🌐 <b>Thành công:</b> Đã chuyển sang chế độ <b>NHẬN CƯỢC TỪ TẤT CẢ MỌI NGƯỜI (*)</b>.\nBất kỳ ai nhắn tin cược đúng cú pháp bot đều sẽ nhận.")
                    return

                # B. Thêm khách
                if sub_action in ["them", "add", "+", "t"]:
                    if len(parts) < 3:
                        self.send_telegram_message(str(chat_id), "⚠️ Cú pháp: <code>/khach them &lt;@username hoặc Chat ID&gt;</code>\nVí dụ: <code>/khach them @Zeng86</code> hoặc <code>/khach them 1023927138</code>")
                        return
                    new_user = parts[2].strip()
                    allowed_clean = [x for x in allowed if x != "*"]
                    exists = any(x.lower().lstrip("@") == new_user.lower().lstrip("@") for x in allowed_clean)
                    if exists:
                        self.send_telegram_message(str(chat_id), f"⚠️ Khách <b>{new_user}</b> đã có trong danh sách từ trước!")
                        return
                    allowed_clean.append(new_user)
                    self.config["allowed_senders"] = allowed_clean
                    self.save_config()
                    self.log(f"{sender_label} đã thêm khách: {new_user}", "INFO")
                    self.send_telegram_message(str(chat_id), f"✅ <b>Thành công:</b> Đã thêm khách <b>{new_user}</b> vào danh sách cho phép!\n👉 Hiện có: <b>{len(allowed_clean)}</b> khách được chỉ định.")
                    return

                # C. Xóa khách
                if sub_action in ["xoa", "del", "remove", "-", "x"]:
                    if len(parts) < 3:
                        self.send_telegram_message(str(chat_id), "⚠️ Cú pháp: <code>/khach xoa &lt;@username hoặc Chat ID&gt;</code>\nVí dụ: <code>/khach xoa @Zeng86</code>")
                        return
                    target_del = parts[2].strip().lower().lstrip("@")
                    allowed_clean = [x for x in allowed if x.lower().lstrip("@") != target_del]
                    if len(allowed_clean) == len(allowed):
                        self.send_telegram_message(str(chat_id), f"⚠️ Không tìm thấy khách <b>{parts[2]}</b> trong danh sách!")
                        return
                    if len(allowed_clean) == 0:
                        allowed_clean = ["*"]
                        note = "Danh sách trống nên tự động chuyển về nhận từ TẤT CẢ (*)."
                    else:
                        note = f"Hiện còn <b>{len(allowed_clean)}</b> khách được chỉ định."
                    self.config["allowed_senders"] = allowed_clean
                    self.save_config()
                    self.log(f"{sender_label} đã xóa khách: {parts[2]}", "INFO")
                    self.send_telegram_message(str(chat_id), f"🗑️ <b>Thành công:</b> Đã xóa khách <b>{parts[2]}</b> khỏi danh sách!\n{note}")
                    return

            # Hiển thị danh sách khách hiện tại
            is_all = ("*" in allowed or not allowed)
            lines = [
                "👥 <b>DANH SÁCH KHÁCH ĐƯỢC PHÉP ĐẶT CƯỢC:</b>",
                "━━━━━━━━━━━━━━━━━━"
            ]
            if is_all:
                lines.append("🌐 <b>Chế độ:</b> <code>Nhận từ TẤT CẢ mọi người (*)</code>")
                lines.append("<i>Bất kỳ ai nhắn tin cược bot cũng sẽ nhận & phân tích.</i>")
            else:
                lines.append(f"🔒 <b>Chỉ nhận từ {len(allowed)} khách được chỉ định:</b>")
                for idx, u in enumerate(allowed, start=1):
                    u_clean = str(u).lstrip("@").lower()
                    info_extra = ""
                    for uid, k_info in self.known_users.items():
                        if str(uid) == u_clean or (k_info.get("username") or "").lower() == u_clean:
                            fn = k_info.get("first_name", "")
                            un = f"@{k_info.get('username')}" if k_info.get("username") else ""
                            info_extra = f" <i>({fn} {un} - ID: {uid})</i>"
                            break
                    lines.append(f"<b>{idx}.</b> <code>{u}</code>{info_extra}")

            lines.append("━━━━━━━━━━━━━━━━━━")
            lines.append("📝 <b>LỆNH QUẢN LÝ KHÁCH HÀNG:</b>")
            lines.append("• <b>Thêm khách:</b> <code>/khach them &lt;@username hoặc ID&gt;</code>")
            lines.append("  <i>(Ví dụ: <code>/khach them @Zeng86</code> hoặc <code>/khach them 1023927138</code>)</i>")
            lines.append("• <b>Xóa khách:</b> <code>/khach xoa &lt;@username hoặc ID&gt;</code>")
            lines.append("• <b>Nhận từ tất cả:</b> <code>/khach tatca</code>")
            lines.append("• <b>Xem danh bạ đã chat:</b> <code>/danhba</code>")

            self.send_telegram_message(str(chat_id), "\n".join(lines))
            return

        # K3. /danhba (Xem danh sách những người đã từng mở chat hoặc bấm /start với Bot - Yêu cầu mật khẩu)
        if cmd_root in ["/danhba", "danhba", "/users", "users"] or cmd_root.startswith("/danhba@"):
            if not self.is_admin(chat_id):
                self.send_telegram_message(str(chat_id), self.msg_need_auth())
                return

            self.known_users = self.load_known_users()
            if not self.known_users:
                self.send_telegram_message(str(chat_id), "📒 <b>Danh bạ Bot:</b> Chưa có người dùng nào bấm /start với Bot.\n👉 Hãy bảo khách hoặc người nhận mở Bot bấm <b>/start</b> trước.")
                return

            lines = [
                f"📒 <b>DANH BẠ NGƯỜI DÙNG ĐÃ CHAT VỚI BOT ({len(self.known_users)} người):</b>",
                "━━━━━━━━━━━━━━━━━━"
            ]
            for idx, (uid, info) in enumerate(self.known_users.items(), start=1):
                name = info.get("first_name") or "Khách"
                uname = f"@{info.get('username')}" if info.get("username") else "(Không có username)"
                cid = info.get("chat_id", uid)
                t = info.get("updated_at", "")
                t_str = f" <i>({t})</i>" if t else ""
                lines.append(
                    f"<b>{idx}. {name}</b> - {uname}\n"
                    f"   🆔 Chat ID: <code>{cid}</code>{t_str}"
                )

            lines.append("━━━━━━━━━━━━━━━━━━")
            lines.append("💡 <i>Sao chép Chat ID hoặc @username để:</i>\n"
                         "• Đặt người nhận cược: <code>/nguoinhan &lt;ID hoặc @user&gt;</code>\n"
                         "• Thêm khách cho phép: <code>/khach them &lt;ID hoặc @user&gt;</code>")

            self.send_telegram_message(str(chat_id), "\n".join(lines))
            return

        # K. /chedo (Đổi chế độ chuyển tức thì / gom bảng - Yêu cầu mật khẩu)
        if cmd_root in ["/chedo", "chedo", "/mode", "mode"] or cmd_root.startswith("/chedo@"):
            if not self.is_admin(chat_id):
                self.send_telegram_message(str(chat_id), self.msg_need_auth())
                return
            if len(parts) >= 2:
                m_str = parts[1].lower()
                if m_str in ["tucthi", "tuc_thi", "instant", "nhanh", "1"]:
                    self.config["mode"] = "instant"
                    self.save_config()
                    self.send_telegram_message(str(chat_id), "✅ Đã chuyển sang chế độ <b>Chuyển tức thì (instant)</b>: Cược vượt định mức sẽ được bắn ngay lập tức.")
                    return
                elif m_str in ["gomban", "gom_ban", "batch", "gom", "2"]:
                    self.config["mode"] = "batch"
                    self.save_config()
                    self.send_telegram_message(str(chat_id), "✅ Đã chuyển sang chế độ <b>Gom bảng (batch)</b>: Chỉ bắn cược khi bạn gõ lệnh /chuyen hoặc bấm nút trên Web.")
                    return
            cur_m = "Tức thì (instant)" if self.config.get("mode") == "instant" else "Gom bảng (batch)"
            self.send_telegram_message(str(chat_id), f"⚙️ Chế độ chuyển cược hiện tại: <b>{cur_m}</b>\n👉 Để đổi, gõ: <code>/chedo tucthi</code> hoặc <code>/chedo gomban</code>")
            return

        # L. /gia, /banggia (Xem bảng giá - Yêu cầu mật khẩu)
        if cmd in ["/gia", "gia", "/banggia", "bang gia", "/rates"] or cmd.startswith("/gia@"):
            if not self.is_admin(chat_id):
                self.send_telegram_message(str(chat_id), self.msg_need_auth())
                return
            summary_msg = format_price_config_summary(self.config.get("price_config"))
            self.send_telegram_message(str(chat_id), summary_msg)
            return

        # M. /giathau hoặc /giachuyen (Sửa bảng giá - Yêu cầu mật khẩu)
        if cmd_root in ["/giathau", "giathau", "/giachuyen", "giachuyen"] or cmd_root.startswith("/giathau@") or cmd_root.startswith("/giachuyen@"):
            if not self.is_admin(chat_id):
                self.send_telegram_message(str(chat_id), self.msg_need_auth())
                return
            is_chuyen = "chuyen" in cmd_root
            target_table = "chuyen" if is_chuyen else "thau"
            table_name = "Bảng Chuyển" if is_chuyen else "Bảng Thầu"

            if len(parts) >= 3:
                bet_k = parts[1].lower()
                p_cfg = self.config.setdefault("price_config", {})
                tbl_cfg = p_cfg.setdefault(target_table, {})

                try:
                    val1 = float(parts[2].replace(",", "."))
                    val2 = float(parts[3].replace(",", ".")) if len(parts) >= 4 else None

                    if bet_k in ["de", "đề", "d"]:
                        tbl_cfg["rateDeComm"] = val1
                        if val2 is not None: tbl_cfg["rateDePayout"] = val2
                        self.save_config()
                        self.send_telegram_message(str(chat_id), f"✅ Đã cập nhật <b>Đề ({table_name})</b>: Hoa hồng = <code>{tbl_cfg['rateDeComm']:g}%</code>, Trúng = <code>1 ăn {tbl_cfg.get('rateDePayout', 80):g}</code>")
                        return
                    elif bet_k in ["lo", "lô", "l"]:
                        cost_val = int(val1 * 100) if (0 < val1 < 100) else val1
                        tbl_cfg["rateLoCost"] = cost_val
                        if val2 is not None: tbl_cfg["rateLoPayout"] = val2
                        self.save_config()
                        self.send_telegram_message(str(chat_id), f"✅ Đã cập nhật <b>Lô ({table_name})</b>: Giá vốn = <code>{val1:g}k ({tbl_cfg['rateLoCost']}đ)</code>, Thưởng = <code>1 ăn {tbl_cfg.get('rateLoPayout', 80):g}k</code>")
                        return
                    elif bet_k in ["3c", "3cang", "3_cang", "cang", "bc"]:
                        tbl_cfg["rate3CComm"] = val1
                        if val2 is not None: tbl_cfg["rate3CPayout"] = val2
                        self.save_config()
                        self.send_telegram_message(str(chat_id), f"✅ Đã cập nhật <b>3 Càng ({table_name})</b>: Hoa hồng = <code>{tbl_cfg['rate3CComm']:g}%</code>, Trúng = <code>1 ăn {tbl_cfg.get('rate3CPayout', 400):g}</code>")
                        return
                    elif bet_k in ["xien", "xiên", "x"]:
                        tbl_cfg["rateXienComm"] = val1
                        self.save_config()
                        self.send_telegram_message(str(chat_id), f"✅ Đã cập nhật <b>Xiên ({table_name})</b>: Hoa hồng = <code>{tbl_cfg['rateXienComm']:g}%</code>")
                        return
                except ValueError:
                    pass

            prefix = "/giachuyen" if is_chuyen else "/giathau"
            self.send_telegram_message(str(chat_id), (
                f"⚠️ <b>Cú pháp chỉnh sửa {table_name}:</b>\n"
                f"• Đề: <code>{prefix} de &lt;hoa_hồng&gt; &lt;trúng&gt;</code> (VD: <code>{prefix} de 82 80</code>)\n"
                f"• Lô: <code>{prefix} lo &lt;vốn&gt; &lt;trúng&gt;</code> (VD: <code>{prefix} lo 21.65 80</code>)\n"
                f"• 3 Càng: <code>{prefix} 3c &lt;hoa_hồng&gt; &lt;trúng&gt;</code> (VD: <code>{prefix} 3c 75 400</code>)\n"
                f"• Xiên: <code>{prefix} xien &lt;hoa_hồng&gt;</code> (VD: <code>{prefix} xien 65</code>)"
            ))
            return

        # N. /chuyen (Yêu cầu mật khẩu)
        if cmd_root in ["/chuyen", "chuyen", "bắn cược", "ban cuoc"] or cmd_root.startswith("/chuyen@"):
            if not self.is_admin(chat_id):
                self.send_telegram_message(str(chat_id), self.msg_need_auth())
                return
            if len(parts) >= 2:
                arg1 = parts[1].lower()
                if arg1 in ["tat", "off", "0", "huy", "dong", "disable", "pause", "tamdung"]:
                    self.config["auto_forward_excess"] = False
                    self.save_config()
                    self.log(f"{sender_label} đã TẮT chức năng cân chuyển cược", "INFO")
                    self.send_telegram_message(str(chat_id), "🔴 <b>Thành công:</b> Đã <b>TẮT</b> chức năng cân chuyển cược tự động!\n\n💡 <i>Bot vẫn nhận tin của khách nhưng TẠM DỪNG bắn cược thừa sang thầu trên.\n👉 Gõ <code>/chuyen bat</code> để bật lại.</i>")
                    return
                elif arg1 in ["bat", "on", "1", "mo", "enable"]:
                    self.config["auto_forward_excess"] = True
                    self.save_config()
                    self.log(f"{sender_label} đã BẬT chức năng cân chuyển cược", "INFO")
                    mode_txt = "Tức thì (instant)" if self.config.get("mode") == "instant" else "Gom bảng (batch)"
                    self.send_telegram_message(str(chat_id), f"🟢 <b>Thành công:</b> Đã <b>BẬT</b> chức năng cân chuyển cược tự động!\n\n💡 <i>Chế độ hiện tại: <b>{mode_txt}</b>. Cược vượt mức giữ lại sẽ được tự động cân và chuyển sang người nhận.</i>")
                    return

            excess = self.balancer.calculate_excess()
            excess_count = sum(len(v) for v in excess.values())
            if excess_count == 0:
                self.send_telegram_message(str(chat_id), "🛡️ Hiện tại bảng cược không có số nào vượt định mức giữ lại.")
                return
            target_recipient = self.config.get("target_recipient", "").strip()
            if not target_recipient:
                self.send_telegram_message(str(chat_id), "⚠️ Chưa cài đặt người nhận cược thừa (target_recipient) trên Web hoặc bằng /chuyensang!")
                return
            transfer_msg = self.balancer.format_transfer_message(excess, header_prefix="Thầu Chuyển")
            ok, err = self.send_telegram_message(target_recipient, transfer_msg, track_for_cleanup=True, tag="transfer")
            if ok:
                self.balancer.commit_transfers(excess)
                self.stats["transfers_sent"] += 1
                self.pending_recipient_acks = {
                    "timestamp": time.time(),
                    "recipient": target_recipient,
                    "alerted": False
                }
                summary_txt = " ; ".join(transfer_msg.strip().splitlines())
                self.log(f"Gửi chủ thầu {target_recipient}: {summary_txt}", "INFO")
                note = "" if self.config.get("auto_forward_excess", True) else "\n<i>(Lưu ý: Tự động cân chuyển đang TẮT. Gõ <code>/chuyen bat</code> để bật tự động).</i>"
                self.send_telegram_message(str(chat_id), f"🚀 Đã bắn {excess_count} con cược thừa sang {target_recipient} thành công!{note}")
            else:
                self.send_telegram_message(str(chat_id), f"❌ Bắn cược thừa thất bại: {err}")
            return

        # O. /reset (Yêu cầu mật khẩu)
        if cmd in ["/reset", "reset", "xóa cược", "xoa cuoc"] or cmd.startswith("/reset@"):
            if not self.is_admin(chat_id):
                self.send_telegram_message(str(chat_id), self.msg_need_auth())
                return
            self.balancer.reset_board()
            self.client_msg_counters = {}
            self.client_bets = {}
            self.save_client_bets()
            self.pending_recipient_acks = None
            self.log("Đã reset bảng cược, số thứ tự tin và danh sách cược của khách về 0", "INFO")
            self.send_telegram_message(str(chat_id), "🗑️ Đã làm mới (reset) toàn bộ bảng cược, số thứ tự tin và danh sách cược của khách về 0 để bắt đầu ngày mới!")
            return

        # P. /clean (Yêu cầu mật khẩu)
        if cmd in ["/clean", "clean", "/xoadauvet", "xoa dau vet"] or cmd.startswith("/clean@"):
            if not self.is_admin(chat_id):
                self.send_telegram_message(str(chat_id), self.msg_need_auth())
                return
            del_c, rem_c = self.check_and_cleanup_traces()
            cur_h = round(self.config.get("cleanup_after_seconds", 86400) / 3600, 1)
            self.send_telegram_message(str(chat_id), f"🧹 Đã rà soát dấu vết: Xóa {del_c} tin nhắn cược cũ, hiện còn {rem_c} tin đang hẹn giờ tự động xóa (chu kỳ {cur_h:g} giờ).")
            return

        # Q. /timer (Yêu cầu mật khẩu)
        if cmd.startswith("/timer") or cmd.startswith("/thoigianxoa") or cmd.startswith("/gio "):
            if not self.is_admin(chat_id):
                self.send_telegram_message(str(chat_id), self.msg_need_auth())
                return
            if len(parts) >= 2:
                try:
                    hours = float(parts[1].replace(",", "."))
                    if hours <= 0:
                        self.send_telegram_message(str(chat_id), "⚠️ Số giờ phải lớn hơn 0.")
                        return
                    secs = int(hours * 3600)
                    self.config["cleanup_after_seconds"] = secs
                    self.config["cleanup_after_hours"] = hours
                    self.save_config()
                    self.log(f"Đã cập nhật thời gian tự động xóa dấu vết sang {hours:g} giờ", "INFO")
                    self.send_telegram_message(str(chat_id), f"⏰ <b>Thành công:</b> Đã cài đặt thời gian tự động xóa vết cược là <b>{hours:g} giờ</b>!")
                    return
                except ValueError:
                    self.send_telegram_message(str(chat_id), "⚠️ Cú pháp không hợp lệ. Ví dụ: <code>/timer 12</code> hoặc <code>/timer 6</code>")
                    return
            else:
                cur_h = round(self.config.get("cleanup_after_seconds", 86400) / 3600, 1)
                self.send_telegram_message(str(chat_id), f"⏰ Thời gian tự động xóa dấu vết hiện tại là: <b>{cur_h:g} giờ</b>.\nĐể đổi, hãy gõ ví dụ: <code>/timer 12</code> hoặc <code>/timer 6</code>")
                return

        # 1. Kiểm tra quyền của khách
        if not self.is_sender_allowed(user_id, username):
            self.log(f"⚠️ Từ chối tin từ '{sender_label}' (ID: {user_id}){group_title}: Người gửi KHÔNG có trong danh sách Khách chỉ định! (Để nhận từ tất cả, hãy điền dấu * vào ô Khách chỉ định)", "WARN")
            return

        # 1b. Kiểm tra nếu khách nhắn yêu cầu HỦY TIN:
        # Cách 1: Gõ lệnh có số tin (VD: "hủy tin 1", "huy tin 1", "hủy t1", "hủy 1", "xóa tin 1"...)
        # Cách 2: Reply lại tin nhắn cần hủy (tin gốc của khách hoặc tin 'Ok tin X' của bot) rồi nhắn "hủy", "huy", "bỏ", "xóa"...
        clean_no_accents = strip_accents(text).lower().strip()
        cancel_kw_match = re.search(
            r"^(?:huy|xoa|bo|cancel)\s*(?:tin\s*(?:cuoc\s*)?(?:so\s*)?|cuoc\s*|so\s*|don\s*|t\s*|\#\s*)?(\d*)(?:\s*(?:nay|di|nhe|nha|gium|giup|ho|oi|e|em|a|anh|c|chi|duoc\s*khong|duoc\s*ko|\.|\!))*$",
            clean_no_accents
        )

        reply_to = message.get("reply_to_message")

        if cancel_kw_match:
            num_str = cancel_kw_match.group(1).strip()
            target_idx = int(num_str) if num_str.isdigit() else None
            reply_mid = reply_to.get("message_id") if reply_to else None
            reply_text = (reply_to.get("text") or reply_to.get("caption") or "").strip() if reply_to else ""

            # Nếu reply tin nhắn "Ok tin 1..." của Bot:
            if target_idx is None and reply_text:
                ok_m = re.search(r"ok\s+tin\s+(\d+)", strip_accents(reply_text).lower())
                if ok_m:
                    target_idx = int(ok_m.group(1))

            self.log(f"Khách {sender_label}: {text} (Yêu cầu hủy tin - target_idx={target_idx}, reply_mid={reply_mid})", "INFO")
            res_cancel = self.cancel_client_bet_by_index(
                chat_id_str=str(chat_id),
                sender_label=sender_label,
                target_msg_idx=target_idx,
                target_msg_id=reply_mid,
                target_raw_text=reply_text
            )

            if res_cancel.get("success"):
                reply_out = res_cancel.get("client_reply", f"Đã hủy tin")
                self.send_telegram_message(str(chat_id), reply_out, track_for_cleanup=True, tag="cancel_receipt")
                return
            elif target_idx is not None or reply_to:
                reply_err = res_cancel.get("client_reply", "⚠️ Không tìm thấy tin cược cần hủy.")
                self.send_telegram_message(str(chat_id), reply_err, track_for_cleanup=True, tag="cancel_receipt")
                return
            else:
                self.send_telegram_message(
                    str(chat_id),
                    "⚠️ Vui lòng gõ số tin cần hủy (VD: <code>hủy tin 1</code>) hoặc <b>reply (trả lời)</b> lại tin cược rồi nhắn <code>hủy</code>."
                )
                return

        self.stats["messages_received"] += 1
        self.stats["last_active"] = datetime.now().strftime("%H:%M:%S")

        # 2. Phân tích cược
        user_msg_id = message.get("message_id")
        parsed = parse_bet_message(text)
        summary = parsed.get("summary", {})
        total_bets_count = summary.get("de_count", 0) + summary.get("lo_count", 0) + summary.get("bacang_count", 0) + summary.get("xien_count", 0)

        if total_bets_count == 0:
            if parsed.get('invalid_items') and self.config.get("auto_reply_client", True):
                unique_inv = list(dict.fromkeys(parsed['invalid_items']))
                joined_inv = " ".join(unique_inv) if all(x.isdigit() for x in unique_inv) else ", ".join(unique_inv)
                self.send_telegram_message(str(chat_id), f"Trả lại {joined_inv}", track_for_cleanup=True, tag="invalid_receipt")
                self.log(f"Khách {sender_label}: {text} (trả lại {joined_inv})", "WARN")
            else:
                self.log(f"Khách {sender_label}: {text}", "INFO")

            # Forward tin nhắn không phải cược sang cho Chủ Bot (nếu bật tùy chọn)
            if self.config.get("forward_client_to_owner", False):
                owner_cid = self.config.get("owner_chat_id")
                if owner_cid and str(chat_id) != str(owner_cid):
                    self.send_telegram_message(str(owner_cid), f"📩 Khách {sender_label}: {text}")
            return

        filter_return_msg = ""
        # 2b. Kiểm tra Bộ Lọc cược cấm nhận / trả lại khách
        if self.config.get("bet_filter_enabled", False) and self.config.get("bet_filter_keywords", "").strip():
            b2d, b3d = expand_filter_numbers(self.config.get("bet_filter_keywords", ""))
            if b2d or b3d:
                accepted, rejected = filter_parsed_bets(parsed, b2d, b3d)
                rej_summary = rejected.get("summary", {})
                rej_count = rej_summary.get("de_count", 0) + rej_summary.get("lo_count", 0) + rej_summary.get("bacang_count", 0) + rej_summary.get("xien_count", 0)

                if rej_count > 0:
                    filter_return_msg = format_rejected_receipt(rejected)
                    owner_cid = self.config.get("owner_chat_id")

                    acc_summary = accepted.get("summary", {})
                    acc_count = acc_summary.get("de_count", 0) + acc_summary.get("lo_count", 0) + acc_summary.get("bacang_count", 0) + acc_summary.get("xien_count", 0)

                    # Trường hợp 1: Tất cả các con cược đều bị lọc cấm nhận -> Trả lại toàn bộ ngay lập tức
                    if acc_count == 0:
                        self.send_telegram_message(str(chat_id), filter_return_msg, track_for_cleanup=True, tag="rejected_receipt")
                        self.log(f"Khách {sender_label}: {text} (Bộ lọc: Đã trả lại {filter_return_msg})", "WARN")
                        # Luôn forward thông báo trả lại về cho Chủ Bot
                        if owner_cid and str(chat_id) != str(owner_cid):
                            self.send_telegram_message(str(owner_cid), f"⚠️ [BỘ LỌC CƯỢC] Đã trả lại Khách {sender_label}:\n{filter_return_msg}\n(Tin gốc: {text})")
                        return

                    # Trường hợp 2: Có 1 phần bị lọc, các số còn lại hợp lệ
                    # Forward ngay cảnh báo các số bị lọc cho Chủ bot biết
                    if owner_cid and str(chat_id) != str(owner_cid):
                        self.send_telegram_message(str(owner_cid), f"⚠️ [BỘ LỌC CƯỢC] Khách {sender_label} có số bị lọc trả lại:\n{filter_return_msg}\n(Tin gốc: {text})")
                    self.log(f"Khách {sender_label}: {text} (Bộ lọc: Lọc ra {filter_return_msg})", "INFO")
                    parsed = accepted
                    summary = acc_summary
                    total_bets_count = acc_count

        # Lưu vết tin nhắn cược của khách để tự động xóa sau 24h
        if user_msg_id and chat_id:
            self.track_message(chat_id, user_msg_id, tag="incoming_bet")
            self.last_bet_timestamp = time.time()

        # KIỂM TRA TRẠNG THÁI BẬT/TẮT BOT:
        # Nếu bot đang TẮT (bot_mode == "manual"):
        # - Vẫn quản lý tin nhắn và lưu vào danh sách chờ duyệt để Chủ bot xem
        # - KHÔNG gửi Ok cho khách
        # - KHÔNG tự ý tính cược hay cân chuyển ngay
        # - Báo tin đến Chủ bot để xem và duyệt tin
        chat_id_str = str(chat_id)
        is_bot_auto = (self.config.get("bot_mode", "auto") == "auto")
        if not is_bot_auto:
            pending_item = self.add_pending_bet(
                chat_id_str=chat_id_str,
                sender_label=sender_label,
                username=username,
                parsed=parsed,
                raw_text=text,
                user_msg_id=user_msg_id,
                filter_return_msg=filter_return_msg
            )
            self.log(f"Khách {sender_label}: {text} (⚠️ Bot đang TẮT -> Tin #{pending_item['id']} ở trạng thái TREO chờ duyệt)", "WARN")
            self.notify_owner_new_pending_bet(pending_item)
            return

        # Nếu hôm nay đã chốt tiền xong mà có cược mới đến: tự động làm sạch bảng và bắt đầu ca mới từ Tin #1!
        if getattr(self, "is_settled_today", False):
            self.log("🌅 Có tin cược mới sau khi đã chốt tiền -> Bắt đầu bảng mới cho ngày tiếp theo (tính từ Tin #1)...", "INFO")
            self.balancer.reset_board()
            self.client_bets = {}
            self.save_client_bets()
            self.client_msg_counters = {}
            self.pending_bets = []
            self.save_pending_bets()
            self.pending_client_receipts = []
            self.last_web_bet_hash = None
            self.last_web_bet_text = ""
            self.last_bet_timestamp = None
            self.cached_kqxs = None
            self.is_settled_today = False

        # Tăng số thứ tự tin của khách (Ok tin 1, Ok tin 2...)
        msg_idx = self.client_msg_counters.get(chat_id_str, 0) + 1
        self.client_msg_counters[chat_id_str] = msg_idx

        # Ghi nhận cược theo từng khách để chốt tiền âm/dương khi có KQXS
        self.record_client_bet(chat_id_str, sender_label, username, parsed, raw_text=text, msg_index=msg_idx, user_msg_id=user_msg_id)
        hist_idx = len(self.client_bets[chat_id_str].get("history", [])) - 1

        # Forward tin nhắn cược của khách sang cho Chủ Bot (nếu bật tùy chọn)
        if self.config.get("forward_client_to_owner", False):
            owner_cid = self.config.get("owner_chat_id")
            if owner_cid and str(chat_id) != str(owner_cid):
                self.send_telegram_message(str(owner_cid), f"📩 Khách {sender_label} (tin #{msg_idx}): {text}")

        # 3. Cân bảng và tính phần cược thừa
        excess = self.balancer.add_bets(parsed)
        excess_count = sum(len(v) for v in excess.values())
        has_forwarded_excess = False

        # 4. Nếu có cược thừa và bật chế độ tự động bắn (instant)
        if self.config.get("auto_forward_excess", True) and self.config.get("mode", "instant") == "instant":
            if excess_count > 0:
                target_recipient = self.config.get("target_recipient", "").strip()
                if target_recipient:
                    transfer_msg = self.balancer.format_transfer_message(excess, header_prefix="Thầu")
                    success, err = self.send_telegram_message(target_recipient, transfer_msg, track_for_cleanup=True, tag="transfer")
                    if success:
                        self.balancer.commit_transfers(
                            excess,
                            client_chat_id=chat_id_str,
                            client_msg_idx=msg_idx,
                            client_history_idx=hist_idx
                        )
                        self.stats["transfers_sent"] += 1
                        self.pending_recipient_acks = {
                            "timestamp": time.time(),
                            "recipient": target_recipient,
                            "alerted": False
                        }
                        has_forwarded_excess = True
                        transfer_summary = " ; ".join(transfer_msg.strip().splitlines())
                        self.log(f"Gửi chủ thầu {target_recipient}: {transfer_summary}", "INFO")
                    else:
                        self.log(f"❌ Thất bại khi gửi cược thừa tới {target_recipient}: {err}", "ERROR")
                else:
                    self.log("⚠️ Có cược thừa nhưng chưa thiết lập người nhận (target_recipient)!", "WARN")
            else:
                self.log(f"🛡️ Toàn bộ cược nằm trong định mức giữ lại, không có cược thừa cần chuyển.", "INFO")

        # Lưu nội dung cân chuyển và giữ lại vào chi tiết tin khách
        single_transfer_txt = self.balancer.format_transfer_message(excess, include_header=False) if excess_count > 0 else ""
        single_retained_txt = self.balancer.calculate_retained_from_single_bet(parsed, excess)
        self.update_last_bet_transfer(chat_id_str, transfer_text=single_transfer_txt, retain_text=single_retained_txt)

        # 5. Phản hồi xác nhận cho khách (nếu bật):
        # - Nếu cược thừa ĐÃ CHUYỂN cho Chủ thầu: Bot chờ Chủ thầu Ok thì mới nhắn Ok lại cho khách!
        # - Nếu KHÔNG CÓ cược thừa (giữ lại 100%): Nhắn Ok tin X cho khách ngay lập tức.
        if self.config.get("auto_reply_client", True):
            receipt_text = format_ok_receipt(parsed, msg_idx)
            if filter_return_msg:
                receipt_text = f"{receipt_text}\n{filter_return_msg}"
            if has_forwarded_excess:
                self.pending_client_receipts.append({
                    "chat_id": chat_id_str,
                    "receipt_text": receipt_text,
                    "msg_idx": msg_idx,
                    "sender_label": sender_label,
                    "text": text,
                    "parsed": json.loads(json.dumps(parsed)),
                    "created_at": time.time()
                })
                self.log(f"Khách {sender_label}: {text} (Đã chuyển thầu {target_recipient}, chờ thầu Ok mới nhắn khách...)", "INFO")
            else:
                if len(receipt_text) > 3800:
                    chunks = [receipt_text[i:i+3800] for i in range(0, len(receipt_text), 3800)]
                    for chunk in chunks:
                        self.send_telegram_message(chat_id_str, chunk, track_for_cleanup=True, tag="receipt")
                else:
                    self.send_telegram_message(chat_id_str, receipt_text, track_for_cleanup=True, tag="receipt")
                self.log(f"Khách {sender_label}: {text} (Giữ lại 100% -> Đã nhắn lại Ok tin {msg_idx})", "INFO")
        else:
            self.log(f"Khách {sender_label}: {text}", "INFO")

    def get_client_price_config(self, chat_id: str, username: str = None) -> dict:
        """
        Lấy cấu hình bảng giá riêng cho khách cược.
        Nếu không có cấu hình riêng, trả về bảng giá Thầu mặc định.
        """
        custom_prices = self.config.get("client_prices", {})
        cid_clean = str(chat_id).strip()
        u_clean = str(username or "").strip().lstrip("@")

        # 1. Tìm theo Chat ID
        if cid_clean in custom_prices:
            return custom_prices[cid_clean]
        # 2. Tìm theo @username hoặc username
        if u_clean:
            if f"@{u_clean}" in custom_prices:
                return custom_prices[f"@{u_clean}"]
            if u_clean in custom_prices:
                return custom_prices[u_clean]

        # 3. Mặc định dùng bảng giá chung
        return self.config.get("price_config", DEFAULT_PRICE_CONFIG)

    def settle_all(self, kqxs: dict, notify_clients: bool = True, notify_recipient: bool = True, notify_owner: bool = True, requested_by: str = None) -> dict:
        """
        Chốt tiền âm/dương tự động hoặc thủ công dựa trên KQXS.
        - Gửi tin nhắn chốt tiền riêng cho từng khách cược đã đánh trong ngày (theo bảng giá riêng của khách nếu có).
        - Gửi tin nhắn chốt tiền bảng Chuyển cho người nhận cược thừa (thầu trên).
        - Gửi báo cáo tổng hợp (Thầu, Chuyển, Giữ lại) cho Chủ bảng (owner).
        """
        date_str = kqxs.get("date", datetime.now().strftime("%d/%m/%Y"))
        price_cfg = self.config.get("price_config", DEFAULT_PRICE_CONFIG)
        acc = calculate_board_accounting(self.balancer, kqxs, price_cfg)

        settled_clients = 0
        client_reports = {}

        # 1. Tính toán và gửi chốt tiền cho từng khách cược
        for cid_str, cdata in list(self.client_bets.items()):
            c_price_cfg = self.get_client_price_config(cid_str, cdata.get("username"))
            c_res = calculate_single_client_accounting(cdata, kqxs, c_price_cfg)
            c_acc = c_res["accounting"]
            if c_acc.get("totalVon", 0) > 0:
                c_msg = c_res["report_text"]
                client_reports[cid_str] = {
                    "name": cdata.get("name"),
                    "username": cdata.get("username"),
                    "accounting": c_acc,
                    "text": c_msg
                }
                if notify_clients:
                    ok, err = self.send_telegram_message(cid_str, c_msg, track_for_cleanup=True, tag="client_settlement")
                    if ok:
                        settled_clients += 1
                        cdata["last_settled"] = date_str
                        self.log(f"🎯 Đã gửi chốt tiền tới khách {cdata.get('name')} ({cid_str}):\n{c_msg}", "SUCCESS")
                    else:
                        self.log(f"❌ Lỗi gửi chốt tiền tới khách {cid_str}: {err}", "WARN")
                else:
                    settled_clients += 1
        if notify_clients:
            self.save_client_bets()

        # 2. Gửi chốt tiền cho Người nhận cược thừa (target_recipient)
        rec_report_text = ""
        target_recipient = self.config.get("target_recipient", "").strip()
        if notify_recipient and target_recipient:
            chuyen_data = acc.get("chuyen", {})
            if chuyen_data.get("totalVon", 0) > 0:
                rec_report_text = format_accounting_report(acc, "chuyen")
                ok, err = self.send_telegram_message(target_recipient, rec_report_text, track_for_cleanup=True, tag="recipient_settlement")
                if ok:
                    self.log(f"🎯 Đã gửi chốt tiền bảng Chuyển tới người nhận {target_recipient}:\n{rec_report_text}", "SUCCESS")
                else:
                    self.log(f"❌ Lỗi gửi chốt tiền tới người nhận {target_recipient}: {err}", "WARN")

        # 3. Gửi Báo Cáo Tổng Hợp cho Chủ Bảng (owner)
        owner_chat_id = self.config.get("owner_chat_id", "").strip() or requested_by
        thau_rep = format_accounting_report(acc, "thau")
        giulai_rep = format_accounting_report(acc, "giulai")
        chuyen_rep = format_accounting_report(acc, "chuyen")

        full_summary = (
            f"🏆 <b>BÁO CÁO TỔNG KẾT NGÀY - {date_str}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📋 <b>BẢNG THẦU (Nhận khách):</b>\n{thau_rep}\n\n"
            f"🛸 <b>BẢNG CHUYỂN (Thầu trên):</b>\n{chuyen_rep}\n\n"
            f"🛡️ <b>BẢNG GIỮ LẠI (Thực thu):</b>\n{giulai_rep}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👑 Đề: <b>{kqxs.get('special_last2', '--')}</b> | 3C: <b>{kqxs.get('special_last3', '---')}</b>\n"
            f"👥 <i>Đã gửi bảng chốt tiền tới: {settled_clients} khách cược.</i>"
        )

        if notify_owner and owner_chat_id:
            self.send_telegram_message(str(owner_chat_id), full_summary, track_for_cleanup=True, tag="daily_report")
            self.log(f"Đã gửi báo cáo tổng kết ngày {date_str} tới chủ bảng ({owner_chat_id})", "SUCCESS")

        client_messages = []
        for cid_str, rep in client_reports.items():
            acc_c = rep.get("accounting", {})
            net_val = acc_c.get("totalNet", 0)
            client_messages.append({
                "chat_id": cid_str,
                "sender": cid_str,
                "name": rep.get("name") or (f"@{rep.get('username')}" if rep.get("username") else f"Khách {cid_str}"),
                "client_name": rep.get("name"),
                "username": rep.get("username"),
                "text": rep.get("text"),
                "message": rep.get("text"),
                "net": net_val,
                "accounting": acc_c
            })

        db_prize = str(kqxs.get("special_prize") or "").strip()
        db_de = str(kqxs.get("special_last2") or "").strip()
        db_c3 = str(kqxs.get("special_last3") or "").strip()
        if not db_de and len(db_prize) >= 2:
            db_de = db_prize[-2:]
        if not db_c3 and len(db_prize) >= 3:
            db_c3 = db_prize[-3:]

        db_display = f"{db_prize} (Đề: {db_de})" if db_prize and db_de else (db_prize or db_de or "N/A")

        return {
            "success": True,
            "date": date_str,
            "kqxs_date": kqxs.get("date") or date_str,
            "kqxs_special": db_display,
            "special_prize": db_prize,
            "special_last2": db_de,
            "special_last3": db_c3,
            "accounting": acc,
            "settled_clients_count": settled_clients or len(client_messages),
            "client_reports": client_reports,
            "client_messages": client_messages,
            "client_texts": client_messages,
            "summary_text": full_summary,
            "thau_report": thau_rep,
            "chuyen_report": chuyen_rep,
            "giulai_report": giulai_rep,
            "full_summary": full_summary
        }

    def save_daily_archive(self, date_str: str, settle_result: dict = None, kqxs: dict = None):
        """
        Lưu trữ toàn bộ dữ liệu cược, tin nhắn gốc, lần chuyển thầu và báo cáo chốt tiền theo ngày.
        Giúp chủ bảng luôn có thể tra cứu và đối soát lại bất kỳ lúc nào khách hoặc thầu thắc mắc.
        """
        try:
            archive_dir = os.path.join(os.path.dirname(__file__), "daily_history")
            os.makedirs(archive_dir, exist_ok=True)

            # Chuẩn hóa tên file YYYY-MM-DD
            clean_date = date_str.replace("/", "-").strip() if date_str else now_vn().strftime("%Y-%m-%d")
            parts = clean_date.split("-")
            if len(parts) == 3 and len(parts[0]) == 2 and len(parts[2]) == 4:
                clean_date = f"{parts[2]}-{parts[1]}-{parts[0]}"

            file_path = os.path.join(archive_dir, f"{clean_date}.json")

            archive_data = {
                "date": clean_date,
                "display_date": date_str,
                "saved_at": now_vn().strftime("%H:%M:%S %d/%m/%Y"),
                "client_bets": json.loads(json.dumps(self.client_bets)),
                "raw_messages": self.get_all_raw_messages(),
                "transfer_history": json.loads(json.dumps(self.balancer.transfer_history)),
                "board": {
                    "de_sums": dict(self.balancer.de_sums),
                    "lo_sums": dict(self.balancer.lo_sums),
                    "bacang_sums": dict(self.balancer.bacang_sums),
                    "xien_bets": list(self.balancer.xien_bets),
                    "retained": self.balancer.get_retained_bets()
                },
                "settle_result": settle_result or {},
                "kqxs": kqxs or getattr(self, "cached_kqxs", {})
            }

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(archive_data, f, ensure_ascii=False, indent=2)

            self.log(f"💾 Đã lưu trữ an toàn toàn bộ dữ liệu cược & chốt tiền ngày {clean_date} để tra cứu đối soát.", "SUCCESS")
        except Exception as e:
            self.log(f"⚠️ Lỗi lưu trữ lịch sử ngày: {e}", "WARN")

    def cleanup_old_history(self, hours: float = None) -> int:
        """Tự động dọn dẹp các file lịch sử cược cũ trong daily_history/ vượt quá số tiếng (giờ) chỉ định.
        Nếu hours == 0: Giữ vĩnh viễn (không xóa).
        """
        if hours is not None:
            retention_hours = float(hours)
        elif "history_retention_hours" in self.config:
            retention_hours = float(self.config.get("history_retention_hours", 36.0))
        elif "history_retention_days" in self.config:
            retention_hours = float(self.config.get("history_retention_days", 1.5)) * 24.0
        else:
            retention_hours = 36.0

        if retention_hours <= 0:
            return 0  # 0: Giữ vĩnh viễn không xóa

        archive_dir = os.path.join(os.path.dirname(__file__), "daily_history")
        if not os.path.exists(archive_dir):
            return 0

        deleted_count = 0
        now_ts = now_vn().timestamp()
        retention_seconds = retention_hours * 3600.0

        try:
            for fname in os.listdir(archive_dir):
                if not fname.endswith(".json"):
                    continue
                file_p = os.path.join(archive_dir, fname)

                # Xác định thời điểm lưu trữ của file (từ saved_at hoặc file mtime)
                file_ts = None
                try:
                    with open(file_p, "r", encoding="utf-8") as f:
                        fdata = json.load(f)
                    saved_at_str = fdata.get("saved_at", "")
                    if saved_at_str:
                        # format ví dụ: "22:17:14 17/09/2026"
                        dt = datetime.strptime(saved_at_str, "%H:%M:%S %d/%m/%Y").replace(tzinfo=VN_TZ)
                        file_ts = dt.timestamp()
                except Exception:
                    pass

                if not file_ts:
                    try:
                        file_ts = os.path.getmtime(file_p)
                    except Exception:
                        continue

                age_seconds = now_ts - file_ts
                if age_seconds >= retention_seconds:
                    try:
                        os.remove(file_p)
                        deleted_count += 1
                        self.log(f"🗑️ [Tự động xóa lịch sử > {retention_hours:g} tiếng] Đã dọn file {fname} (đã lưu được {round(age_seconds/3600, 1):g} tiếng).", "INFO")
                    except Exception as fe:
                        self.log(f"Lỗi khi xóa file {fname}: {fe}", "WARN")
        except Exception as e:
            self.log(f"Lỗi quét dọn dẹp thư mục lịch sử: {e}", "WARN")

        return deleted_count

    def load_daily_archive(self, date_str: str) -> dict:
        """Đọc lại dữ liệu lưu trữ của một ngày cũ để đối soát khi có thắc mắc"""
        try:
            archive_dir = os.path.join(os.path.dirname(__file__), "daily_history")
            if not os.path.exists(archive_dir):
                return None

            clean_date = date_str.replace("/", "-").strip() if date_str else ""
            parts = clean_date.split("-")
            if len(parts) == 3 and len(parts[0]) == 2 and len(parts[2]) == 4:
                clean_date = f"{parts[2]}-{parts[1]}-{parts[0]}"

            candidates = [
                os.path.join(archive_dir, f"{clean_date}.json"),
                os.path.join(archive_dir, f"{date_str.replace('/', '-')}.json")
            ]
            for p in candidates:
                if os.path.exists(p):
                    with open(p, "r", encoding="utf-8") as f:
                        return json.load(f)
        except Exception as e:
            self.log(f"Lỗi đọc file lịch sử ngày {date_str}: {e}", "WARN")
        return None

    def get_settle_status(self, date_str: str = None) -> dict:
        """Kiểm tra trạng thái chốt tiền của ngày chỉ định hoặc ngày hôm nay.
        Trả về dict:
        {
            "is_settled": True/False,
            "settled_date": "17/09/2026",
            "settled_at": "22:52:54 17/09/2026",
            "settled_time": "22:52:54",
            "net_profit": 1000 or None
        }
        """
        now = now_vn()
        today_clean = now.strftime("%Y-%m-%d")
        today_vn = now.strftime("%d/%m/%Y")
        
        target = date_str or getattr(self, "current_date", "") or today_clean
        clean_target = target.replace("/", "-").strip()
        parts = clean_target.split("-")
        if len(parts) == 3 and len(parts[0]) == 2 and len(parts[2]) == 4:
            clean_target = f"{parts[2]}-{parts[1]}-{parts[0]}"
        
        disp_date = f"{parts[2]}/{parts[1]}/{parts[0]}" if (len(parts) == 3 and len(parts[0]) == 4) else (
            f"{parts[0]}/{parts[1]}/{parts[2]}" if (len(parts) == 3 and len(parts[2]) == 4) else target
        )

        is_querying_today = (clean_target == today_clean) or (target in [today_clean, today_vn])

        # 1. Kiểm tra cờ bộ nhớ nếu đang xem hôm nay
        if is_querying_today:
            if getattr(self, "is_settled_today", False):
                return {
                    "is_settled": True,
                    "settled_date": getattr(self, "last_settled_date", "") or disp_date,
                    "settled_at": getattr(self, "last_settled_at", "") or now.strftime("%H:%M:%S %d/%m/%Y"),
                    "settled_time": getattr(self, "last_settled_time", "") or now.strftime("%H:%M:%S"),
                    "net_profit": getattr(self, "last_settle_result", {}).get("net")
                }
            if getattr(self, "last_settled_date", None):
                return {
                    "is_settled": True,
                    "settled_date": self.last_settled_date,
                    "settled_at": getattr(self, "last_settled_at", ""),
                    "settled_time": getattr(self, "last_settled_time", ""),
                    "net_profit": getattr(self, "last_settle_result", {}).get("net")
                }

        # 2. Kiểm tra file lưu trữ trong daily_history
        archive = self.load_daily_archive(clean_target)
        if archive:
            res = archive.get("settle_result") or {}
            saved_at = archive.get("saved_at", "")
            time_str = saved_at.split(" ")[0] if " " in saved_at else ""
            file_disp = archive.get("display_date") or disp_date
            if file_disp and "-" in file_disp:
                fp = file_disp.split("-")
                if len(fp) == 3 and len(fp[0]) == 4:
                    file_disp = f"{fp[2]}/{fp[1]}/{fp[0]}"
            return {
                "is_settled": True,
                "settled_date": file_disp,
                "settled_at": saved_at,
                "settled_time": time_str,
                "net_profit": res.get("net") if isinstance(res, dict) else None
            }

        # 3. Chưa chốt tiền
        return {
            "is_settled": False,
            "settled_date": disp_date,
            "settled_at": "",
            "settled_time": "",
            "net_profit": None
        }

    def _reset_after_settle(self, date_str: str, settle_result: dict = None, kqxs: dict = None):
        """Lưu trữ dữ liệu ngày cũ và đánh dấu ngày hôm nay đã chốt tiền thành công.
        Theo đúng kế hoạch:
        1. Các con số thống kê và tin cược trong ngày vẫn được GIỮ NGUYÊN trên màn hình suốt buổi tối
           để chủ bảng xem lại, kiểm tra lãi/lỗ và đối soát thoải mái.
        2. Toàn bộ số liệu sẽ tự động reset về 0 khi đồng hồ điểm 12h đêm (00:00:00) chuyển sang ngày hôm sau.
        3. Nếu sau khi chốt tiền mà có khách gửi tin cược mới (chuẩn bị ngày mai), hệ thống tự động bắt đầu tính từ Tin #1 cho ngày mới.
        """
        # 1. Lưu trữ an toàn dữ liệu ngày vừa chốt vào kho daily_history
        self.save_daily_archive(date_str, settle_result=settle_result, kqxs=kqxs)

        # 2. Đặt cờ ngày đã chốt
        today_str = now_vn().strftime("%Y-%m-%d")
        self.last_daily_report_date = today_str  # Ngăn check_daily_schedule() chốt lại
        self.is_settled_today = True
        self.last_settled_date = date_str or now_vn().strftime("%d/%m/%Y")
        self.last_settled_at = now_vn().strftime("%H:%M:%S %d/%m/%Y")
        self.last_settled_time = now_vn().strftime("%H:%M:%S")
        self.last_settle_result = settle_result or {}

        self.log(f"🎯 Đã chốt tiền thành công ngày {date_str}. Dữ liệu được bảo lưu và giữ nguyên trên màn hình đối soát đến 12h đêm.", "SUCCESS")

    def check_and_rollover_date(self) -> bool:
        """Tự động kiểm tra chuyển sang ngày mới khi đồng hồ điểm 12h đêm (00:00:00):
        1. Đưa tất cả các con số thống kê về 0.
        2. Làm sạch bảng cược và bộ đếm tin nhắn khách để đón ngày mới.
        3. Dữ liệu ngày hôm qua được lưu trữ an toàn trong kho lịch sử, xem lại qua nút [⏮️ Hôm trước].
        """
        now = now_vn()
        today_str = now.strftime("%Y-%m-%d")

        if not hasattr(self, "current_date") or not self.current_date:
            self.current_date = today_str
            return False

        if self.current_date != today_str:
            old_date = self.current_date
            self.log(f"⏰ [12H ĐÊM - SANG NGÀY MỚI {today_str}] Tự động reset toàn bộ các con số thống kê về 0...", "INFO")

            # Lưu archive nếu ngày cũ còn dữ liệu chưa lưu
            has_old_data = bool(self.client_bets or (hasattr(self, "balancer") and self.balancer.step_count > 0) or getattr(self, "pending_bets", []))
            if has_old_data and not getattr(self, "is_settled_today", False):
                self.save_daily_archive(old_date)

            # Reset sạch sẽ đón ngày mới
            self.balancer.reset_board()
            self.client_bets = {}
            self.save_client_bets()
            self.client_msg_counters = {}
            self.pending_bets = []
            self.save_pending_bets()
            self.pending_client_receipts = []
            self.last_web_bet_hash = None
            self.last_web_bet_text = ""
            self.last_bet_timestamp = None
            self.cached_kqxs = None
            self.is_settled_today = False
            self.last_daily_report_date = None  # Cho phép ngày mới được chốt tiền

            self.current_date = today_str
            self.log(f"✅ ĐÃ ĐƯA TẤT CẢ VỀ 0 CHO NGÀY MỚI {today_str}. Sẵn sàng nhận tin cược mới bắt đầu từ Tin #1!", "SUCCESS")

            # Tự động dọn dẹp các file lịch sử cược quá hạn lưu trữ
            self.cleanup_old_history()
            return True

        return False

    def check_daily_schedule(self):
        """Kiểm tra thời gian và tự động chốt tiền từ 18h35, nếu chưa có KQXS thì lặp lại mỗi 5 phút/lần cho đến khi chốt thành công và đưa tất cả về 0.
        Nếu đã chốt thủ công bằng tay thì dừng hoàn toàn việc chốt tiền của ngày hôm đó.
        """
        if not self.config.get("auto_fetch_kqxs_daily", True):
            return

        now = now_vn()
        today_str = now.strftime("%Y-%m-%d")

        # NẾU ĐÃ CHỐT TIỀN HÔM NAY RỒI (kể cả tự chốt bằng tay thủ công hay tự động) THÌ DỪNG!
        if getattr(self, "last_daily_report_date", "") == today_str:
            return

        # BẮT ĐẦU TỪ LÚC 18h35 TRỞ ĐI (18:35 đến 23:59):
        is_settle_time = (now.hour == 18 and now.minute >= 35) or (now.hour > 18)
        if not is_settle_time:
            return

        # CỨ 5 PHÚT / LẦN (300 giây) THỰC HIỆN LẤY KẾT QUẢ VÀ CHỐT TIỀN:
        last_check = getattr(self, "_last_kqxs_check_ts", 0)
        if time.time() - last_check < 300:
            return
        self._last_kqxs_check_ts = time.time()

        self.log(f"⏰ [18h35+] Đến giờ chốt tiền tự động ({now.strftime('%H:%M:%S')}) -> Đang kiểm tra KQXS Miền Bắc...", "INFO")
        try:
            kq = fetch_xsmb()
            # Điều kiện KQXS hợp lệ: đài báo đầy đủ (is_complete) hoặc đã có giải đặc biệt và ít nhất 20 giải
            prizes_count = len(kq.get("all_last2", [])) if kq.get("all_last2") else 0
            has_db = bool(kq.get("special_last2"))
            is_valid_kqxs = kq.get("is_complete") or (has_db and prizes_count >= 20)

            if is_valid_kqxs:
                self.cached_kqxs = kq
                date_label = kq.get('date') or today_str
                self.log(f"🎯 ĐÃ CÓ ĐẦY ĐỦ KQXS NGÀY {date_label}! Tiến hành tự động chốt tiền với khách & chủ thầu...", "SUCCESS")
                res = self.settle_all(kq, notify_clients=True, notify_recipient=True, notify_owner=True)
                self._reset_after_settle(date_label, settle_result=res, kqxs=kq)
                self.log(f"✅ ĐÃ TỰ ĐỘNG CHỐT TIỀN THÀNH CÔNG VÀ ĐƯA TẤT CẢ THỐNG KÊ VỀ 0 CHO NGÀY TIẾP THEO.", "SUCCESS")
            else:
                self.log(f"⏳ Chưa có đầy đủ KQXS 27 giải ngày {today_str} (đài chưa quay xong). Sẽ tiếp tục kiểm tra lại sau 5 phút...", "WARN")
        except Exception as ex:
            self.log(f"⚠️ Lỗi trong quá trình tự động chốt tiền: {ex}. Sẽ thử lại sau 5 phút...", "WARN")

    def poll_updates(self):
        """Vòng lặp Long Polling nhận tin nhắn liên tục"""
        self.log("Bot Telegram bắt đầu lắng nghe tin cược...")
        token = self.config.get("bot_token", "").strip()
        if not token:
            self.log("Chưa cài đặt Bot Token!", "ERROR")
            return

        # Tự động gỡ webhook (nếu có) để tránh xung đột getUpdates 409
        try:
            requests.post(f"https://api.telegram.org/bot{token}/deleteWebhook", json={"drop_pending_updates": False}, timeout=8)
        except Exception:
            pass

        while self.is_running:
            try:
                # Tự động rà soát và xóa dấu vết cược sau 24h (chạy mỗi 60 giây)
                now_ts = time.time()
                if now_ts - getattr(self, "last_cleanup_ts", 0) >= 60:
                    self.last_cleanup_ts = now_ts
                    try:
                        self.check_and_cleanup_traces()
                    except Exception as ex:
                        self.log(f"Lỗi tự động xóa dấu vết cược: {ex}", "WARN")

                # Tự động kiểm tra chuyển ngày mới qua nửa đêm (00:00)
                try:
                    self.check_and_rollover_date()
                except Exception as ex:
                    pass

                # Tự động kiểm tra lịch KQXS 18h35
                try:
                    self.check_daily_schedule()
                except Exception as ex:
                    pass

                # Tự động kiểm tra timeout 3 phút người nhận cược thừa chưa phản hồi (nếu bật Check phản hồi)
                if self.config.get("check_recipient_ack", True) and self.pending_recipient_acks and not self.pending_recipient_acks.get("alerted"):
                    if time.time() - self.pending_recipient_acks["timestamp"] >= 180:
                        self.pending_recipient_acks["alerted"] = True
                        rec_name = self.pending_recipient_acks.get("recipient", "")
                        owner_cid = self.config.get("owner_chat_id")
                        if owner_cid:
                            self.send_telegram_message(str(owner_cid), "Chủ thầu chưa Ok lại")
                        self.log(f"Chủ thầu {rec_name} chưa Ok lại sau 3 phút", "WARN")

                url = f"https://api.telegram.org/bot{token}/getUpdates"
                params = {
                    "offset": self.last_update_id + 1,
                    "timeout": 15
                }
                resp = requests.get(url, params=params, timeout=20)
                data = resp.json()

                if data.get("ok"):
                    for update in data.get("result", []):
                        self.last_update_id = update.get("update_id", self.last_update_id)
                        message = update.get("message") or update.get("channel_post") or update.get("edited_message")
                        if message:
                            self.handle_incoming_message(message)
                else:
                    err = data.get("description", "")
                    if "Conflict" in err:
                        now_c = time.time()
                        if now_c - getattr(self, "_last_conflict_log_ts", 0) > 60:
                            self._last_conflict_log_ts = now_c
                            self.log("Xung đột getUpdates: Có tiến trình bot khác đang chạy trùng lặp.", "WARN")
                        time.sleep(10)
                    else:
                        self.log(f"Lỗi getUpdates: {err}", "WARN")
                        time.sleep(3)

            except requests.exceptions.Timeout:
                # Timeout bình thường của Long Polling
                continue
            except Exception as e:
                self.log(f"Lỗi kết nối polling: {e}", "ERROR")
                time.sleep(3)

        self.log("Bot Telegram đã dừng lắng nghe.")

    def start(self) -> dict:
        """Bật bot chạy chế độ tự động (auto)"""
        self.config["bot_mode"] = "auto"
        self.save_config()
        self.log("Bot đã BẬT (Chế độ Tự Động): Tự động nhận cược, cân chuyển và Ok lại cho khách.", "SUCCESS")

        if not self.is_running:
            check = self.check_bot_token()
            if not check.get("valid"):
                return {"status": "error", "message": check.get("error")}
            self.is_running = True
            self.polling_thread = threading.Thread(target=self.poll_updates, daemon=True)
            self.polling_thread.start()
            return {"status": "started", "bot_mode": "auto", "running": True, "info": check.get("info")}

        return {"status": "started", "bot_mode": "auto", "running": True}

    def stop(self) -> dict:
        """
        Chuyển sang trạng thái TẮT (Treo chờ duyệt):
        - Bot vẫn duy trì nhận tin nhắn để quản lý và hiển thị cho Chủ xem
        - KHÔNG tự động Ok lại cho khách
        - Các tin cược sẽ ở trạng thái Treo và báo tin về cho Chủ bot
        """
        self.config["bot_mode"] = "manual"
        self.save_config()
        self.log("Bot đã TẮT (Chế độ Treo chờ duyệt): Vẫn quản lý tin nhắn nhưng không Ok khách, chờ Chủ bot duyệt.", "WARN")

        # Đảm bảo luồng lắng nghe tin nhắn vẫn chạy để nhận tin nhắn của khách và chủ thầu
        if not self.is_running and self.config.get("bot_token"):
            check = self.check_bot_token()
            if check.get("valid"):
                self.is_running = True
                self.polling_thread = threading.Thread(target=self.poll_updates, daemon=True)
                self.polling_thread.start()

        return {"status": "stopped", "bot_mode": "manual", "running": self.is_running}

    def stop_polling(self) -> dict:
        """Ngắt kết nối hoàn toàn khỏi Telegram (dừng luồng polling)"""
        self.is_running = False
        if self.polling_thread and self.polling_thread.is_alive():
            self.polling_thread.join(timeout=2)
        self.log("Đã ngắt kết nối hoàn toàn khỏi Telegram Bot API.", "INFO")
        return {"status": "offline", "running": False}


# Singleton instance
bot_service = TelegramBotService()
# Chỉ tự động khởi chạy bot nếu có cờ AUTO_START_BOT=true (ví dụ chạy trên Render / Docker)
if os.environ.get("AUTO_START_BOT", "").lower() in ["true", "1"]:
    if bot_service.config.get("bot_token"):
        try:
            bot_service.start()
        except Exception:
            pass
